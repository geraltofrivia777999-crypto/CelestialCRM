"""Клиент Partner Integration Service — факты «дата — оффер — тег — депозиты».

Сервис живёт на том же сервере и отдаёт уже нормализованные данные по своим
интеграциям с партнёрками. Авторизация — общий `X-API-Key`; ошибки сервиса
заворачиваются в `PartnerIntegrationError` с готовым для UI текстом.
"""

from datetime import date
from decimal import Decimal

import httpx

from app.core.security import decrypt_secret


class PartnerIntegrationError(Exception):
    """Сбой обращения к сервису партнёрок — текст готов для UI."""


def partner_client(integration, **overrides) -> httpx.AsyncClient:
    """httpx-клиент с ключом интеграции (расшифровка на месте)."""
    key = overrides.get("api_key") or decrypt_secret(integration.api_key_encrypted)
    return httpx.AsyncClient(
        base_url=str(integration.base_url).rstrip("/"),
        headers={"X-API-Key": key},
        timeout=overrides.get("timeout", 60.0),
        transport=overrides.get("transport"),
    )


class PartnerServiceClient:
    """Тонкая обёртка над HTTP API сервиса партнёрок."""

    def __init__(
        self,
        integration,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = str(integration.base_url).rstrip("/")
        key = api_key or decrypt_secret(integration.api_key_encrypted)
        self.headers = {"X-API-Key": key}
        self.timeout = timeout
        self.transport = transport

    async def _request(self, method: str, path: str, *, json_body=None, params=None):
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method, path, json=json_body, params=params
                )
        except httpx.HTTPError:
            raise PartnerIntegrationError(
                "Сервис партнёрок недоступен — проверьте, что он запущен"
            ) from None
        if response.status_code in (401, 403):
            raise PartnerIntegrationError(
                "Сервис партнёрок отклонил API-ключ — сверьте X-API-Key"
            )
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("detail") or payload.get("error") or "")
            except Exception:  # noqa: BLE001 — тело может быть не JSON
                detail = ""
            raise PartnerIntegrationError(
                detail or f"Сервис партнёрок: ошибка {response.status_code}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def stats(
        self,
        date_from: date,
        date_to: date,
        *,
        offer_id: str | None = None,
        tag: str | None = None,
    ) -> list[dict]:
        """Факты «дата — оффер CRM — тег — депозиты» за диапазон."""
        params: dict = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        if offer_id:
            params["offer_id"] = offer_id
        if tag:
            params["tag"] = tag
        data = await self._request("GET", "/api/v1/stats", params=params)
        return data if isinstance(data, list) else data.get("items", [])

    async def test_request(
        self,
        connector_config: dict,
        credentials: dict,
        *,
        external_offer_id: str,
        date_from: date,
        date_to: date,
    ) -> list[dict]:
        """Пробный запрос в реальную ПП через сервис — до сохранения конфига."""
        data = await self._request(
            "POST",
            "/api/v1/integrations/test-request",
            json_body={
                "connector_config": connector_config,
                "credentials": credentials,
                "external_offer_id": external_offer_id,
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
        )
        return (data or {}).get("records", [])

    async def integrations(self) -> list[dict]:
        """Интеграции, заведённые на самом сервисе.

        Конфиг коннектора к ПП (connector_config, credentials) — техническая
        JSON-простыня, её заводят на стороне сервиса. CRM не пересоздаёт её у
        себя, а выбирает готовую из этого списка и запоминает её id.
        """
        data = await self._request("GET", "/api/v1/integrations")
        return data if isinstance(data, list) else data.get("items", [])

    async def put_offer_mapping(
        self, integration_external_id: str, crm_offer_id: str, external_offer_id: str
    ) -> dict:
        """Привязка оффера на стороне сервиса.

        Без неё оффер не попадёт в `/stats` — так устроен сервис. Повторный
        вызов с тем же `external_offer_id` обновляет привязку, а не двоит её.
        """
        data = await self._request(
            "POST",
            f"/api/v1/integrations/{integration_external_id}/offer-mappings",
            json_body={
                "crm_offer_id": crm_offer_id,
                "external_offer_id": external_offer_id,
            },
        )
        return data or {}

    async def trigger_sync(
        self, integration_external_id: str, date_from: date, date_to: date
    ) -> dict:
        """Попросить сервис сходить в ПП за период.

        Вызов синхронный: сервис отвечает, когда синк действительно завершился.
        Без него `/stats` отдаёт только то, что сервис успел собрать своим
        расписанием, — то есть дозагрузка прошлых дат не работала бы вовсе.
        """
        data = await self._request(
            "POST",
            f"/api/v1/integrations/{integration_external_id}/sync",
            json_body={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
        )
        return data or {}

    async def sync_runs(self, integration_external_id: str, limit: int = 50) -> list[dict]:
        data = await self._request(
            "GET",
            f"/api/v1/integrations/{integration_external_id}/sync-runs",
            params={"limit": limit},
        )
        return data if isinstance(data, list) else data.get("items", [])


def decimal_value(raw) -> Decimal:
    try:
        return Decimal(str(raw))
    except Exception:  # noqa: BLE001 — ПП может прислать мусор в числе
        return Decimal("0")
