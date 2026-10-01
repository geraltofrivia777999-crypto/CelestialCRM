import asyncio
import hashlib
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

REPORT_DIMENSIONS = [
    "day",
    "campaign_id",
    "offer_id",
    "country_code",
    *[f"sub_id_{index}" for index in range(1, 11)],
]
REPORT_MEASURES = [
    "clicks",
    "campaign_unique_clicks",
    "conversions",
    "leads",
    "sales",
    "rejected",
    "cost",
    "revenue",
]
# Колонки журнала конверсий из текущего контракта Admin API. Некоторые версии
# возвращают для этих полей алиасы (`event_id`, `datetime`) — обработчик ниже
# принимает оба варианта. Неизвестная колонка роняет весь запрос, поэтому
# `payout` здесь быть не должно: сумма конверсии в журнале называется revenue.
CONVERSION_COLUMNS = [
    "conversion_id",
    "status",
    "postback_datetime",
    "click_datetime",
    "campaign",
    "campaign_id",
    "offer",
    "offer_id",
    "country",
    "revenue",
    "sub_id",
    *[f"sub_id_{index}" for index in range(1, 11)],
]
CLICK_COLUMNS = ["sub_id", "datetime"]
MAX_CONVERSION_PAGES = 100
CLICK_LOOKBACK_DAYS = 90
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
# What the tracker's HTTP code actually means for whoever is filling in the form.
# Without these the UI only ever showed "HTTP 404", which tells nobody anything.
STATUS_HINTS = {
    400: "Keitaro не принял запрос (400). Проверьте версию трекера — нужен Admin API v1.",
    401: (
        "Keitaro отклонил API-ключ (401). Возьмите ключ в трекере: "
        "Администратор → Настройки → API."
    ),
    403: (
        "Keitaro запретил доступ (403). У ключа нет прав на Admin API "
        "либо IP сервера не в белом списке трекера."
    ),
    404: (
        "По этому адресу нет Admin API Keitaro (404). Чаще всего URL ведёт на домен "
        "для трафика, а не на панель трекера — укажите адрес, по которому вы "
        "открываете саму панель Keitaro."
    ),
}


class KeitaroError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class KeitaroClient:
    """Small, retry-aware client for the Keitaro Admin API v1."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 45.0,
        max_attempts: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Api-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = timeout
        self.max_attempts = max(max_attempts, 1)
        self.transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    headers=self.headers,
                    timeout=self.timeout,
                    transport=self.transport,
                ) as client:
                    response = await client.request(method, path, json=json, params=params)
                if response.status_code in RETRYABLE_STATUSES and attempt < self.max_attempts:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise KeitaroError(
                    _status_message(status, exc.response, self.base_url),
                    status_code=status,
                    retryable=status in RETRYABLE_STATUSES,
                ) from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
                    continue
            except ValueError as exc:
                raise KeitaroError("Keitaro returned an invalid JSON response") from exc

        raise KeitaroError(
            f"Keitaro не отвечает по адресу {self.base_url} "
            f"({self.max_attempts} попытки). Проверьте, что домен доступен с этого сервера.",
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

    async def check(self) -> bool:
        await self._request("GET", "/admin_api/v1/offers")
        return True

    async def offers(self) -> list[dict]:
        return _rows(await self._request("GET", "/admin_api/v1/offers"))

    async def affiliate_networks(self) -> list[dict]:
        return _rows(await self._request("GET", "/admin_api/v1/affiliate_networks"))

    async def campaigns(self) -> list[dict]:
        return _rows(
            await self._request(
                "GET",
                "/admin_api/v1/campaigns",
                params={"limit": 10000, "offset": 0},
            )
        )

    async def groups(self, resource_type: str) -> list[dict]:
        if resource_type not in {"campaigns", "offers"}:
            raise ValueError("Unsupported Keitaro group type")
        return _rows(
            await self._request(
                "GET",
                "/admin_api/v1/groups",
                params={"type": resource_type},
            )
        )

    async def report(
        self,
        start: date,
        end: date,
        *,
        timezone: str = "Europe/Moscow",
    ) -> list[dict]:
        payload = {
            "range": {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "timezone": timezone,
            },
            "dimensions": REPORT_DIMENSIONS,
            "measures": REPORT_MEASURES,
            "sort": [{"name": "day", "order": "ASC"}],
        }
        return _rows(
            await self._request("POST", "/admin_api/v1/report/build", json=payload)
        )

    async def meta_name_report(self, start: date, end: date, *, timezone: str) -> list[dict]:
        """FB name macros, grouped in the ad account's own timezone."""
        return _rows(await self._request("POST", "/admin_api/v1/report/build", json={
            "range": {"from": start.isoformat(), "to": end.isoformat(), "timezone": timezone},
            "dimensions": ["country_code", "sub_id_2", "sub_id_3", "sub_id_4"],
            "measures": ["clicks", "campaign_unique_clicks", "leads", "sales"],
        }))


    async def conversions(
        self,
        start: date,
        end: date,
        *,
        timezone: str = "Europe/Moscow",
        limit: int = 1000,
    ) -> list[dict]:
        """Журнал конверсий за период — по строке на конверсию.

        Дневной отчёт для уведомления о депозите не годится: он знает «три
        продажи за день», а в сообщении нужен конкретный депозит — его время
        клика и его sub_id.

        Набор колонок у разных версий трекера отличается, поэтому мы просим
        то, что есть везде, и разбираем ответ по факту, а не по ожиданиям.
        """
        page_size = max(min(limit, 1000), 1)
        rows: list[dict] = []
        offset = 0

        for _ in range(MAX_CONVERSION_PAGES):
            payload = {
                "range": {
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "timezone": timezone,
                },
                "columns": CONVERSION_COLUMNS,
                "filters": [],
                "sort": [{"name": "postback_datetime", "order": "DESC"}],
                "limit": page_size,
                "offset": offset,
            }
            data = await self._request(
                "POST", "/admin_api/v1/conversions/log", json=payload
            )
            page = _rows(data)
            rows.extend(page)
            offset += len(page)

            total = _total(data)
            if not page or (total is not None and offset >= total):
                break
            # Старые версии могут не вернуть total. В этом случае короткая
            # страница является последней.
            if total is None and len(page) < page_size:
                break

        return rows

    async def click_times(
        self,
        sub_ids: list[str],
        *,
        start: date,
        end: date,
        timezone: str = "Europe/Moscow",
    ) -> dict[str, str]:
        """Время исходного клика для конверсий.

        Некоторые сборки Keitaro принимают колонку ``click_datetime`` в
        журнале конверсий, но не возвращают её. Корневой ``sub_id`` там есть,
        а журнал кликов надёжно находит по нему исходный ``datetime``. Запросы
        идут пачками, чтобы один всплеск депозитов не превратился в сотни
        обращений к трекеру.
        """
        unique = list(dict.fromkeys(value for value in sub_ids if value))
        found: dict[str, str] = {}
        batch_size = 200

        for batch_offset in range(0, len(unique), batch_size):
            batch = unique[batch_offset : batch_offset + batch_size]
            offset = 0
            while True:
                payload = {
                    "range": {
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "timezone": timezone,
                    },
                    "columns": CLICK_COLUMNS,
                    "filters": [
                        {
                            "name": "sub_id",
                            "operator": "IN_LIST",
                            "expression": batch,
                        }
                    ],
                    "limit": min(max(len(batch), 1), 1000),
                    "offset": offset,
                }
                data = await self._request(
                    "POST", "/admin_api/v1/clicks/log", json=payload
                )
                page = _rows(data)
                for row in page:
                    sub_id = str(row.get("sub_id") or "").strip()
                    moment = str(row.get("datetime") or "").strip()
                    if sub_id and moment:
                        found[sub_id] = moment
                offset += len(page)
                total = _total(data)
                if not page or (total is not None and offset >= total):
                    break
                if total is None and len(page) < payload["limit"]:
                    break

        return found


def stat_dimension_key(row: dict) -> str:
    values = [
        str(row.get("day") or row.get("datetime") or row.get("date") or ""),
        str(row.get("campaign_id") or ""),
        str(row.get("offer_id") or ""),
        str(row.get("country_code") or row.get("country") or ""),
        *[str(row.get(f"sub_id_{index}") or "") for index in range(1, 11)],
    ]
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


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


def _rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("rows", [])
        return [row for row in rows if isinstance(row, dict)]
    return []


def _total(data: Any) -> int | None:
    if not isinstance(data, dict) or data.get("total") in (None, ""):
        return None
    try:
        return max(int(data["total"]), 0)
    except (TypeError, ValueError):
        return None


def _status_message(status: int, response: httpx.Response, base_url: str) -> str:
    """A message the person editing the connection can act on.

    The tracker's own wording is appended when it says something useful, but it is
    never the whole message: Keitaro answers a wrong URL with a bare 404 page.
    """
    hint = STATUS_HINTS.get(status)
    if hint is None:
        hint = f"Keitaro ответил HTTP {status} на {base_url}."
    detail = _safe_error_detail(response)
    return f"{hint}{detail}"


def _safe_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("error") or payload.get("message")
    if isinstance(raw, dict):
        raw = raw.get("message")
    if isinstance(raw, str):
        clean = " ".join(raw.split())[:240]
        return f": {clean}" if clean else ""
    return ""
