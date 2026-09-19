"""Клиент Partner Integration Service — факты «дата — оффер — тег — депозиты».

Сервис живёт на том же сервере и отдаёт уже нормализованные данные по своим
интеграциям с партнёрками. Авторизация — общий `X-API-Key`; ошибки сервиса
заворачиваются в `PartnerIntegrationError` с готовым для UI текстом.

Адрес сервиса и ключ к нему — настройка развёртывания (`PARTNER_SERVICE_*` в
`.env`), а не поле карточки: сервис у CRM один, и вводить его адрес в каждой
интеграции значило бы предлагать человеку ошибиться. В карточке живут доступы
к самой партнёрской программе — их CRM передаёт сервису при создании
интеграции, а дальше в ПП ходит он сам.
"""

from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


class PartnerIntegrationError(Exception):
    """Сбой обращения к сервису партнёрок — текст готов для UI."""


def normalize_partner_url(raw: str) -> str:
    """Проверить адрес API ПП до того, как он попадёт в сервис-коннектор."""
    value = str(raw or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Адрес API должен начинаться с http:// или https://")
    return value


def platform_template_id(platform: str) -> int | None:
    """Номер шаблона коннектора по названию платформы."""
    return settings.partner_platform_templates.get(str(platform or "").strip().lower())


class PartnerServiceClient:
    """Тонкая обёртка над HTTP API сервиса партнёрок.

    Интеграция аргументом больше не нужна: сервис один на всю CRM, и адрес с
    ключом берутся из настроек. Аргумент оставлен необязательным — вызовы вида
    `PartnerServiceClient(integration)` в коде и тестах продолжают работать.
    """

    def __init__(
        self,
        integration=None,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = str(settings.partner_service_base_url).rstrip("/")
        self.headers = {"X-API-Key": api_key or settings.partner_service_token}
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
        integration_id: str | int | None = None,
    ) -> list[dict]:
        """Факты «дата — оффер CRM — тег — депозиты» за диапазон."""
        params: dict = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        if integration_id is not None:
            params["integration_id"] = integration_id
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

    async def create_integration(
        self,
        *,
        partner_name: str,
        name: str,
        template_integration_id: int,
        base_url: str,
        api_key: str,
    ) -> dict:
        """Завести интеграцию на сервисе по готовому шаблону платформы.

        Конфиг коннектора руками не собирается: сервис держит шаблоны под
        числовыми id, и CRM только называет платформу и передаёт доступы к
        конкретной ПП. Ответ содержит id, по которому дальше просят синк и
        заводят привязки офферов.
        """
        data = await self._request(
            "POST",
            "/api/v1/integrations",
            json_body={
                "partner_name": partner_name,
                "name": name,
                "template_integration_id": template_integration_id,
                "base_url": base_url,
                "credentials": {"api_key": api_key},
            },
        )
        return data or {}

    async def integrations(self) -> list[dict]:
        """Интеграции, заведённые на самом сервисе.

        Конфиг коннектора к ПП (connector_config, credentials) — техническая
        JSON-простыня, её заводят на стороне сервиса. CRM не пересоздаёт её у
        себя, а выбирает готовую из этого списка и запоминает её id.
        """
        data = await self._request("GET", "/api/v1/integrations")
        return data if isinstance(data, list) else data.get("items", [])

    async def integration(self, integration_external_id: str) -> dict:
        """Прочитать полный конфиг интеграции, чтобы безопасно обновить его."""
        data = await self._request(
            "GET", f"/api/v1/integrations/{integration_external_id}"
        )
        return data or {}

    async def update_integration(
        self,
        integration_external_id: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> dict:
        """Передать изменённые доступы в сервис, сохранив его шаблон коннектора."""
        payload: dict = {}
        if base_url is not None:
            current = await self.integration(integration_external_id)
            connector_config = current.get("connector_config")
            if not isinstance(connector_config, dict):
                raise PartnerIntegrationError(
                    "Сервис партнёрок не вернул конфигурацию интеграции"
                )
            connector_config = dict(connector_config)
            connector_config["base_url"] = base_url
            payload["connector_config"] = connector_config
        if api_key is not None:
            payload["credentials"] = {"api_key": api_key}
        if not payload:
            return {}
        data = await self._request(
            "PATCH",
            f"/api/v1/integrations/{integration_external_id}",
            json_body=payload,
        )
        return data or {}

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
