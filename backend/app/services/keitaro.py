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
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


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
                detail = _safe_error_detail(exc.response)
                raise KeitaroError(
                    f"Keitaro Admin API returned HTTP {status}{detail}",
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
            f"Keitaro is unavailable after {self.max_attempts} attempts",
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
        timezone: str = "UTC",
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
