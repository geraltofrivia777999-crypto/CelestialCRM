"""Публикация залива в Meta — ТЗ 3.3 и 3.4.

Публикация разбита на этапы: кампания → группа объявлений → объявления. ID
каждого созданного объекта сразу записывается в залив, поэтому повторный запуск
после сбоя продолжает с места обрыва, а не создаёт вторую кампанию. Meta ключей
идемпотентности не поддерживает — эта запись и есть единственная защита.

Всё создаётся на паузе. Снятие с паузы — отдельный, последний шаг: именно в этот
момент начинают тратиться деньги, и он должен быть виден в журнале отдельно.
"""

import logging
import random
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import decrypt_secret
from app.models import (
    IntegrationConnection,
    LaunchStatus,
    MetaAdAccount,
    MetaCreative,
    MetaLaunch,
    MetaLaunchCreative,
    MetaOperation,
    MetaRule,
    MetaTemplate,
    Offer,
)
from app.services.meta import (
    MetaClient,
    MetaError,
    build_asset_feed_spec,
    build_object_story_spec,
    build_targeting,
    campaign_id_macro_url,
)
from app.services.meta_bundle import (
    ATTRIBUTION_WINDOWS,
    DEFAULT_ATTRIBUTION,
    render_macros,
    spin,
)
from app.services.meta_session import get_session_manager, open_session_access

logger = logging.getLogger(__name__)

ClientFactory = Callable[..., MetaClient]

# Цели, у которых Meta принимает окно атрибуции. У остальных его нет как
# понятия: у охвата не бывает «конверсии в течение семи дней».
ATTRIBUTION_GOALS = {
    "OFFSITE_CONVERSIONS",
    "LINK_CLICKS",
    "LANDING_PAGE_VIEWS",
    "LEAD_GENERATION",
    "VALUE",
}


class LaunchValidationError(ValueError):
    """Залив нельзя публиковать. Проверяется до первого обращения к Meta."""


class MetaLaunchPublisher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client_factory: ClientFactory = MetaClient,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory

    async def publish(self, launch_id: str, user_id: str | None = None) -> dict:
        launch_uuid = uuid.UUID(launch_id)
        actor = uuid.UUID(user_id) if user_id else None

        async with self.session_factory() as db:
            context = await load_launch_context(db, launch_uuid)
            if not context:
                return {"status": "missing"}
            validate_launch(context)
            launch = context["launch"]
            launch.status = LaunchStatus.publishing
            launch.last_error = None
            await db.commit()

        access: dict = {"transport": None, "owned": False, "token": None}
        try:
            access = await _launch_session_access(context, self.session_factory)
            client = self.client_factory(
                access["token"] or context["access_token"],
                proxy=context.get("proxy_url"),
                user_agent=context.get("user_agent"),
                transport=access["transport"],
            )
            # Числовые ID локалей (для правил мультиязычного креатива) резолвим
            # ДО создания кампании: сбой здесь не должен оставить осиротевший
            # объект в кабинете.
            context["locale_ids"] = await self._resolve_locale_ids(client, context["launch"])
            groups = max(1, int(launch.campaign_count or 1))
            campaign_ids: list[str] = []
            all_adsets: list[str] = []
            all_ads: list[str] = []
            first_adset_ids: list[str] = []
            first_ads: list[str] = []
            for group in range(groups):
                campaign_id = await self._ensure_campaign(
                    launch_uuid, context, client, actor,
                    group=group, groups=groups, number=group + 1,
                )
                adset_ids = await self._ensure_adsets(
                    launch_uuid, context, client, campaign_id, actor,
                    group=group, groups=groups,
                )
                ads = await self._ensure_ads(
                    launch_uuid, context, client, adset_ids, actor,
                    group=group, groups=groups,
                )
                campaign_ids.append(campaign_id)
                all_adsets.extend(adset_ids)
                all_ads.extend(ads)
                if group == 0:
                    first_adset_ids = list(adset_ids)
                    first_ads = list(ads)
            # Теги (adlabels) на объектах залива — после создания всех уровней.
            await self._apply_tags(client, context, campaign_ids, all_adsets, all_ads)
            activated = await self._activate(
                launch_uuid, context, client, campaign_ids, all_adsets, all_ads, actor
            )
        except Exception as exc:
            async with self.session_factory() as db:
                launch = await db.get(MetaLaunch, launch_uuid)
                if launch:
                    launch.status = LaunchStatus.failed
                    launch.last_error = _safe_error(exc)
                    await db.commit()
            raise
        finally:
            if access["owned"]:
                # Браузер, восстановленный ради публикации, закрываем.
                try:
                    await get_session_manager().close(str(context["connection_id"]))
                except Exception:  # noqa: BLE001 — очистка не маскирует результат
                    pass

        async with self.session_factory() as db:
            launch = await db.get(MetaLaunch, launch_uuid)
            if not launch:
                return {"status": "missing"}
            launch.status = LaunchStatus.active if activated else LaunchStatus.paused
            launch.published_at = datetime.now(UTC)
            launch.last_error = None
            await db.commit()

        return {
            "status": "active" if activated else "paused",
            "campaign_id": campaign_ids[0],
            "campaign_ids": campaign_ids,
            "adset_id": first_adset_ids[0],
            "adset_ids": first_adset_ids,
            "ads": first_ads,
        }

    async def _resolve_locale_ids(
        self, client: MetaClient, launch: MetaLaunch
    ) -> dict[str, int]:
        """Локаль → числовой ID словаря adlocale для правил мультиязычности.

        Meta принимает `customization_spec.locales` только как числовые ключи
        (`en_US` отбивает кодом 100), а ключи стабильно живут в словаре
        `/search?type=adlocale`. Спрашиваем его тем же живым токеном, которым
        создаётся кампания, и сопоставляем по человеческому имени языка,
        выбранному на форме (оно надёжнее кода: у поиска нет поля «код»).
        """
        named: dict[str, str] = {}
        for item in launch.ads or []:
            for text in item.get("texts") or []:
                language = str(text.get("language") or "").strip()
                if not language:
                    continue
                named.setdefault(
                    language, str(text.get("language_name") or "").strip()
                )
        resolved: dict[str, int] = {}
        for language, name in named.items():
            resolved[language] = await _locale_id_by_name(client, language, name)
        return resolved

    async def _ensure_campaign(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        client: MetaClient,
        actor: uuid.UUID | None,
        *,
        group: int = 0,
        groups: int = 1,
        number: int = 1,
    ) -> str:
        launch = context["launch"]
        state = launch.external_payload or {}
        if groups == 1:
            if launch.campaign_external_id:
                return launch.campaign_external_id
        else:
            stored = (state.get("campaign_groups") or {}).get(str(group), {}).get(
                "campaign_id"
            )
            if stored:
                return str(stored)
        template = context["template"]
        campaign = _block(template, "campaign")
        # Кастомный нейминг кабинета сильнее шаблона связки: это те же макросы,
        # но заданные под конкретный залив.
        pattern = launch.campaign_name or campaign.get("campaign_name")
        name = _name(pattern, context, {"campaign.number": number}) or launch.name
        budgets = _campaign_budgets(launch, template)
        # Расширенный режим: цель и стратегия ставок поверх связки.
        objective = launch.objective or template.objective
        bid_strategy = launch.bid_strategy or (
            "LOWEST_COST_WITHOUT_CAP"
            if campaign.get("advantage")
            else template.bid_strategy
        )
        request = {
            "name": name,
            "objective": objective,
            "status": "PAUSED",
            **{key: str(value) for key, value in budgets.items() if value is not None},
        }
        response = await self._call(
            launch_uuid,
            context,
            actor,
            "campaign_create",
            request,
            lambda: client.create_campaign(
                context["account"].external_id,
                name=name,
                objective=objective,
                spend_cap=launch.spend_limit,
                special_ad_categories=[
                    str(code) for code in (campaign.get("special_ad_categories") or [])
                ],
                # Advantage+ означает «настройки за Meta»: минимальная цена и
                # бюджет на кампании. Ручную стратегию в этом режиме она
                # отбивает, поэтому подменяем её здесь, а не молчим.
                bid_strategy=bid_strategy,
                currency=context["account"].currency,
                **budgets,
            ),
        )
        campaign_id = str(response.get("id") or "")
        if not campaign_id:
            raise MetaError("Meta не вернула ID созданной кампании")
        async with self.session_factory() as db:
            stored_launch = await db.get(MetaLaunch, launch_uuid)
            if stored_launch:
                if groups == 1:
                    stored_launch.campaign_external_id = campaign_id
                else:
                    payload = dict(stored_launch.external_payload or {})
                    groups_state = dict(payload.get("campaign_groups") or {})
                    slot = dict(groups_state.get(str(group)) or {})
                    slot["campaign_id"] = campaign_id
                    groups_state[str(group)] = slot
                    payload["campaign_groups"] = groups_state
                    payload["campaign_ids"] = [
                        item.get("campaign_id")
                        for _, item in sorted(
                            groups_state.items(), key=lambda pair: int(pair[0])
                        )
                        if item.get("campaign_id")
                    ]
                    stored_launch.external_payload = payload
                    if group == 0:
                        stored_launch.campaign_external_id = campaign_id
                await db.commit()
        launch.campaign_external_id = campaign_id
        return campaign_id

    async def _ensure_adsets(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        client: MetaClient,
        campaign_id: str,
        actor: uuid.UUID | None,
        *,
        group: int = 0,
        groups: int = 1,
    ) -> list[str]:
        """Копии адсета внутри кампании.

        Одинаковые группы с одним таргетом — обычный приём: Meta учится на
        каждой отдельно, и одна из них обычно раскачивается лучше прочих.
        """
        launch = context["launch"]
        payload = launch.external_payload or {}
        if groups == 1:
            stored_ids = list(payload.get("adset_ids") or [])
            if launch.adset_external_id and not stored_ids:
                stored_ids = [launch.adset_external_id]
        else:
            stored_ids = list(
                (payload.get("campaign_groups") or {}).get(str(group), {}).get(
                    "adset_ids"
                )
                or []
            )
        created = list(stored_ids)
        for index in range(len(created), max(1, int(launch.adset_count or 1))):
            created.append(
                await self._create_adset(
                    launch_uuid, context, client, campaign_id, actor, index
                )
            )
            if groups == 1:
                await self._store_payload(launch_uuid, {"adset_ids": created})
            else:
                await self._store_group_payload(launch_uuid, group, "adset_ids", created)
        launch.adset_external_id = created[0]
        return created

    async def _create_adset(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        client: MetaClient,
        campaign_id: str,
        actor: uuid.UUID | None,
        index: int,
    ) -> str:
        launch = context["launch"]
        template = context["template"]
        campaign = _block(template, "campaign")
        adset = _block(template, "adset")
        targeting = build_targeting(template)
        # Instagram показывается только когда страница связана с IG-аккаунтом:
        # без этой связи Meta отбивает объявление «Выберите IG-аккаунт или
        # Page». Кабинет без IG никуда не девается — реклама идёт в Facebook.
        await self._drop_unlinked_instagram(launch, template, client, targeting)
        if adset.get("age_randomize"):
            targeting.update(
                _randomized_age(
                    template, str(launch.id), int(adset.get("age_randomize_years") or 3)
                )
            )
        promoted_object = _promoted_object(launch, template)
        name = (
            _name(adset.get("adset_name"), context, {"adset.number": index + 1})
            or f"{launch.name} — adset {index + 1}"
        )
        budgets = _adset_budgets(launch, template)
        goal = template.optimization_goal
        if goal in {"LINK_CLICKS", "LANDING_PAGE_VIEWS"}:
            # Кликовые цели Meta считает только по кликам, и окно просмотров у
            # них одно: (клики=1, просмотры=0). Любая другая комбинация (в т.ч.
            # из связки «7d_click_1d_view») отбивается кодом 100/1885501.
            attribution = list(ATTRIBUTION_WINDOWS["1d_click"]["spec"])
        elif goal in ATTRIBUTION_GOALS:
            attribution = list(
                ATTRIBUTION_WINDOWS.get(
                    str(launch.attribution or adset.get("attribution") or DEFAULT_ATTRIBUTION),
                    ATTRIBUTION_WINDOWS[DEFAULT_ATTRIBUTION],
                )["spec"]
            )
            engaged_view = str(
                launch.engaged_view if launch.engaged_view is not None
                else adset.get("engaged_view") or "none"
            )
            if engaged_view != "none":
                # Досмотр видео — отдельное окно рядом с кликом и просмотром: без
                # него конверсии с видео Meta не засчитает.
                attribution.append(
                    {
                        "event_type": "ENGAGED_VIDEO_VIEW",
                        "window_days": 7 if engaged_view == "7d" else 1,
                    }
                )
        else:
            # Охват и лайки страницы окна атрибуции не имеют — Meta отбивает
            # поле как недопустимое для этой оптимизации.
            attribution = []
        request = {
            "campaign_id": campaign_id,
            "optimization_goal": template.optimization_goal,
            "targeting": targeting,
            **{key: str(value) for key, value in budgets.items() if value is not None},
        }
        # DSA-прозрачность: для ЕС Meta требует бенефициара и платильщика.
        # Мастер берёт одно значение из подсказок кабинета и проставляет оба.
        beneficiary = (launch.beneficiary or "").strip() or None
        if beneficiary:
            request["dsa_beneficiary"] = beneficiary
            request["dsa_payor"] = beneficiary
        response = await self._call(
            launch_uuid,
            context,
            actor,
            "adset_create",
            request,
            lambda: client.create_adset(
                context["account"].external_id,
                name=name,
                campaign_id=campaign_id,
                targeting=targeting,
                billing_event=template.billing_event,
                optimization_goal=template.optimization_goal,
                bid_strategy=launch.bid_strategy or template.bid_strategy,
                bid_amount=_money(campaign.get("bid_amount")),
                accelerated=bool(campaign.get("accelerated_delivery")),
                spend_cap=(
                    launch.adset_budget_limit
                    if launch.adset_budget_limit is not None
                    else _money(campaign.get("adset_budget_limit"))
                ),
                budget_min=launch.budget_limit_min,
                budget_max=launch.budget_limit_max,
                start_time=_start_time(launch),
                end_time=_schedule_time(launch.end_date),
                promoted_object=promoted_object,
                attribution_spec=attribution,
                currency=context["account"].currency,
                dsa_beneficiary=beneficiary,
                dsa_payor=beneficiary,
                **budgets,
            ),
        )
        adset_id = str(response.get("id") or "")
        if not adset_id:
            raise MetaError("Meta не вернула ID созданной группы объявлений")
        # ID пишем немедленно — до следующего шага: при падении воркера между
        # ответом Meta и записью в БД повторная доставка создала бы дубль.
        async with self.session_factory() as db:
            stored = await db.get(MetaLaunch, launch_uuid)
            if stored:
                if index == 0:
                    stored.adset_external_id = adset_id
                payload = dict(stored.external_payload or {})
                ids = list(payload.get("adset_ids") or [])
                if not ids and stored.adset_external_id:
                    ids = [stored.adset_external_id]
                while len(ids) <= index:
                    ids.append("")
                ids[index] = adset_id
                payload["adset_ids"] = ids
                stored.external_payload = payload
                await db.commit()
        return adset_id

    async def _ensure_ads(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        client: MetaClient,
        adset_ids: list[str],
        actor: uuid.UUID | None,
        *,
        group: int = 0,
        groups: int = 1,
    ) -> list[str]:
        """Объявления во всех копиях адсета.

        Что именно создавать, решает план: если у залива заданы объявления со
        своими текстами и языками — по объявлению на запись плана, иначе, как
        раньше, по объявлению на креатив.

        Созданные ID лежат в заливе ключом «адсет:объявление»: повтор после
        сбоя продолжает с места обрыва и не задваивает то, что уже создано.
        """
        launch = context["launch"]
        account = context["account"]
        template = context["template"]
        ad_block = _block(template, "ad")
        page_id = launch.page_id or template.page_id or ""
        plan = _ad_plan(launch, context, ad_block)
        state = dict((launch.external_payload or {}).get("ads") or {})
        if groups > 1:
            state = dict(
                (launch.external_payload or {})
                .get("campaign_groups", {})
                .get(str(group), {})
                .get("ads")
                or {}
            )
        created: list[str] = []

        def persist():
            if groups == 1:
                return self._store_payload(launch_uuid, {"ads": state})
            return self._store_group_payload(launch_uuid, group, "ads", state)

        for adset_index, adset_id in enumerate(adset_ids):
            for ad_index, item in enumerate(plan, start=1):
                key = f"{adset_index}:{ad_index}"
                if state.get(key):
                    created.append(state[key])
                    continue
                creative_key = f"creative:{ad_index}"
                creative_external_id = state.get(creative_key)
                if not creative_external_id:
                    creative_external_id = await self._create_ad_creative(
                        launch_uuid, context, client, actor, item, page_id, ad_index
                    )
                    state[creative_key] = creative_external_id
                    await persist()
                ad_name = (
                    _name(
                        ad_block.get("ad_name"),
                        context,
                        {
                            "ad.number": ad_index,
                            "adset.number": adset_index + 1,
                            "adset.id": adset_id,
                            "creative.name": item["label"],
                        },
                    )
                    or f"{launch.name} — {ad_index}"
                )
                ad_response = await self._call(
                    launch_uuid,
                    context,
                    actor,
                    "ad_create",
                    {
                        "adset_id": adset_id,
                        "creative_id": creative_external_id,
                        "name": ad_name,
                    },
                    lambda cid=creative_external_id, ad_name=ad_name, adset_id=adset_id: (
                        client.create_ad(
                            account.external_id,
                            name=ad_name[:100],
                            adset_id=adset_id,
                            creative_id=cid,
                        )
                    ),
                )
                ad_id = str(ad_response.get("id") or "")
                if not ad_id:
                    raise MetaError("Meta не вернула ID созданного объявления")
                state[key] = ad_id
                await persist()
                if adset_index == 0 and item.get("link_id"):
                    # Первый адсет заполняет привязку креатива: по ней в CRM
                    # видно, какое объявление получилось из какого файла.
                    await self._store_link(
                        item["link_id"],
                        ad_external_id=ad_id,
                        creative_external_id=creative_external_id,
                    )
                created.append(ad_id)
        return created

    async def _drop_unlinked_instagram(
        self,
        launch: MetaLaunch,
        template: MetaTemplate,
        client: MetaClient,
        targeting: dict,
    ) -> None:
        """Убрать Instagram без связи страницы с IG (иначе код 100).

        `build_targeting` не задаёт платформы — Meta включает «автоматические»
        плейсменты, а в их числе Instagram: для кабинета без связанного
        IG-профиля она отбивает «Выберите IG-аккаунт или Page». Связи нет —
        выключаем авто и явно оставляем Facebook и Messenger.
        """
        page_id = launch.page_id or template.page_id
        if not page_id:
            return
        try:
            page = await client.object_fields(
                str(page_id), ["id", "instagram_business_account"]
            )
        except MetaError:
            # Страница недоступна — не трогаем: об ошибке расскажет Meta.
            return
        if (page or {}).get("instagram_business_account"):
            return
        platforms = [
            str(platform).lower()
            for platform in (targeting.get("publisher_platforms") or [])
        ]
        keep = [platform for platform in platforms
                if platform not in ("instagram", "instagram_stories")]
        if any(item in keep for item in ("facebook", "messenger")) or keep:
            targeting["publisher_platforms"] = keep
        else:
            # Авто-плейсменты или чистый Instagram: фиксируемся на Facebook.
            targeting["publisher_platforms"] = ["facebook", "messenger"]
        logger.info(
            "Instagram недоступен (страница без связи с IG) — платформы: %s",
            targeting["publisher_platforms"],
        )

    async def _create_ad_creative(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        client: MetaClient,
        actor: uuid.UUID | None,
        item: dict,
        page_id: str,
        ad_index: int,
    ) -> str:
        """Креатив объявления: обычный или мультиязычный.

        Языков много — Meta показывает зрителю текст на его языке сама, и это
        по-прежнему одно объявление: по объявлению на язык делило бы бюджет и
        учило каждое отдельно.
        """
        launch = context["launch"]
        account = context["account"]
        template = context["template"]
        ad_block = _block(template, "ad")
        sub_id = context["attribution_sub_id"]
        texts = [
            {**text, "link_url": campaign_id_macro_url(str(text.get("link_url") or ""), sub_id)}
            for text in item["texts"]
        ]
        name = f"{launch.name} — {item['label']}"[:100]
        cta = str(texts[0].get("call_to_action") or launch.call_to_action or "LEARN_MORE")

        if len(texts) > 1:
            spec = build_asset_feed_spec(
                texts,
                item["creatives"],
                cta=cta,
                locale_ids=context.get("locale_ids") or {},
            )
            request = {"name": name, "asset_feed_spec": spec}
            action = lambda: client.create_ad_creative(  # noqa: E731 — замыкание для журнала
                account.external_id,
                name=name,
                asset_feed_spec=spec,
                page_id=page_id,
                url_tags=launch.url_tags,
                advantage_creative=bool(ad_block.get("advantage_creative")),
                multi_advertiser=bool(ad_block.get("multi_advertiser")),
            )
        else:
            proxy = _launch_with_link(launch, str(texts[0].get("link_url") or ""), texts[0])
            spec = build_object_story_spec(
                proxy, item["creatives"][0], page_id=page_id, caption=launch.display_link
            )
            request = {"name": name, "object_story_spec": spec}
            action = lambda: client.create_ad_creative(  # noqa: E731
                account.external_id,
                name=name,
                object_story_spec=spec,
                url_tags=launch.url_tags,
                advantage_creative=bool(ad_block.get("advantage_creative")),
                multi_advertiser=bool(ad_block.get("multi_advertiser")),
            )

        response = await self._call(
            launch_uuid, context, actor, "creative_create", request, action
        )
        creative_id = str(response.get("id") or "")
        if not creative_id:
            raise MetaError("Meta не вернула ID креатива объявления")
        return creative_id

    async def _activate(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        client: MetaClient,
        campaign_ids: list[str],
        adset_ids: list[str],
        ads: list[str],
        actor: uuid.UUID | None,
    ) -> bool:
        """Снять с паузы то, что просили снять.

        Всё создаётся на паузе, и снятие — отдельный шаг с отдельной записью в
        журнале: именно в этот момент начинают тратиться деньги. Уровни
        снимаются снизу вверх — объявления, адсет, кампания: если оборвётся на
        середине, крутиться не начнёт ничего, потому что верхний уровень всё
        ещё на паузе.
        """
        launch = context["launch"]
        if not launch.activate_on_publish:
            return False
        levels: list[tuple[str, str, str]] = []
        if not launch.pause_ads:
            levels.extend(("ad_activate", ad_id, ad_id) for ad_id in ads)
        if not launch.pause_adsets:
            levels.extend(("adset_activate", value, value) for value in adset_ids)
        if not launch.pause_campaigns:
            levels.extend(
                ("campaign_activate", value, value) for value in campaign_ids
            )
        for kind, target, external_id in levels:
            await self._call(
                launch_uuid,
                context,
                actor,
                kind,
                {"status": "ACTIVE"},
                lambda external_id=external_id: client.set_status(external_id, "ACTIVE"),
                target=target,
            )
        return not launch.pause_campaigns

    async def _call(
        self,
        launch_uuid: uuid.UUID,
        context: dict,
        actor: uuid.UUID | None,
        kind: str,
        request: dict,
        action: Callable,
        *,
        target: str | None = None,
    ) -> dict:
        """Записать операцию до вызова, дописать результат после.

        Порядок именно такой: если процесс упадёт между запросом и ответом, в
        журнале останется след «отправляли, чем кончилось — неизвестно», и это
        честнее, чем отсутствие записи.
        """
        operation_id = await self._open_operation(
            launch_uuid, context["workspace_id"], actor, kind, request, target
        )
        try:
            response = await action()
        except Exception as exc:
            await self._close_operation(operation_id, "failed", {}, _safe_error(exc))
            raise
        await self._close_operation(operation_id, "success", response, None)
        return response

    async def _open_operation(
        self,
        launch_uuid: uuid.UUID,
        workspace_id: uuid.UUID,
        actor: uuid.UUID | None,
        kind: str,
        request: dict,
        target: str | None,
    ) -> uuid.UUID:
        async with self.session_factory() as db:
            operation = MetaOperation(
                workspace_id=workspace_id,
                launch_id=launch_uuid,
                kind=kind,
                target_external_id=target,
                status="pending",
                request=_jsonable(request),
                created_by_id=actor,
            )
            db.add(operation)
            await db.commit()
            return operation.id

    async def _close_operation(
        self,
        operation_id: uuid.UUID,
        status: str,
        response: dict,
        error: str | None,
    ) -> None:
        async with self.session_factory() as db:
            operation = await db.get(MetaOperation, operation_id)
            if not operation:
                return
            operation.status = status
            operation.response = _jsonable(response)
            operation.error = error
            if status == "success" and isinstance(response, dict) and response.get("id"):
                operation.target_external_id = str(response["id"])
            await db.commit()

    async def _store_payload(self, launch_uuid: uuid.UUID, changes: dict) -> None:
        """Дописать в `external_payload` то, что уже создано в кабинете."""
        async with self.session_factory() as db:
            launch = await db.get(MetaLaunch, launch_uuid)
            if not launch:
                return
            payload = dict(launch.external_payload or {})
            payload.update(changes)
            launch.external_payload = payload
            await db.commit()

    async def _store_group_payload(
        self, launch_uuid: uuid.UUID, group: int, key: str, value
    ) -> None:
        """Дописать в `external_payload.campaign_groups[group]` созданное."""
        async with self.session_factory() as db:
            launch = await db.get(MetaLaunch, launch_uuid)
            if not launch:
                return
            payload = dict(launch.external_payload or {})
            groups_state = dict(payload.get("campaign_groups") or {})
            slot = dict(groups_state.get(str(group)) or {})
            slot[key] = value
            groups_state[str(group)] = slot
            payload["campaign_groups"] = groups_state
            launch.external_payload = payload
            await db.commit()

    async def _apply_tags(
        self,
        client: MetaClient,
        context: dict,
        campaign_ids: list[str],
        adset_ids: list[str],
        ads: list[str],
    ) -> None:
        """Навесить adlabels (теги) на созданные объекты залива.

        Уровень из залива (`tags.level`): campaign|adset|ad; «аккаунты» и
        «кабинеты» вешаются на кампании (у кабинета как такового тегов нет).
        Режим `add` — создать/навесить, `remove` — снять по имени.
        """
        launch = context["launch"]
        tags = launch.tags or {}
        names = [str(name).strip() for name in (tags.get("names") or []) if str(name).strip()]
        if not names:
            return
        level = str(tags.get("level") or "campaign")
        mode = str(tags.get("mode") or "add")
        targets: list[str] = []
        if level in {"campaign", "account"}:
            targets = campaign_ids
        elif level == "adset":
            targets = adset_ids
        elif level == "ad":
            targets = ads
        if not targets:
            return
        account_id = context["account"].external_id
        existing = {
            str(row.get("name") or "").strip(): str(row.get("id") or "")
            for row in await client.list_adlabels(account_id)
        } if mode == "remove" else {}
        for name in names:
            if mode == "remove":
                adlabel_id = existing.get(name)
                if not adlabel_id:
                    continue
                for target in targets:
                    try:
                        await self._call(
                            launch.id, context, None, "adlabel_detach",
                            {"adlabel_id": adlabel_id, "target": target},
                            lambda adlabel_id=adlabel_id, target=target: client.detach_adlabel(
                                target, adlabel_id
                            ),
                        )
                    except MetaError:
                        continue
                continue
            created = await self._call(
                launch.id, context, None, "adlabel_create",
                {"name": name},
                lambda name=name: client.create_adlabel(account_id, name),
            )
            adlabel_id = str(created.get("id") or "")
            if not adlabel_id:
                continue
            for target in targets:
                await self._call(
                    launch.id, context, None, "adlabel_attach",
                    {"adlabel_id": adlabel_id, "target": target},
                    lambda adlabel_id=adlabel_id, target=target: client.attach_adlabel(
                        target, adlabel_id
                    ),
                )

    async def _store_link(self, link_id: uuid.UUID, **changes: str) -> None:
        async with self.session_factory() as db:
            link = await db.get(MetaLaunchCreative, link_id)
            if not link:
                return
            for field, value in changes.items():
                setattr(link, field, value)
            await db.commit()


async def load_launch_context(db: AsyncSession, launch_id: uuid.UUID) -> dict | None:
    """Собрать всё, что нужно для публикации, одним проходом."""
    launch = await db.get(MetaLaunch, launch_id)
    if not launch:
        return None
    account = await db.get(MetaAdAccount, launch.account_id)
    if not account:
        return None
    connection = await db.get(IntegrationConnection, account.connection_id)
    if not connection:
        return None
    template = (
        await db.get(MetaTemplate, launch.template_id) if launch.template_id else None
    ) or _default_template(launch)
    rows = list(
        (
            await db.execute(
                select(MetaLaunchCreative, MetaCreative)
                .join(MetaCreative, MetaCreative.id == MetaLaunchCreative.creative_id)
                .where(MetaLaunchCreative.launch_id == launch_id)
                .order_by(MetaLaunchCreative.position)
            )
        ).all()
    )
    offer_name = (
        await db.scalar(select(Offer.name).where(Offer.id == launch.offer_id))
        if launch.offer_id
        else None
    )
    return {
        "launch": launch,
        "account": account,
        "template": template,
        "connection_name": connection.name,
        "offer_name": offer_name,
        "creatives": [(link, creative) for link, creative in rows],
        "workspace_id": launch.workspace_id,
        "access_token": decrypt_secret(connection.api_key_encrypted),
        "connection_id": connection.id,
        "auth_method": connection.auth_method,
        "proxy_url": connection.proxy_url,
        "user_agent": connection.user_agent,
        "attribution_sub_id": connection.attribution_sub_id,
    }


async def _launch_session_access(context: dict, session_factory) -> dict:
    """Транспорт браузерной сессии для публикации через session-подключение.

    Meta принимает запросы с сессионным EAAB только из браузерного контекста —
    для остальных способов авторизации возвращается пустой словарь, и клиент
    работает как раньше (обычный httpx).
    """
    if context.get("auth_method") != "session":
        return {"transport": None, "owned": False, "token": None}
    access = await open_session_access(
        session_factory,
        str(context["connection_id"]),
        proxy_url=context.get("proxy_url"),
        user_agent=context.get("user_agent"),
    )
    if access.get("token"):
        context["access_token"] = access["token"]
    return access


def validate_launch(context: dict) -> None:
    """Всё, что можно поймать до обращения к Meta, ловим до обращения к Meta."""
    launch = context["launch"]
    template = context["template"]
    problems: list[str] = []

    if launch.status == LaunchStatus.publishing:
        problems.append("Залив уже публикуется")
    if not (launch.link_url or _block(template, "ad").get("link_url")):
        problems.append("Не заполнена ссылка объявления")
    if not (launch.page_id or template.page_id):
        problems.append(
            "Не указан ID страницы Facebook — без неё Meta не примет объявление"
        )
    if not launch.daily_budget or launch.daily_budget <= 0:
        problems.append("Дневной бюджет должен быть больше нуля")
    if not context["creatives"]:
        problems.append("К заливу не привязан ни один креатив")
    if not (template.geo or launch.geo):
        problems.append("Не указано GEO")

    for _, creative in context["creatives"]:
        if creative.account_id != launch.account_id:
            problems.append(f"Креатив «{creative.name}» загружен в другой кабинет")
        elif creative.kind == "video" and not creative.external_id:
            problems.append(f"Видео «{creative.name}» не загружено в кабинет")
        elif creative.kind == "video" and not creative.thumbnail_url:
            # Без превью Meta не соберёт video_data и отобьёт креатив.
            problems.append(f"У видео «{creative.name}» нет превью")
        elif creative.kind != "video" and not creative.external_hash:
            problems.append(f"Картинка «{creative.name}» не загружена в кабинет")

    problems.extend(_goal_problems(launch, template))
    problems.extend(_budget_problems(launch, template))
    problems.extend(_category_problems(template))

    if problems:
        raise LaunchValidationError("; ".join(problems))


def _goal_problems(launch: MetaLaunch, template: MetaTemplate) -> list[str]:
    """Чего Meta потребует под выбранную цель — спрашиваем заранее."""
    goal = template.optimization_goal
    if goal == "OFFSITE_CONVERSIONS" and not (launch.pixel_id or template.pixel_id):
        return [
            "Оптимизация на конверсии требует пиксель — выберите его у кабинета "
            "на шаге «Кабинеты»"
        ]
    if goal in {"PAGE_LIKES", "CONVERSATIONS"} and not (launch.page_id or template.page_id):
        return ["Этой цели нужна страница Facebook — укажите её ID в связке"]
    if goal == "APP_INSTALLS":
        # Установки приложения требуют ID приложения и ссылки на стор, а их в
        # связке нет. Честнее сказать это здесь, чем отдать залив в Meta и
        # получить от неё ошибку про promoted_object.
        return [
            "Цель «Установки приложения» пока не заливается: Meta требует ID "
            "приложения и ссылку на стор, а связка их не хранит"
        ]
    return []


def _budget_problems(launch: MetaLaunch, template: MetaTemplate) -> list[str]:
    campaign = _block(template, "campaign")
    if str(campaign.get("budget_kind") or "daily") != "lifetime":
        return []
    if launch.end_date or launch.external_payload.get("end_time"):
        return []
    # Бюджет на весь срок без даты окончания Meta не принимает: ей нужно
    # знать, на сколько дней его растянуть.
    return ["Бюджет на весь срок требует даты окончания — укажите её в заливе"]


def _category_problems(template: MetaTemplate) -> list[str]:
    """Особая категория запрещает часть таргетинга — Meta отбивает адсет.

    Проверяем до отправки: сообщение «уберите пол и сузьте возраст» понятнее,
    чем её код 100 про недопустимый таргетинг.
    """
    campaign = _block(template, "campaign")
    if not (campaign.get("special_ad_categories") or []):
        return []
    problems = []
    if template.genders:
        problems.append("Особая категория рекламы запрещает таргетинг по полу")
    if (template.age_min or 18) != 18 or (template.age_max or 65) != 65:
        problems.append("Особая категория рекламы разрешает только возраст 18–65")
    adset = _block(template, "adset")
    if adset.get("excluded_geo") or adset.get("excluded_interests"):
        problems.append("Особая категория рекламы запрещает исключения в таргетинге")
    return problems


def _default_template(launch: MetaLaunch) -> MetaTemplate:
    """Шаблон по умолчанию для залива без шаблона.

    Значения проставлены явно: у несохранённого объекта column defaults ещё не
    применились, и `template.objective` был бы None — Meta ответила бы ошибкой
    про обязательное поле вместо понятного текста.
    """
    return MetaTemplate(
        workspace_id=launch.workspace_id,
        name="Без шаблона",
        objective="OUTCOME_LEADS",
        optimization_goal="LINK_CLICKS",
        billing_event="IMPRESSIONS",
        bid_strategy="LOWEST_COST_WITHOUT_CAP",
        geo=[launch.geo] if launch.geo else [],
        age_min=18,
        age_max=65,
        genders=[],
        languages=[],
        placements={},
        interests=[],
        call_to_action=launch.call_to_action or "LEARN_MORE",
        settings={},
    )


def _block(template: MetaTemplate, name: str) -> dict:
    """Блок связки. Пустой словарь означает «всё по умолчанию»."""
    settings = getattr(template, "settings", None) or {}
    block = settings.get(name)
    return block if isinstance(block, dict) else {}


def _name(pattern: object, context: dict, extra: dict | None = None) -> str:
    """Название объекта в кабинете — шаблон с макросами, развёрнутый под залив."""
    launch = context["launch"]
    account = context["account"]
    template = context["template"]
    now = datetime.now(UTC)
    genders = list(template.genders or [])
    values = {
        "bundle.name": template.name,
        "launch.name": launch.name,
        "cab.name": account.name,
        "cab.id": account.external_id,
        "cab.time": account.timezone_name or "",
        "bm.name": context.get("connection_name") or "",
        "page.id": launch.page_id or template.page_id or "",
        "pixel.id": launch.pixel_id or template.pixel_id or "",
        "geo": launch.geo or "",
        "offer": context.get("offer_name") or "",
        "age": f"{template.age_min or 18}-{template.age_max or 65}",
        "gender": "m" if genders == [1] else "f" if genders == [2] else "all",
        "date": now.date().isoformat(),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "time": now.strftime("%H:%M"),
        "campaign.number": 1,
        "adset.number": 1,
        "ad.number": 1,
        "creative.name": "",
        "campaign.id": launch.campaign_external_id or "",
        "adset.id": launch.adset_external_id or "",
    }
    values.update(extra or {})
    return render_macros(str(pattern or ""), values)[:100]


def _money(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return amount if amount > 0 else None


def _campaign_budgets(launch: MetaLaunch, template: MetaTemplate) -> dict:
    """Бюджет кампании — только когда связка ведёт его на уровне кампании.

    Расширенный режим мастера переопределяет уровень, тип и разброс поверх
    связки (`launch.budget_level/budget_kind/budget_randomize`).
    """
    campaign = _block(template, "campaign")
    level = launch.budget_level or str(campaign.get("budget_level") or "campaign")
    if level != "campaign":
        return {"daily_budget": None, "lifetime_budget": None}
    return _split_budget(launch, campaign)


def _adset_budgets(launch: MetaLaunch, template: MetaTemplate) -> dict:
    campaign = _block(template, "campaign")
    level = launch.budget_level or str(campaign.get("budget_level") or "campaign")
    if level != "adset":
        return {"daily_budget": None, "lifetime_budget": None}
    return _split_budget(launch, campaign)


def _randomized_age(template: MetaTemplate, seed: str, years: int = 3) -> dict:
    """Возраст ± заданное число лет — тот же приём против одинаковых адсетов,
    что и разброс бюджета.

    За 18 и 65 разброс не выходит: ниже восемнадцати Meta таргет не пускает
    вовсе, а выше 65 у неё просто нет — там «65 и старше».
    """
    rng = random.Random(seed + "age")
    shift = max(1, int(years or 1))
    low = max(18, min(65, int(template.age_min or 18) + rng.randint(-shift, shift)))
    high = max(low, min(65, int(template.age_max or 65) + rng.randint(-shift, shift)))
    return {"age_min": low, "age_max": high}


def _randomized(amount: Decimal, seed: str) -> Decimal:
    """Разброс ±10 % вокруг суммы.

    Одинаковый бюджет на двадцати кабинетах — заметный след, и Meta его видит.
    Seed берётся от залива, поэтому повтор после сбоя не меняет сумму: иначе
    вторая попытка ушла бы в кабинет с другим бюджетом.
    """
    shift = Decimal(str(random.Random(seed).uniform(-0.1, 0.1)))
    result = (amount * (Decimal(1) + shift)).quantize(Decimal("0.01"))
    return result if result > 0 else amount


def _split_budget(launch: MetaLaunch, campaign: dict) -> dict:
    """Одна сумма ложится в дневной бюджет или в бюджет на весь срок.

    Оба поля разом Meta не принимает, поэтому выбор типа — это выбор поля, а не
    ещё одна сумма. Тип и разброс переопределяются расширенным режимом.
    """
    amount = _money(launch.daily_budget)
    if amount is None:
        return {"daily_budget": None, "lifetime_budget": None}
    kind = launch.budget_kind or str(campaign.get("budget_kind") or "daily")
    randomize = launch.budget_randomize or bool(campaign.get("budget_randomize"))
    if randomize:
        amount = _randomized(amount, str(launch.id))
    if kind == "lifetime":
        return {"daily_budget": None, "lifetime_budget": amount}
    return {"daily_budget": amount, "lifetime_budget": None}


def _ad_texts(launch: MetaLaunch, ad_block: dict, rng: random.Random) -> dict:
    """Тексты объявления: что задано в заливе, иначе — из связки.

    Залив сильнее связки: связка это заготовка, а под конкретный оффер тексты
    правят руками. Spintax разворачивается здесь — по варианту на объявление.
    """
    return {
        "primary_text": spin(launch.primary_text or ad_block.get("primary_text"), rng),
        "headline": spin(launch.headline or ad_block.get("headline"), rng),
        "description": spin(launch.description or ad_block.get("description"), rng),
    }


def _start_time(launch: MetaLaunch) -> str | None:
    """Когда адсету начать крутиться."""
    if launch.start_at:
        moment = launch.start_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if moment <= datetime.now(UTC):
            return None
        return moment.strftime("%Y-%m-%dT%H:%M:%S%z")
    return _schedule_time(launch.start_date)


def _promoted_object(launch: MetaLaunch, template: MetaTemplate) -> dict | None:
    """Что именно продвигает адсет. У разных целей это разные вещи.

    Конверсии — пиксель с событием, лайки страницы и переписки — сама страница.
    Meta требует промо-объект под цель, и без него отбивает адсет ошибкой 100.
    """
    goal = template.optimization_goal
    if goal == "OFFSITE_CONVERSIONS":
        pixel_id = launch.pixel_id or template.pixel_id
        if not pixel_id:
            return None
        return {
            "pixel_id": str(pixel_id),
            "custom_event_type": launch.custom_event_type
            or template.custom_event_type
            or "LEAD",
        }
    if goal in {"PAGE_LIKES", "CONVERSATIONS"}:
        page_id = launch.page_id or template.page_id
        return {"page_id": str(page_id)} if page_id else None
    return None


async def attach_rules_to_launch(db, launch: MetaLaunch, rule_ids: list) -> None:
    """Привязать автоправила к заливу: движок считает их по его объектам."""
    valid = [uuid.UUID(str(value)) for value in rule_ids if str(value)]
    if not valid:
        return
    rules = list(
        (
            await db.execute(
                select(MetaRule).where(
                    MetaRule.id.in_(valid), MetaRule.workspace_id == launch.workspace_id
                )
            )
        ).scalars()
    )
    for rule in rules:
        rule.launch_id = launch.id


def _ad_plan(launch: MetaLaunch, context: dict, ad_block: dict) -> list[dict]:
    """Что именно создавать объявлениями.

    Заданные в заливе объявления сильнее: у них свои тексты, языки и креативы.
    Креативы есть двух уровней: общие на объявление (`item.creative_ids`) и
    свои на каждый язык (`text.creative_ids`) — для «на каждый язык свой
    креатив». Уровень текста приоритетнее.
    """
    creatives = {str(creative.id): (link, creative) for link, creative in context["creatives"]}
    plan: list[dict] = []
    for index, item in enumerate((launch.ads or []), start=1):
        shared = [creatives[str(value)] for value in (item.get("creative_ids") or [])
                  if str(value) in creatives]
        texts = [_ad_text(launch, ad_block, text) for text in (item.get("texts") or [{}])]
        per_text = False
        first_own: list = []
        for text in texts:
            own = [creatives[str(value)]
                   for value in (text.get("creative_ids") or [])
                   if str(value) in creatives]
            if own:
                text["creatives"] = [creative for _, creative in own]
                if not first_own:
                    first_own = own
                per_text = True
        if per_text and not all(text.get("creatives") for text in texts):
            continue
        pool = shared or first_own
        if not pool:
            continue
        plan.append(
            {
                "texts": texts,
                "creatives": [creative for _, creative in pool],
                "link_id": pool[0][0].id,
                "label": pool[0][1].name or f"ad {index}",
            }
        )
    if plan:
        return plan
    for link, creative in context["creatives"]:
        plan.append(
            {
                "texts": [_ad_text(launch, ad_block, {})],
                "creatives": [creative],
                "link_id": link.id,
                "label": creative.name,
            }
        )
    return plan


_LOCALE_ID_CACHE: dict[str, int] = {}


async def _locale_id_by_name(client: MetaClient, language: str, name: str) -> int:
    """Числовой ID локали Meta по её человеческому имени (кэш на процесс).

    Словарь ищем по первому слову имени («English (US)» → «English»), точное
    совпадение имени выбирает нужный вариант среди англоязычных и прочих
    одноимённых. Без имени или без попадания — понятная ошибка: отправлять
    в Meta заведомо неверный ID значит получить ровно тот же код 100.
    """
    cached = _LOCALE_ID_CACHE.get(language)
    if cached:
        return cached
    if not name:
        raise MetaError(
            f"У языка «{language}» не сохранилось название — вернитесь на шаг "
            "«Креативы» и выберите язык заново"
        )
    words = name.replace("(", " ").replace(")", " ").split()
    if not words:
        raise MetaError(f"Не удалось разобрать название языка «{name}»")
    rows = await client.targeting_search("locale", words[0], limit=50)
    wanted = name.strip().lower()
    match = next(
        (row for row in rows if str(row.get("name") or "").strip().lower() == wanted),
        None,
    )
    if match is None:
        match = next(
            (
                row
                for row in rows
                if str(row.get("name") or "").strip().lower().startswith(words[0].lower())
            ),
            None,
        )
    if match is None:
        raise MetaError(
            f"Meta не нашла язык «{name}» в справочнике — перевыберите его "
            "на шаге «Креативы»"
        )
    try:
        locale_id = int(match["key"])
    except (KeyError, TypeError, ValueError):
        raise MetaError(f"Meta не вернула числовой ID языка «{name}»") from None
    _LOCALE_ID_CACHE[language] = locale_id
    return locale_id


def _ad_text(launch: MetaLaunch, ad_block: dict, text: dict) -> dict:
    """Тексты одного языка: что задано в объявлении, иначе — залив, иначе связка.

    Spintax разворачивается здесь, по варианту на язык: связка одна на все
    объявления, а текст в кабинете у каждого должен быть свой.
    """
    rng = random.Random(f"{launch.id}:{text.get('language') or ''}")
    return {
        "language": str(text.get("language") or ""),
        "language_name": str(text.get("language_name") or ""),
        # Креативы этого языка («на каждый язык свой») — пробросить дальше,
        # в план объявления: там они маппятся на конкретные MetaCreative.
        "creative_ids": [str(value) for value in (text.get("creative_ids") or [])],
        "headline": spin(
            text.get("headline") or launch.headline or ad_block.get("headline"), rng
        ),
        "description": spin(
            text.get("description") or launch.description or ad_block.get("description"), rng
        ),
        "primary_text": spin(
            text.get("primary_text") or launch.primary_text or ad_block.get("primary_text"), rng
        ),
        "link_url": str(
            text.get("link_url") or launch.link_url or ad_block.get("link_url") or ""
        ),
        "call_to_action": str(
            text.get("call_to_action") or launch.call_to_action or ad_block.get("call_to_action")
            or "LEARN_MORE"
        ),
    }


def _launch_with_link(launch: MetaLaunch, link: str, texts: dict) -> MetaLaunch:
    """Копия залива со ссылкой, в которую уже дописан sub_id с ID кампании.

    Сам залив не трогаем: в CRM должна остаться ссылка в том виде, в каком её
    ввёл баер, без служебного макроса. Тексты тоже подставляются копии — в
    заливе остаётся spintax, а в кабинет уходит выбранный вариант.
    """
    proxy = MetaLaunch(
        workspace_id=launch.workspace_id,
        account_id=launch.account_id,
        name=launch.name,
        link_url=link,
        primary_text=texts.get("primary_text"),
        headline=texts.get("headline"),
        description=texts.get("description"),
        call_to_action=texts.get("call_to_action") or launch.call_to_action,
    )
    return proxy


def _schedule_time(value: date | None) -> str | None:
    if not value:
        return None
    if value <= datetime.now(UTC).date():
        # Прошедшую дату Meta отклоняет — для неё «начать сейчас» это отсутствие поля.
        return None
    return f"{value.isoformat()}T00:00:00+0000"


def _jsonable(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    result: dict = {}
    for key, value in payload.items():
        if isinstance(value, dict | list | str | int | float | bool) or value is None:
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return message[:500] if message else type(exc).__name__
