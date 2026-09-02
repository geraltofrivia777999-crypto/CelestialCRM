"""Клиент Meta Marketing API (Graph API).

Чтение (кабинеты, объекты, статистика) работает на разрешении `ads_read`.
Запись — создание кампаний, загрузка креативов, пауза и смена бюджета — требует
`ads_management`: без него Meta вернёт код 200, и мы покажем это словами.

Повторы для чтения и записи устроены по-разному, и это принципиально. GET можно
переслать сколько угодно раз; POST — нельзя: ответ мог потеряться уже после того,
как Meta создала кампанию, и повтор создал бы вторую. Поэтому записи повторяются
только тогда, когда достоверно известно, что запрос до Meta не дошёл.
"""

import asyncio
import hashlib
import json
import os
import time
import zlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import settings
from app.services.meta_session import normalize_proxy_url

ACCOUNT_FIELDS = [
    "id",
    "account_id",
    "name",
    "account_status",
    "currency",
    "timezone_name",
    "spend_cap",
    "amount_spent",
    "balance",
    "disable_reason",
]
ENTITY_FIELDS = {
    "campaign": ["id", "name", "status", "effective_status", "objective", "daily_budget",
                 "lifetime_budget", "start_time", "stop_time"],
    "adset": ["id", "name", "campaign_id", "status", "effective_status", "daily_budget",
              "lifetime_budget", "optimization_goal", "start_time", "end_time"],
    # `effective_object_story_id` — это "<page_id>_<post_id>": по нему объявление
    # связывается с фан-пейджем, от лица которого крутится.
    "ad": ["id", "name", "adset_id", "status", "effective_status",
           "creative{id,effective_object_story_id,object_story_spec}"],
}
ENTITY_EDGE = {"campaign": "campaigns", "adset": "adsets", "ad": "ads"}
ENTITY_PARENT_FIELD = {"campaign": None, "adset": "campaign_id", "ad": "adset_id"}
INSIGHT_FIELDS = [
    "date_start",
    "campaign_id",
    "adset_id",
    "ad_id",
    "impressions",
    "clicks",
    # Общие клики включают лайки и развороты текста; трафик мерят вот этим.
    "inline_link_clicks",
    "reach",
    "spend",
    "actions",
]
HOURLY_BREAKDOWN = "hourly_stats_aggregated_by_advertiser_time_zone"
HOURLY_INSIGHT_FIELDS = [
    "date_start",
    "impressions",
    "clicks",
    "inline_link_clicks",
    "spend",
    "actions",
]
BUSINESS_FIELDS = ["id", "name", "verification_status", "created_time"]
PAGE_FIELDS = ["id", "name", "category", "link"]
# `from` Meta отдаёт только токену с правами на страницу: у рекламного токена
# без `pages_read_engagement` автор приедет пустым, и это единственный внешний
# признак, по которому видно, что прав не хватает.
COMMENT_FIELDS = [
    "id",
    "message",
    "created_time",
    "from{id,name}",
    "is_hidden",
    "like_count",
    "comment_count",
    "parent{id}",
]

# Что Meta возвращает в account_status числом. Без расшифровки в CRM попадала бы
# цифра, по которой невозможно понять, почему кабинет не откручивает.
ACCOUNT_STATUS = {
    1: "ACTIVE",
    2: "DISABLED",
    3: "UNSETTLED",
    7: "PENDING_RISK_REVIEW",
    8: "PENDING_SETTLEMENT",
    9: "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
    201: "ANY_ACTIVE",
    202: "ANY_CLOSED",
}
# Лиды и покупки в терминах пикселя. Meta возвращает десятки типов действий,
# деньгами из них не является ни один — доход приходит из Keitaro.
LEAD_ACTION_TYPES = {"lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"}
PURCHASE_ACTION_TYPES = {"purchase", "offsite_conversion.fb_pixel_purchase"}

# Справочники для форм: что Meta принимает в соответствующих полях. Список
# намеренно короткий — это то, что реально используется в заливах на трафике,
# а не полный перечень из документации.
OBJECTIVES = {
    "OUTCOME_SALES": "Продажи",
    "OUTCOME_LEADS": "Лиды",
    "OUTCOME_TRAFFIC": "Трафик",
    "OUTCOME_ENGAGEMENT": "Вовлечённость",
    "OUTCOME_AWARENESS": "Узнаваемость",
    "OUTCOME_APP_PROMOTION": "Приложение",
}
OPTIMIZATION_GOALS = {
    "OFFSITE_CONVERSIONS": "Конверсии на сайте",
    "LINK_CLICKS": "Клики по ссылке",
    "LANDING_PAGE_VIEWS": "Просмотры лендинга",
    "LEAD_GENERATION": "Лид-формы",
    "IMPRESSIONS": "Показы",
    "REACH": "Охват",
    "VALUE": "Ценность конверсии",
}
BILLING_EVENTS = {"IMPRESSIONS": "За показы", "LINK_CLICKS": "За клики"}
BID_STRATEGIES = {
    "LOWEST_COST_WITHOUT_CAP": "Максимальное количество",
    "LOWEST_COST_WITH_BID_CAP": "Предельная ставка",
    "COST_CAP": "Цель по цене за результат",
}
CALL_TO_ACTIONS = {
    "APPLY_NOW": "Подать заявку",
    "BOOK_TRAVEL": "Забронировать",
    "BUY_TICKETS": "Купить билеты",
    "CONTACT_US": "Связаться с нами",
    "DOWNLOAD": "Скачать",
    "GET_OFFER": "Узнать стоимость (offer)",
    "GET_QUOTE": "Узнать стоимость",
    "GET_SHOWTIMES": "Информация о сеансах",
    "LEARN_MORE": "Подробнее",
    "LISTEN_NOW": "Слушать",
    "ORDER_NOW": "Заказать",
    "PLAY_GAME": "Играть",
    "REQUEST_TIME": "Запросить время",
    "SEE_MENU": "Меню",
    "SHOP_NOW": "В магазин",
    "SIGN_UP": "Регистрация",
    "SUBSCRIBE": "Подписаться",
    "WATCH_MORE": "Смотреть ещё",
}
PUBLISHER_PLATFORMS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "audience_network": "Audience Network",
    "messenger": "Messenger",
}
# Конверсия, ради которой оптимизируется adset. Meta требует её вместе с пикселем,
# когда optimization_goal = OFFSITE_CONVERSIONS.
CUSTOM_EVENT_TYPES = {
    "LEAD": "Лид",
    "COMPLETE_REGISTRATION": "Регистрация",
    "PURCHASE": "Покупка",
    "ADD_TO_CART": "Добавление в корзину",
    "INITIATED_CHECKOUT": "Начало оформления",
    "SUBMIT_APPLICATION": "Заявка",
}
# Чем выпущен токен. На сами запросы это не влияет — Graph API любой токен
# принимает одинаково, — но живут они по-разному, и когда синхронизация встанет,
# объяснение «почему» зависит от способа.
AUTH_METHODS = {
    "system_user": "System User",
    "app_token": "Токен приложения",
    "session": "Токен сессии (EAAB)",
}
AUTH_METHOD_HINTS = {
    "system_user": (
        "Бессрочный токен системного пользователя. Если он перестал работать — "
        "его отозвали, сняли доступ к кабинету или при выпуске выбрали срок "
        "60 дней вместо «Never»."
    ),
    "app_token": (
        "Токен из панели приложения живёт часы. Для фоновой синхронизации он не "
        "предназначен — выпустите токен системного пользователя в Business Manager."
    ),
    "session": (
        "Токен сессии живёт, пока жива сессия аккаунта: смена пароля, выход из "
        "устройств или запрос подтверждения личности его обнуляют. Получите новый "
        "тем же способом или переходите на системного пользователя."
    ),
}

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
VIDEO_MIME_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
# Коды из тела ответа Graph API. HTTP тут почти всегда 400, поэтому решение о
# повторе и текст для пользователя приходится принимать по error.code.
RETRYABLE_ERROR_CODES = {1, 2, 4, 17, 32, 341, 613, 80000, 80001, 80002, 80003, 80004}
# Подмножество, при котором Meta заведомо не выполнила запрос, а отбила его по
# лимиту. Только эти коды дают право повторить запись.
RATE_LIMIT_ERROR_CODES = {4, 17, 32, 341, 613, 80000, 80001, 80002, 80003, 80004}
ERROR_HINTS = {
    190: (
        "Meta отклонила токен (код 190). Он истёк или отозван — выпустите новый "
        "токен системного пользователя в Business Manager и вставьте его в подключение."
    ),
    102: (
        "Meta закрыла сессию токена (код 102). Нужен новый токен системного "
        "пользователя — токены обычного пользователя живут недолго."
    ),
    200: (
        "У токена нет прав на этот запрос (код 200). Проверьте, что системному "
        "пользователю выданы права ads_read и доступ к рекламным кабинетам."
    ),
    294: (
        "Meta требует прохождения App Review для этого запроса (код 294). "
        "Чтения статистики это касаться не должно — проверьте разрешения приложения."
    ),
    17: (
        "Meta временно ограничила частоту запросов (код 17). Синхронизация "
        "повторится сама; если повторяется постоянно — увеличьте интервал."
    ),
    100: (
        "Meta не приняла параметры запроса (код 100). Чаще всего это неверный ID "
        "кабинета или версия Graph API, которую Meta уже не поддерживает."
    ),
    1: (
        "Meta отклонила запрос (код 1 «Invalid request»). Для токена сессии это "
        "обычно чекпойнт аккаунта: зайдите в браузере сессии на www.facebook.com, "
        "войдите и пройдите проверку (код/подтверждение личности), затем получите "
        "токен заново. Для токена системного пользователя проверьте, что приложение "
        "в Business Manager не ограничено."
    ),
}


def graph_base_url() -> str:
    return f"{settings.meta_api_base.rstrip('/')}/{settings.meta_graph_version}"


class MetaError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retryable = retryable


class MetaClient:
    """Retry-aware клиент Graph API с курсорной постраничкой."""

    def __init__(
        self,
        access_token: str,
        *,
        api_base: str | None = None,
        version: str | None = None,
        timeout: float = 60.0,
        max_attempts: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        proxy: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.version = version or settings.meta_graph_version
        base = (api_base or settings.meta_api_base).rstrip("/")
        self.base_url = f"{base}/{self.version}"
        # Токен уходит заголовком, а не параметром запроса: URL попадает в логи
        # прокси и в текст ошибок, а секрет там оказаться не должен.
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        # Через какой адрес ходить. Свой прокси у подключения нужен там, где
        # кабинеты живут за ним: запрос из другой сети Meta просто не пустит к
        # кабинету, привязанному к конкретной стране.
        if user_agent:
            self.headers["User-Agent"] = user_agent
        self.proxy = proxy or None
        if self.proxy:
            # Страховка на случай «сырого» формата host:port:user:pass (так
            # пишут продавцы прокси): httpx такой адрес не примет. Канонический
            # вид нормализуется ещё при сохранении подключения, здесь — для
            # старых записей и прямых вызовов. Некорректный адрес пускаем как
            # есть — httpx сам вернёт понятную ошибку.
            try:
                self.proxy = normalize_proxy_url(self.proxy)
            except Exception:
                pass
        self.timeout = timeout
        self.max_attempts = max(max_attempts, 1)
        self.transport = transport

    async def _request(self, path: str, params: dict | None = None) -> dict:
        return await self._send("GET", path, params=params)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict:
        # GET повторяем свободно. У записи повтор допустим только там, где Meta
        # точно ничего не сделала: либо соединение не установилось, либо она сама
        # ответила «слишком часто». Таймаут чтения под это не подходит — ответ мог
        # потеряться уже после создания объекта.
        idempotent = method == "GET"
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=self.headers,
                    timeout=self.timeout,
                    transport=self.transport,
                    # Прокси не задаётся вместе с транспортом: транспорт бывает
                    # только в тестах, и подменять его прокси нечем.
                    **({"proxy": self.proxy} if self.proxy and not self.transport else {}),
                ) as client:
                    response = await client.request(
                        method, path, params=params, data=data, files=files
                    )
                payload = _json_body(response)
                error = payload.get("error") if isinstance(payload, dict) else None
                if error:
                    code = _int_or_none(error.get("code"))
                    rate_limited = code in RATE_LIMIT_ERROR_CODES
                    retryable = (
                        code in RETRYABLE_ERROR_CODES
                        or response.status_code in RETRYABLE_STATUSES
                    )
                    may_retry = retryable if idempotent else rate_limited
                    if may_retry and attempt < self.max_attempts:
                        await asyncio.sleep(self._retry_delay(response, attempt))
                        continue
                    raise MetaError(
                        _error_message(error),
                        status_code=response.status_code,
                        error_code=code,
                        retryable=retryable,
                    )
                if (
                    idempotent
                    and response.status_code in RETRYABLE_STATUSES
                    and attempt < self.max_attempts
                ):
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return payload if isinstance(payload, dict) else {}
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise MetaError(
                    f"Meta ответила HTTP {status} на {path}",
                    status_code=status,
                    retryable=status in RETRYABLE_STATUSES,
                ) from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if not idempotent and not isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
                    raise MetaError(
                        f"Связь с Meta оборвалась во время запроса {method} {path}. "
                        "Повторять его автоматически нельзя — объект мог быть уже создан. "
                        "Откройте журнал операций и проверьте кабинет.",
                        retryable=False,
                    ) from exc
                if attempt < self.max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
                    continue

        raise MetaError(
            f"Meta не отвечает по адресу {self.base_url} "
            f"({self.max_attempts} попытки). Проверьте, что сервер видит graph.facebook.com.",
            retryable=True,
        ) from last_error

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return min(max(float(value), 0), 60)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    return min(max(retry_at.timestamp() - time.time(), 0), 60)
                except (TypeError, ValueError):
                    pass
        return min(2 ** (attempt - 1), 8)

    async def _paged(self, path: str, params: dict, *, page_limit: int = 100) -> list[dict]:
        rows: list[dict] = []
        cursor: str | None = None
        for _ in range(page_limit):
            page_params = dict(params)
            if cursor:
                page_params["after"] = cursor
            payload = await self._request(path, page_params)
            rows.extend(row for row in payload.get("data", []) if isinstance(row, dict))
            paging = payload.get("paging") or {}
            cursor = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not cursor:
                break
        return rows

    async def check(self) -> dict:
        """Проверка токена. Возвращает то, чем Meta считает его владельца."""
        return await self._request("/me", {"fields": "id,name"})

    async def ad_accounts(self, business_id: str | None = None) -> list[dict]:
        params = {"fields": ",".join(ACCOUNT_FIELDS), "limit": 100}
        if business_id:
            return await self._paged(f"/{business_id}/owned_ad_accounts", params)
        return await self._paged("/me/adaccounts", params)

    async def businesses(self) -> list[dict]:
        """Бизнес-менеджеры владельца токена. Требует business_management."""
        return await self._paged(
            "/me/businesses", {"fields": ",".join(BUSINESS_FIELDS), "limit": 100}
        )

    async def pages(self, business_id: str | None = None) -> list[dict]:
        """Фан-пейджи: свои у социального аккаунта, `owned_pages` — у БМа."""
        params = {"fields": ",".join(PAGE_FIELDS), "limit": 100}
        if business_id:
            return await self._paged(f"/{business_id}/owned_pages", params)
        return await self._paged("/me/accounts", params)

    async def entities(self, account_external_id: str, level: str) -> list[dict]:
        if level not in ENTITY_EDGE:
            raise ValueError("Unsupported Meta entity level")
        return await self._paged(
            f"/{account_external_id}/{ENTITY_EDGE[level]}",
            {"fields": ",".join(ENTITY_FIELDS[level]), "limit": 200},
        )

    async def insights(
        self,
        account_external_id: str,
        start: date,
        end: date,
        *,
        level: str = "ad",
    ) -> list[dict]:
        return await self._paged(
            f"/{account_external_id}/insights",
            {
                "level": level,
                "fields": ",".join(INSIGHT_FIELDS),
                "time_increment": 1,
                "time_range": f'{{"since":"{start.isoformat()}","until":"{end.isoformat()}"}}',
                "limit": 500,
            },
        )


    async def hourly_insights(
        self, external_id: str, start: date, end: date
    ) -> list[dict]:
        """Расход по часам для одного объекта — кампании, адсета или объявления.

        Разбивка `hourly_stats_aggregated_by_advertiser_time_zone` считает часы
        по таймзоне рекламного кабинета, а не по нашей: у Meta других вариантов
        нет, и это надо показывать пользователю, а не молча выдавать за местное
        время. Данные не храним — их запрашивают точечно и редко.
        """
        return await self._paged(
            f"/{external_id}/insights",
            {
                "fields": ",".join(HOURLY_INSIGHT_FIELDS),
                "breakdowns": HOURLY_BREAKDOWN,
                "time_increment": 1,
                "time_range": f'{{"since":"{start.isoformat()}","until":"{end.isoformat()}"}}',
                "limit": 500,
            },
            page_limit=20,
        )

    async def pixels(self, account_external_id: str) -> list[dict]:
        """Пиксели кабинета. Их заводят в Events Manager, и синхронизация их не
        видит — спрашиваем у Meta в момент, когда они понадобились."""
        return await self._paged(
            f"/{account_external_id}/adspixels", {"fields": "id,name", "limit": 100}
        )

    async def account_pages(self, account_external_id: str) -> list[dict]:
        """Страницы, которые кабинет может продвигать."""
        return await self._paged(
            f"/{account_external_id}/promote_pages", {"fields": "id,name", "limit": 100}
        )

    async def page_tokens(self) -> dict[str, str]:
        """Токены страниц, доступных владельцу текущего токена.

        Комментарии тёмных (рекламных) постов Graph отдаёт только в контексте
        страницы: юзер-токен получает `total_count: 0` даже когда коммент
        виден в интерфейсе Facebook. Токен страницы это ограничение снимает,
        поэтому читаем комментарии им — там, где страница наша.
        """
        rows = await self._paged(
            "/me/accounts", {"fields": "id,access_token", "limit": 100}, page_limit=5
        )
        return {row["id"]: row.get("access_token") or "" for row in rows}

    async def page_ads_posts(self, page_external_id: str) -> list[dict]:
        """Рекламные (в том числе тёмные) посты страницы.

        Запасной путь для динамических объявлений: у них нет своего поста,
        но пост страницы, под которым крутится реклама, существует.
        """
        return await self._paged(
            f"/{page_external_id}/ads_posts",
            {
                "fields": "id,created_time,message,permalink_url,is_published",
                "limit": 100,
            },
            page_limit=2,
        )

    async def dsa_recommendations(self, account_external_id: str) -> list[str]:
        """Варианты «Бенефициар / Плательщик», которые подсказывает сам кабинет.

        Meta по активности кабинета сама видит, чьё имя обычно стоит в
        DSA-прозрачности, и отдаёт список строк — его и показываем в селекте
        мастера залива. Пустой список не ошибка: кабинет мог ещё не рекламить.
        """
        payload = await self._request(
            f"/{account_external_id}", {"fields": "dsa_recommendations"}
        )
        block = payload.get("dsa_recommendations") or {}
        return [
            str(item) for item in (block.get("recommendations") or []) if str(item).strip()
        ]

    async def targeting_search(self, kind: str, query: str, limit: int = 25) -> list[dict]:
        """Поиск по справочникам таргетинга Meta — интересы и языки.

        Своего справочника здесь быть не может: интересы Meta заводит и
        переименовывает сама, а её ID — единственное, что она принимает в
        `flexible_spec`. Список, набитый руками, устарел бы в первый же месяц.
        """
        types = {"interest": "adinterest", "locale": "adlocale"}
        params = {"type": types[kind], "q": query, "limit": limit}
        response = await self._request("/search", params)
        rows = response.get("data") if isinstance(response, dict) else None
        return list(rows or [])

    async def object_fields(self, external_id: str, fields: list[str]) -> dict:
        return await self._request(f"/{external_id}", {"fields": ",".join(fields)})

    # --- запись (ТЗ 3.4 и 3.6), требует ads_management -------------------------

    async def create_campaign(
        self,
        account_external_id: str,
        *,
        name: str,
        objective: str,
        status: str = "PAUSED",
        spend_cap: Decimal | None = None,
        special_ad_categories: list[str] | None = None,
        daily_budget: Decimal | None = None,
        lifetime_budget: Decimal | None = None,
        bid_strategy: str | None = None,
        currency: str | None = None,
    ) -> dict:
        data = {
            "name": name,
            "objective": objective,
            "status": status,
            "buying_type": "AUCTION",
            # Поле обязательное даже когда категорий нет: без него Meta отвечает
            # ошибкой 100, а не подставляет пустой список сама.
            "special_ad_categories": json.dumps(special_ad_categories or []),
        }
        if spend_cap is not None:
            data["spend_cap"] = money_to_minor(spend_cap, currency)
        # Бюджет на кампании (CBO) — Meta распределяет его между адсетами сама.
        # Стратегия ставок уезжает туда же: на уровне адсета её при CBO не берут.
        if daily_budget is not None:
            data["daily_budget"] = money_to_minor(daily_budget, currency)
        if lifetime_budget is not None:
            data["lifetime_budget"] = money_to_minor(lifetime_budget, currency)
        if bid_strategy and (daily_budget is not None or lifetime_budget is not None):
            data["bid_strategy"] = bid_strategy
        return await self._send("POST", f"/{account_external_id}/campaigns", data=data)

    async def create_adset(
        self,
        account_external_id: str,
        *,
        name: str,
        campaign_id: str,
        targeting: dict,
        billing_event: str,
        optimization_goal: str,
        bid_strategy: str,
        daily_budget: Decimal | None = None,
        lifetime_budget: Decimal | None = None,
        status: str = "PAUSED",
        start_time: str | None = None,
        end_time: str | None = None,
        promoted_object: dict | None = None,
        attribution_spec: list[dict] | None = None,
        bid_amount: Decimal | None = None,
        accelerated: bool = False,
        spend_cap: Decimal | None = None,
        budget_min: Decimal | None = None,
        budget_max: Decimal | None = None,
        currency: str | None = None,
        dsa_beneficiary: str | None = None,
        dsa_payor: str | None = None,
    ) -> dict:
        data = {
            "name": name,
            "campaign_id": campaign_id,
            "billing_event": billing_event,
            "optimization_goal": optimization_goal,
            "targeting": json.dumps(targeting),
            "status": status,
        }
        if daily_budget is not None:
            data["daily_budget"] = money_to_minor(daily_budget, currency)
        if lifetime_budget is not None:
            data["lifetime_budget"] = money_to_minor(lifetime_budget, currency)
        # Стратегия ставок живёт рядом с бюджетом: при CBO её задаёт кампания, и
        # повтор на адсете Meta отбивает ошибкой.
        if daily_budget is not None or lifetime_budget is not None:
            data["bid_strategy"] = bid_strategy
            if bid_amount is not None and bid_strategy != "LOWEST_COST_WITHOUT_CAP":
                data["bid_amount"] = money_to_minor(bid_amount, currency)
        if accelerated:
            data["pacing_type"] = json.dumps(["no_pacing"])
        if spend_cap is not None:
            data["daily_spend_cap"] = money_to_minor(spend_cap, currency)
        # Лимит адсета мин/макс — рамки дневного бюджета группы.
        if budget_min is not None:
            data["daily_budget_min"] = money_to_minor(budget_min, currency)
        if budget_max is not None:
            data["daily_budget_max"] = money_to_minor(budget_max, currency)
        if start_time:
            data["start_time"] = start_time
        if end_time:
            data["end_time"] = end_time
        if promoted_object:
            data["promoted_object"] = json.dumps(promoted_object)
        if attribution_spec:
            data["attribution_spec"] = json.dumps(attribution_spec)
        # DSA-прозрачность: для рекламы на ЕС Meta требует бенефициара и
        # платильщика на адсете. Без обоих полей создание адсета падает, если
        # у кабинета не заданы значения по умолчанию.
        if dsa_beneficiary:
            data["dsa_beneficiary"] = dsa_beneficiary
        if dsa_payor:
            data["dsa_payor"] = dsa_payor
        return await self._send("POST", f"/{account_external_id}/adsets", data=data)

    async def create_ad_creative(
        self,
        account_external_id: str,
        *,
        name: str,
        object_story_spec: dict | None = None,
        asset_feed_spec: dict | None = None,
        page_id: str | None = None,
        url_tags: str | None = None,
        advantage_creative: bool = False,
        multi_advertiser: bool = False,
    ) -> dict:
        data: dict[str, str] = {"name": name}
        if object_story_spec is not None:
            data["object_story_spec"] = json.dumps(object_story_spec)
        if asset_feed_spec is not None:
            # У мультиязычного объявления вместо готовой истории — набор
            # вариантов, и страница задаётся отдельно.
            data["asset_feed_spec"] = json.dumps(asset_feed_spec)
            data["object_story_spec"] = json.dumps({"page_id": str(page_id or "")})
        if url_tags:
            data["url_tags"] = url_tags
        # Advantage+ для креативов Meta включает через «степени свободы»:
        # отдельного флага в API нет, есть разрешение менять текст и картинку.
        data["degrees_of_freedom_spec"] = json.dumps(
            {
                "creative_features_spec": {
                    "standard_enhancements": {
                        "enroll_status": "OPT_IN" if advantage_creative else "OPT_OUT"
                    }
                }
            }
        )
        # Показ в блоке с несколькими рекламодателями отправляем только когда
        # его включили: поле молодое, и на кабинетах, где Meta его ещё не
        # раскатала, лишний параметр стоил бы всего залива.
        if multi_advertiser:
            data["contextual_multi_ads"] = json.dumps({"enroll_status": "OPT_IN"})
        return await self._send("POST", f"/{account_external_id}/adcreatives", data=data)

    async def create_ad(
        self,
        account_external_id: str,
        *,
        name: str,
        adset_id: str,
        creative_id: str,
        status: str = "PAUSED",
    ) -> dict:
        return await self._send(
            "POST",
            f"/{account_external_id}/ads",
            data={
                "name": name,
                "adset_id": adset_id,
                "creative": json.dumps({"creative_id": creative_id}),
                "status": status,
            },
        )

    async def create_adlabel(self, account_external_id: str, name: str) -> dict:
        """Adlabel (тег) кабинета: потом вешается на кампании/адсеты/объявления."""
        return await self._send(
            "POST", f"/{account_external_id}/adlabels", data={"name": name}
        )

    async def list_adlabels(self, account_external_id: str) -> list[dict]:
        """Все adlabels кабинета — нужны для режима «убрать теги»."""
        rows = await self._paged(
            f"/{account_external_id}/adlabels", {"fields": "id,name", "limit": 100}
        )
        return [row for row in rows if row.get("id")]

    async def attach_adlabel(self, object_external_id: str, adlabel_id: str) -> None:
        await self._send(
            "POST", f"/{object_external_id}/adlabels", data={"adlabel_id": adlabel_id}
        )

    async def detach_adlabel(self, object_external_id: str, adlabel_id: str) -> None:
        await self._send(
            "DELETE", f"/{object_external_id}/adlabels/{adlabel_id}"
        )

    async def upload_image(
        self,
        account_external_id: str,
        *,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> dict:
        """Загрузка картинки. Meta отвечает хэшем, а не ID — им и адресуют файл."""
        payload = await self._send(
            "POST",
            f"/{account_external_id}/adimages",
            files={"source": (file_name, content, mime_type)},
        )
        images = payload.get("images")
        if isinstance(images, dict) and images:
            # Ключ — имя файла, под которым Meta его приняла, а не наше поле формы.
            return next(iter(images.values()))
        return payload

    async def upload_video(
        self,
        account_external_id: str,
        *,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> dict:
        return await self._send(
            "POST",
            f"/{account_external_id}/advideos",
            data={"name": file_name},
            files={"source": (file_name, content, mime_type)},
        )

    async def get_object(self, external_id: str, params: dict | None = None) -> dict:
        """Чтение одного объекта Graph API (например, превью загруженного видео)."""
        return await self._request(f"/{external_id}", params=params)

    # --- комментарии под постом объявления ------------------------------------

    async def post_comments(self, post_external_id: str, *, pages: int = 5) -> list[dict]:
        """Комментарии поста, с ответами в ветках.

        `filter=stream` отдаёт плоским списком и корневые комментарии, и ответы:
        спам чаще всего висит именно в ветке под чужим комментарием, и обходить
        ветки отдельными запросами значило бы умножить их число на порядок.
        Порядок обратно-хронологический — свежий мусор нужен первым, а глубину
        ограничивает `pages`: у старого поста комментариев могут быть тысячи, и
        выгребать их целиком ради ручной чистки незачем.
        """
        return await self._paged(
            f"/{post_external_id}/comments",
            {"fields": ",".join(COMMENT_FIELDS), "filter": "stream",
             "order": "reverse_chronological", "limit": 100},
            page_limit=max(pages, 1),
        )

    async def hide_comment(self, comment_external_id: str, hidden: bool) -> dict:
        """Скрыть или вернуть комментарий.

        Скрытый виден автору и его друзьям, но не остальным, — обратимая мера, в
        отличие от удаления.
        """
        return await self._send(
            "POST", f"/{comment_external_id}", data={"is_hidden": "true" if hidden else "false"}
        )

    async def delete_comment(self, comment_external_id: str) -> dict:
        """Удалить комментарий. Необратимо: Meta не отдаёт его больше никогда."""
        return await self._send("DELETE", f"/{comment_external_id}")

    async def update_object(self, external_id: str, data: dict) -> dict:
        return await self._send("POST", f"/{external_id}", data=data)

    async def set_status(self, external_id: str, status: str) -> dict:
        return await self.update_object(external_id, {"status": status})

    async def set_daily_budget(self, external_id: str, budget: Decimal) -> dict:
        return await self.update_object(external_id, {"daily_budget": money_to_minor(budget)})


def client_with_token(base: MetaClient, token: str) -> MetaClient:
    """Клиент с другим токеном — в том же транспорте и с теми же настройками.

    Нужен для токенов страниц: читаем комментарии тёмных постов токеном
    страницы, но через тот же браузерный контекст сессии — сессионные
    токены вне его Meta отклоняет.
    """
    return MetaClient(
        token,
        api_base=base.base_url.rsplit("/", 1)[0],
        version=base.version,
        timeout=base.timeout,
        max_attempts=base.max_attempts,
        transport=base.transport,
        proxy=base.proxy,
        user_agent=base.headers.get("User-Agent"),
    )


def uniquify(content: bytes, mime_type: str) -> bytes:
    """Дописать в файл случайную метку, чтобы у него сменился хэш.

    Один и тот же баннер на двадцати кабинетах — заметный след, и Meta считает
    его по хэшу файла. Метка кладётся в служебное поле формата: пиксели не
    меняются, картинка остаётся той же.

    Форматы, которые так не умеют, возвращаются как есть — портить файл ради
    уникальности хуже, чем оставить его прежним.
    """
    marker = os.urandom(8).hex().encode("ascii")
    if mime_type == "image/jpeg" and content[:2] == b"\xff\xd8":
        # COM-сегмент JPEG: комментарий, который декодеры пропускают.
        segment = b"\xff\xfe" + (len(marker) + 2).to_bytes(2, "big") + marker
        return content[:2] + segment + content[2:]
    if mime_type == "image/png" and content[:8] == b"\x89PNG\r\n\x1a\n":
        # tEXt-чанк PNG кладётся перед завершающим IEND.
        end = content.rfind(b"IEND")
        if end > 4:
            payload = b"Comment\x00" + marker
            chunk = (
                len(payload).to_bytes(4, "big")
                + b"tEXt"
                + payload
                + zlib.crc32(b"tEXt" + payload).to_bytes(4, "big")
            )
            return content[: end - 4] + chunk + content[end - 4 :]
    return content


def stat_dimension_key(account_external_id: str, row: dict) -> str:
    values = [
        account_external_id,
        str(row.get("date_start") or ""),
        str(row.get("campaign_id") or ""),
        str(row.get("adset_id") or ""),
        str(row.get("ad_id") or ""),
        str(row.get("country") or ""),
    ]
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def account_status_label(value: object) -> str | None:
    code = _int_or_none(value)
    if code is None:
        return None
    return ACCOUNT_STATUS.get(code, f"CODE_{code}")


def decimal_value(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def integer_value(value: object) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def money_from_minor(value: object) -> Decimal | None:
    """Лимиты и бюджеты Meta отдаёт в копейках/центах строкой.

    Отличать «нет лимита» от нуля обязательно: spend_cap = 0 у Meta означает,
    что лимит снят, а не что тратить нельзя.
    """
    if value in (None, ""):
        return None
    return (decimal_value(value) / Decimal(100)).quantize(Decimal("0.01"))


# Валюты без дробной части: у иены и воны «центов» нет, и Meta принимает
# бюджет в целых единицах. Умножение на сто дало бы кабинету стократный бюджет.
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "MGA",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}


def money_to_minor(value: Decimal | int | float | str, currency: str | None = None) -> str:
    """Обратная сторона money_from_minor: бюджеты уходят в Meta целыми центами."""
    factor = Decimal(1) if str(currency or "").upper() in ZERO_DECIMAL_CURRENCIES else Decimal(100)
    amount = decimal_value(value) * factor
    return str(int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def build_targeting(template: object) -> dict:
    """Собрать блок targeting из связки (ТЗ 3.5).

    Пустые плейсменты не подставляются пустым списком, а опускаются: для Meta
    отсутствие publisher_platforms означает автоматические плейсменты, а пустой
    список — ошибку. По той же причине опускаются устройства и версии ОС: для
    Meta «не указано» означает «все», а пустой список — ошибку.
    """
    adset = (getattr(template, "settings", None) or {}).get("adset") or {}
    countries = [str(code).upper()[:2] for code in (getattr(template, "geo", None) or []) if code]
    targeting: dict[str, Any] = {
        "age_min": int(getattr(template, "age_min", 18) or 18),
        "age_max": int(getattr(template, "age_max", 65) or 65),
    }
    regions = [str(value) for value in (adset.get("geo_regions") or []) if value]
    cities = [str(value) for value in (adset.get("geo_cities") or []) if value]
    if countries or regions or cities:
        # location_types Meta удалила (ошибка 100/1870194) — «проживающие,
        # путешествующие, недавно находившиеся» больше не выбираются.
        geo_locations: dict[str, Any] = {}
        if countries:
            geo_locations["countries"] = countries
        if regions:
            geo_locations["regions"] = [{"key": key} for key in regions]
        if cities:
            geo_locations["cities"] = [{"key": key} for key in cities]
        targeting["geo_locations"] = geo_locations
    excluded_geo = [str(code).upper()[:2] for code in (adset.get("excluded_geo") or []) if code]
    if excluded_geo:
        targeting["excluded_geo_locations"] = {"countries": excluded_geo}
    genders = [int(value) for value in (getattr(template, "genders", None) or []) if value in (1, 2)]
    if genders:
        targeting["genders"] = genders
    languages = [int(value) for value in (getattr(template, "languages", None) or []) if value]
    if languages:
        targeting["locales"] = languages
    interests = _interest_specs(getattr(template, "interests", None))
    if interests:
        targeting["flexible_spec"] = [{"interests": interests}]
    excluded = _interest_specs(adset.get("excluded_interests"))
    if excluded:
        targeting["exclusions"] = {"interests": excluded}
    if adset.get("advantage_audience"):
        # Advantage+ аудитория означает «ищи шире заданного», поэтому ручное
        # расширение вместе с ней не отправляется — Meta примет только одно.
        targeting["targeting_automation"] = {"advantage_audience": 1}
    elif adset.get("targeting_expansion") and interests:
        # Расширять Meta может только заданный интерес: на пустом таргете
        # ослаблять нечего, а поле она в этом случае отбивает.
        targeting["targeting_automation"] = {"advantage_audience": 1}

    settings = getattr(template, "settings", None) or {}
    advantage = bool((settings.get("campaign") or {}).get("advantage"))
    placements = getattr(template, "placements", None) or {}
    platforms = [
        platform
        for platform in (placements.get("publisher_platforms") or [])
        if platform in PUBLISHER_PLATFORMS
    ]
    if platforms and not advantage and not adset.get("auto_placements", True):
        targeting["publisher_platforms"] = platforms
        for key in ("facebook_positions", "instagram_positions", "audience_network_positions"):
            values = placements.get(key)
            if values:
                targeting[key] = list(values)
    targeting.update(_device_targeting(adset))
    return targeting


def _interest_specs(value: object) -> list[dict]:
    return [
        {"id": str(item.get("id")), "name": str(item.get("name") or "")}
        for item in (value or [])
        if isinstance(item, dict) and item.get("id")
    ]


def _device_targeting(adset: dict) -> dict:
    """Устройства, ОС и Wi-Fi одним куском.

    Версии ОС уходят строкой вида `Android_10.0`: именно так их ждёт
    `user_os`, отдельного поля под диапазон в Graph API нет.
    """
    result: dict[str, Any] = {}
    devices = str(adset.get("devices") or "all")
    if devices == "desktop":
        result["device_platforms"] = ["desktop"]
    elif devices == "mobile":
        result["device_platforms"] = ["mobile"]

    os_choice = str(adset.get("os") or "all")
    user_os: list[str] = []
    user_device: list[str] = []
    if os_choice in {"all", "android"} and devices != "desktop":
        user_os.append(_os_version("Android", adset.get("android_min")))
        if adset.get("android_smartphone", True):
            user_device.append("Android_Smartphone")
        if adset.get("android_tablet", True):
            user_device.append("Android_Tablet")
    if os_choice in {"all", "ios"} and devices != "desktop":
        user_os.append(_os_version("iOS", adset.get("ios_min")))
        for flag, name in (("ios_iphone", "iPhone"), ("ios_ipad", "iPad"), ("ios_ipod", "iPod")):
            if adset.get(flag, True):
                user_device.append(name)
    if os_choice != "all":
        # Список ОС отправляем только когда выбор сделан: «все» для Meta это
        # отсутствие поля, а перечисление обеих ОС сузило бы охват до мобильных.
        result["user_os"] = user_os
    elif any(adset.get(key) for key in ("android_min", "ios_min")):
        result["user_os"] = user_os
    if user_device and len(user_device) < 5 and devices != "desktop":
        result["user_device"] = user_device
    if adset.get("wifi_only"):
        result["wireless_carrier"] = ["Wifi"]
    return result


def _os_version(family: str, minimum: object) -> str:
    """`iOS_ver_13.0_and_above` — именно в таком виде Meta ждёт версию в
    `user_os`. Без `_ver_` она отбивает адсет ошибкой 100."""
    version = str(minimum or "").strip()
    return f"{family}_ver_{version}_and_above" if version else family


def build_asset_feed_spec(
    texts: list[dict],
    creatives: list[object],
    *,
    cta: str,
    locale_ids: dict[str, int] | None = None,
) -> dict:
    """Мультиязычное объявление одним креативом.

    Meta показывает зрителю текст на его языке сама, если дать ей набор
    вариантов и правило соответствия. Поэтому языков много, а объявление в
    кабинете одно — по объявлению на язык означало бы делить бюджет между
    ними и учиться на каждом отдельно.

    Правила Meta принимает только как `customization_spec.locales` с
    числовыми ID словаря adlocale (коды вроде `en_US` она отбивает кодом 100),
    плюс одно правило обязательно помечается `is_default` — запасной вариант
    для тех, чей язык не попал ни в одно правило.

    У каждого языка может быть свой креатив (`texts[i]["creatives"]` —
    картинки/видео именно этого языка): тогда все ассеты помечаются
    `adlabels` этого языка, а правило получает `image_label`/`video_label`.
    Если своих креативов нет ни у кого — общий пул без меток, как раньше.
    """
    per_text = any(item.get("creatives") for item in texts)
    if per_text and any(not item.get("creatives") for item in texts):
        raise ValueError(
            "У части языков не задан свой креатив: либо у всех, либо общий"
        )
    titles, bodies, descriptions, links, rules = [], [], [], [], []
    images, videos = [], []
    for index, item in enumerate(texts):
        # Метка языка — его код («en_US»), а не номер: числовую строку Meta
        # читает как id и отвечает «Label's id or name has to be present».
        label = str(item.get("language") or "").strip() or f"text_{index}"
        titles.append({"text": str(item.get("headline") or ""), "adlabels": [{"name": label}]})
        bodies.append({"text": str(item.get("primary_text") or ""), "adlabels": [{"name": label}]})
        descriptions.append(
            {"text": str(item.get("description") or ""), "adlabels": [{"name": label}]}
        )
        links.append(
            {
                "website_url": str(item.get("link_url") or ""),
                "adlabels": [{"name": label}],
            }
        )
        pool = item.get("creatives") or creatives
        pool_images, pool_videos = [], []
        for creative in pool:
            asset = (
                {
                    "video_id": str(getattr(creative, "external_id", "") or ""),
                    "thumbnail_url": str(
                        getattr(creative, "thumbnail_url", "") or ""
                    ),
                }
                if str(getattr(creative, "kind", "image")) == "video"
                else {"hash": str(getattr(creative, "external_hash", "") or "")}
            )
            if per_text:
                asset["adlabels"] = [{"name": label}]
            if str(getattr(creative, "kind", "image")) == "video":
                pool_videos.append(asset)
            else:
                pool_images.append(asset)
        # Общий пул складываем один раз, свои — с лейблом языка.
        if per_text or index == 0:
            images.extend(pool_images)
            videos.extend(pool_videos)
        language = str(item.get("language") or "").strip()
        if not language:
            continue
        locale_id = (locale_ids or {}).get(language)
        if locale_id is None:
            raise ValueError(
                "Не удалось определить числовой ID языка "
                f"«{item.get('language_name') or language}» для правил показа"
            )
        rule = {
            "customization_spec": {"locales": [int(locale_id)]},
            "title_label": {"name": label},
            "body_label": {"name": label},
            "description_label": {"name": label},
            "link_url_label": {"name": label},
            "is_default": index == 0,
        }
        # По одному изображению без метки работает на всех («общий креатив»),
        # в per-language режиме каждое правило ссылается на картинку языка.
        if per_text and pool_images:
            rule["image_label"] = {"name": label}
        elif per_text and pool_videos:
            rule["video_label"] = {"name": label}
        rules.append(rule)
    spec: dict[str, Any] = {
        "titles": titles,
        "bodies": bodies,
        "descriptions": descriptions,
        "link_urls": links,
        "call_to_action_types": [cta],
        "ad_formats": ["SINGLE_IMAGE"],
    }
    if videos:
        spec["videos"] = videos
        spec["ad_formats"] = ["SINGLE_VIDEO"]
    if images:
        spec["images"] = images
    if rules:
        spec["asset_customization_rules"] = rules
    return spec


def build_object_story_spec(
    launch: object, creative: object, *, page_id: str, caption: str | None = None
) -> dict:
    """object_story_spec для одного креатива — картинка или видео."""
    link = str(getattr(launch, "link_url", "") or "")
    call_to_action = {
        "type": str(getattr(launch, "call_to_action", "") or "LEARN_MORE"),
        "value": {"link": link},
    }
    common = {
        "message": str(getattr(launch, "primary_text", "") or ""),
        "name": str(getattr(launch, "headline", "") or ""),
        "description": str(getattr(launch, "description", "") or ""),
        "call_to_action": call_to_action,
    }
    if str(getattr(creative, "kind", "image")) == "video":
        video_data = {
            "video_id": str(getattr(creative, "external_id", "") or ""),
            "message": common["message"],
            "title": common["name"],
            "link_description": common["description"],
            "call_to_action": call_to_action,
        }
        thumbnail = getattr(creative, "thumbnail_url", None)
        if thumbnail:
            video_data["image_url"] = str(thumbnail)
        return {"page_id": page_id, "video_data": video_data}
    link_data = {
        **common,
        "link": link,
        "image_hash": str(getattr(creative, "external_hash", "") or ""),
    }
    if caption:
        # Отображаемый URL: в объявлении видно его, а ведёт ссылка всё равно
        # на трекер.
        link_data["caption"] = str(caption)
    return {"page_id": page_id, "link_data": link_data}


def campaign_id_macro_url(link: str, sub_id: int | None) -> str:
    """Дописать в ссылку sub_id с ID кампании, если его там ещё нет.

    Без этого параметра залив попадёт в Meta, но доход по нему из Keitaro не
    подтянется — а заметят это только через сутки, по пустому ROI.
    """
    if not link or not sub_id:
        return link
    param = f"sub_id_{sub_id}"
    if param in link:
        return link
    separator = "&" if "?" in link else "?"
    return f"{link}{separator}{param}={{{{campaign.id}}}}"


def action_counts(actions: object) -> tuple[int, int, dict]:
    """Лиды и покупки пикселя плюс сырой срез действий для отладки."""
    if not isinstance(actions, list):
        return 0, 0, {}
    totals: dict[str, int] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type") or "")
        if not action_type:
            continue
        totals[action_type] = totals.get(action_type, 0) + integer_value(action.get("value"))
    leads = sum(count for name, count in totals.items() if name in LEAD_ACTION_TYPES)
    purchases = sum(count for name, count in totals.items() if name in PURCHASE_ACTION_TYPES)
    return leads, purchases, totals


def hour_of(row: dict) -> int | None:
    """Час из разбивки Meta.

    Значение приходит строкой-диапазоном «13:00:00 - 13:59:59»; берём начало.
    """
    raw = str(row.get(HOURLY_BREAKDOWN) or "").strip()
    if not raw:
        return None
    head = raw.split("-", 1)[0].strip()
    try:
        hour = int(head.split(":", 1)[0])
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


def creative_post_id(row: dict) -> str | None:
    """Пост объявления — «<page_id>_<post_id>».

    Именно под ним живут комментарии. У объявления без органического поста
    (например, собранного из каталога) его нет, и это нормально: чистить там
    нечего.
    """
    creative = row.get("creative")
    if not isinstance(creative, dict):
        return None
    story = str(creative.get("effective_object_story_id") or "")
    return story[:120] if "_" in story else None


def creative_page_id(row: dict) -> str | None:
    """Фан-пейдж объявления.

    Meta отдаёт его в двух местах и не всегда в обоих: в `object_story_spec`
    прямым полем и в `effective_object_story_id` как префикс до подчёркивания.
    """
    creative = row.get("creative")
    if not isinstance(creative, dict):
        return None
    spec = creative.get("object_story_spec")
    if isinstance(spec, dict) and spec.get("page_id"):
        return str(spec["page_id"])[:100]
    story = str(creative.get("effective_object_story_id") or "")
    page = story.split("_", 1)[0]
    return page[:100] or None


def _json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _error_message(error: dict) -> str:
    code = _int_or_none(error.get("code"))
    message = error.get("error_user_msg") or error.get("message") or ""
    clean = " ".join(str(message).split())[:240]
    hint = ERROR_HINTS.get(code) if code is not None else None
    if hint:
        # Общий текст — для человека; точную фразу Meta — для понимания причины.
        if clean and clean.lower() not in hint.lower():
            return f"{hint} — Meta: «{clean}»"
        return hint
    return f"Meta вернула ошибку (код {code}): {clean}" if code else f"Meta вернула ошибку: {clean}"
