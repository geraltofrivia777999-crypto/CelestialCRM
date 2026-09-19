"""Один прогон синка с ПП — общий для ручного запуска и расписания.

Раньше эти два пути были написаны отдельно и разошлись: ручной ловил только
`PartnerIntegrationError` и на любой другой ошибке оставлял прогон навсегда в
статусе «running», а плановый не сохранял `last_sync_at`, из-за чего карточка
вечно показывала прочерк. Теперь порядок действий один:

1. попросить сервис сходить в ПП за период (если интеграция с ним связана);
2. забрать `/stats`;
3. отфильтровать по привязкам этой интеграции;
4. разложить по книгам баеров — по тегу;
5. закрыть прогон, что бы ни случилось.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Offer, PartnerIntegration, PartnerSyncRun
from app.services.partner_deposits import distribute_deposits
from app.services.partner_integrations import PartnerIntegrationError

logger = logging.getLogger(__name__)


async def mapped_offers(db: AsyncSession, integration: PartnerIntegration) -> list[Offer]:
    """Офферы этой интеграции — те, у которых заполнен ID партнёрки.

    Привязка живёт в самом оффере: раздел «Оффера» — единственное место, где её
    заводят. Синхронизированные из Keitaro сюда не попадают: у них `external_id`
    принадлежит трекеру, а не партнёрке.

    Номер оффера принадлежит конкретной программе, поэтому берём только те, у
    которых выбрана эта интеграция. Офферы без выбора достаются ей же, но лишь
    пока интеграция в воркспейсе одна: со второй тот же номер значит уже другой
    оффер, и отдавать его обеим — это привезти депозиты не туда.
    """
    total = await db.scalar(
        select(func.count()).select_from(PartnerIntegration).where(
            PartnerIntegration.workspace_id == integration.workspace_id
        )
    )
    scope = [Offer.partner_integration_id == integration.id]
    if (total or 0) <= 1:
        scope.append(Offer.partner_integration_id.is_(None))
    return list(
        (
            await db.execute(
                select(Offer).where(
                    Offer.workspace_id == integration.workspace_id,
                    Offer.connection_id.is_(None),
                    Offer.external_id.is_not(None),
                    Offer.external_id != "",
                    or_(*scope),
                )
            )
        ).scalars()
    )


async def unassigned_offers(db: AsyncSession, workspace_id) -> int:
    """Сколько офферов с ID остались без интеграции — их депозиты не приедут."""
    return await db.scalar(
        select(func.count()).select_from(Offer).where(
            Offer.workspace_id == workspace_id,
            Offer.connection_id.is_(None),
            Offer.external_id.is_not(None),
            Offer.external_id != "",
            Offer.partner_integration_id.is_(None),
        )
    ) or 0


async def ensure_partner_ref(db: AsyncSession, offer: Offer) -> int:
    """Числовой ключ оффера для сервиса.

    Сервис принимает `crm_offer_id` только целым числом — на UUID отвечает 422,
    и привязка не заводится. Номер выдаём один раз и больше не меняем: им же
    сервис помечает факты, которые возвращает в `/stats`, и смена номера
    оторвала бы уже собранные данные от оффера.
    """
    if offer.partner_ref:
        return offer.partner_ref
    highest = await db.scalar(
        select(func.max(Offer.partner_ref)).where(
            Offer.workspace_id == offer.workspace_id
        )
    )
    offer.partner_ref = int(highest or 0) + 1
    await db.flush()
    return offer.partner_ref


async def push_offer_mappings(
    db: AsyncSession, integration: PartnerIntegration, client
) -> tuple[int, list[str]]:
    """Отдать сервису привязки офферов перед синком.

    Сервис не покажет оффер в `/stats`, пока не знает про его связь с оффером
    ПП. Раньше это заводили руками в настройках; теперь берём прямо из офферов
    и обновляем на каждом синке — правка ID в разделе «Оффера» доезжает сама,
    без второго места, где то же самое надо не забыть поменять.
    """
    notes: list[str] = []
    if not integration.external_id:
        return 0, notes
    pushed = 0
    for offer in await mapped_offers(db, integration):
        ref = await ensure_partner_ref(db, offer)
        try:
            await client.put_offer_mapping(
                integration.external_id, str(ref), str(offer.external_id)
            )
            pushed += 1
        except PartnerIntegrationError as exc:
            notes.append(f"Оффер «{offer.name}»: сервис не принял привязку — {exc}")
    return pushed, notes


async def collect_facts(
    db: AsyncSession,
    integration: PartnerIntegration,
    client,
    date_from: date,
    date_to: date,
) -> tuple[list[dict], list[str]]:
    """Свежие факты сервиса за период, суженные до наших офферов."""
    notes: list[str] = []
    offers = await mapped_offers(db, integration)
    if not offers:
        notes.append(
            "Ни у одного оффера не заполнен ID партнёрки. Укажите его в разделе "
            "«Оффера» — по нему депозиты и находят оффер."
        )
        return [], notes
    # Оффер без выбранной интеграции ни одной из них не достаётся, когда их
    # несколько: молча пропустить его — значит потерять его депозиты.
    orphans = await unassigned_offers(db, integration.workspace_id)
    if orphans:
        notes.append(
            f"Офферов без выбранной интеграции: {orphans}. Их депозиты не "
            "приедут — укажите «ID ПП» в разделе «Оффера»."
        )
    if integration.external_id:
        pushed, push_notes = await push_offer_mappings(db, integration, client)
        notes.extend(push_notes)
        if pushed:
            notes.append(f"Привязок отдано сервису: {pushed}.")
        # Дозагрузка прошлых дат возможна только так: расписание самого сервиса
        # ходит в ПП лишь «за сегодня».
        await client.trigger_sync(integration.external_id, date_from, date_to)
    else:
        notes.append(
            "Интеграция не связана с сервисом: читаем только то, что он уже "
            "собрал. Укажите ID интеграции на сервисе, чтобы синк ходил в ПП."
        )
    # Read the selected connection only. The legacy unscoped endpoint sums
    # all mappings, including duplicate old/new connections to the same PP.
    scope = {"integration_id": integration.external_id} if integration.external_id else {}
    facts = await client.stats(date_from, date_to, **scope)
    # Сервис отдаёт `offer_id` тем значением, которое мы ему и передали как
    # `crm_offer_id`, — то есть наш числовой ключ. UUID и номер оффера у
    # партнёрки принимаем тоже: так синк работает и с интеграцией, настроенной
    # на сервисе руками, и со старыми привязками.
    by_uuid = {str(offer.id): offer for offer in offers}
    by_external = {str(offer.external_id): offer for offer in offers}
    by_ref = {str(offer.partner_ref): offer for offer in offers if offer.partner_ref}
    kept = []
    for fact in facts:
        raw = str(fact.get("offer_id") or "")
        offer = by_ref.get(raw) or by_uuid.get(raw) or by_external.get(raw)
        if not offer:
            continue
        row = dict(fact)
        row["offer_id"] = str(offer.id)
        kept.append(row)
    dropped = len(facts) - len(kept)
    if dropped:
        notes.append(f"{dropped} фактов не относятся к нашим офферам.")
    return kept, notes


def duplicate_notes(facts: list[dict]) -> list[str]:
    """Сообщить, если сервис прислал одну и ту же тройку дважды.

    Ключ факта — дата, оффер и тег; повтор означает, что итоговое число в книге
    зависит от порядка строк в ответе. Это либо две интеграции сервиса, знающие
    один и тот же оффер, либо разбивка, которую сервис не сложил. И то и другое
    выглядит в книге как «цифра не сходится с кабинетом партнёрки», поэтому
    молчать об этом нельзя.
    """
    seen: dict[tuple[str, str, str], int] = {}
    for fact in facts:
        key = (
            str(fact.get("date") or "")[:10],
            str(fact.get("offer_id") or ""),
            " ".join(str(fact.get("tag") or "").split()).casefold(),
        )
        seen[key] = seen.get(key, 0) + 1
    repeated = sum(1 for count in seen.values() if count > 1)
    if not repeated:
        return []
    return [
        f"Сервис прислал повторяющиеся строки: {repeated} троек «дата — оффер — "
        "тег» встретились больше одного раза. В книгу попало последнее значение "
        "каждой — сверьте с кабинетом партнёрки."
    ]


async def perform_sync(
    db: AsyncSession,
    integration: PartnerIntegration,
    client,
    date_from: date,
    date_to: date,
) -> dict:
    """Забрать факты и разложить их. Исключения не глушит — их пишет вызывающий."""
    facts, notes = await collect_facts(db, integration, client, date_from, date_to)
    notes.extend(duplicate_notes(facts))
    result = await distribute_deposits(db, integration.workspace_id, facts)
    result["reasons"] = notes + list(result.get("reasons") or [])
    result["received"] = len(facts)
    return result


def finish_run(run: PartnerSyncRun, integration: PartnerIntegration, result: dict) -> None:
    run.status = "success"
    run.records_upserted = result["upserted"]
    run.records_pending = result.get("pending", 0)
    run.records_skipped = result.get("skipped", 0)
    run.details = {
        "received": result.get("received", 0),
        "reasons": result.get("reasons", [])[:20],
    }
    run.error = None
    run.finished_at = datetime.now(UTC)
    integration.last_sync_status = "success"
    integration.last_sync_error = None
    integration.last_sync_at = run.finished_at


def fail_run(run: PartnerSyncRun, integration: PartnerIntegration, exc: Exception) -> str:
    """Закрыть прогон ошибкой. Любое исключение, а не только сбой сервиса:
    иначе неожиданная ошибка оставляла прогон висеть в «running» навсегда."""
    if isinstance(exc, PartnerIntegrationError):
        message = str(exc)
    else:
        logger.exception("partner sync failed: %s", integration.name)
        message = f"Внутренняя ошибка синка: {' '.join(str(exc).split())}"
    message = message[:500]
    run.status = "failed"
    run.error = message
    run.finished_at = datetime.now(UTC)
    integration.last_sync_status = "failed"
    integration.last_sync_error = message
    integration.last_sync_at = run.finished_at
    return message
