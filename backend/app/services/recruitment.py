"""Клиент Recruitment Service — шаблоны поиска HH, запуски, кандидаты.

Сервис развёрнут рядом (та же VPS, docker-сеть `recruitmentservice_internal`) и
владеет своими данными: CRM ничего у себя не хранит, а только ходит в его API
с общим секретом. Ошибки сервиса наружу отдаём как 502 с человеческим текстом —
фронтенд показывает их в разделе, а не валит страницу.
"""

from typing import Any

import httpx

from app.core.config import settings


class RecruitmentError(Exception):
    """Готовый для UI текст сбоя интеграции с сервисом рекрутинга."""


class RecruitmentClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = (base_url or settings.recruitment_api_base).rstrip("/")
        self.token = token if token is not None else settings.recruitment_service_token
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        if not self.token:
            raise RecruitmentError("Сервис рекрутинга не настроен: нет токена доступа")
        headers = {"Authorization": f"Bearer {self.token}"}
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
            transport=self.transport,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        try:
            async with self._client() as client:
                response = await client.request(method, path, params=params, json=json)
        except RecruitmentError:
            raise
        except httpx.HTTPError:
            raise RecruitmentError("Сервис рекрутинга недоступен") from None
        if response.status_code in (401, 403):
            raise RecruitmentError("Сервис рекрутинга отклонил токен доступа")
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("detail") or "")
            except Exception:
                detail = ""
            raise RecruitmentError(detail or f"Сервис рекрутинга: ошибка {response.status_code}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # --- Шаблоны поиска ---

    async def list_templates(self) -> list[dict]:
        data = await self._request("GET", "/search-templates")
        return data if isinstance(data, list) else data.get("items", [])

    async def create_template(self, payload: dict) -> dict:
        return await self._request("POST", "/search-templates", json=payload)

    async def update_template(self, template_id: str, payload: dict) -> dict:
        return await self._request(
            "PUT", f"/search-templates/{template_id}", json=payload
        )

    async def delete_template(self, template_id: str) -> None:
        await self._request("DELETE", f"/search-templates/{template_id}")

    async def run_template(self, template_id: str) -> dict:
        return await self._request(
            "POST", f"/search-templates/{template_id}/run"
        )

    # --- Запуски ---

    async def get_run(self, run_id: str) -> dict:
        return await self._request("GET", f"/search-runs/{run_id}")

    async def list_runs(self, template_id: str | None = None) -> list[dict]:
        params = {"search_template_id": template_id} if template_id else None
        data = await self._request("GET", "/search-runs", params=params)
        return data if isinstance(data, list) else data.get("items", [])

    # --- Кандидаты ---

    async def list_candidates(
        self,
        *,
        source: str | None = None,
        template_id: str | None = None,
        min_score: int | None = None,
        review_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        params: dict = {"limit": limit, "offset": offset}
        if source:
            params["source"] = source
        if template_id:
            params["search_template_id"] = template_id
        if min_score is not None:
            params["min_score"] = min_score
        if review_status:
            params["review_status"] = review_status
        data = await self._request("GET", "/external-candidates", params=params)
        return data if isinstance(data, list) else data.get("items", [])

    async def review_candidate(
        self, candidate_id: str, decision: str, reviewed_by: str | None
    ) -> dict:
        payload: dict = {"decision": decision}
        if reviewed_by:
            payload["reviewed_by"] = reviewed_by
        return await self._request(
            "PATCH", f"/external-candidates/{candidate_id}/review", json=payload
        )

    # --- HH ---

    async def hh_status(self) -> dict:
        return await self._request("GET", "/providers/hh/status")

    async def hh_connect(self) -> dict:
        return await self._request("POST", "/providers/hh/connect")


def recruitment_client() -> RecruitmentClient:
    return RecruitmentClient()
