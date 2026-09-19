import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.main import app
from app.models import IntegrationConnection, KeitaroGroup, Status, User
from app.services.keitaro import KeitaroError

GROUPS = "/api/v1/integrations/keitaro/groups"


def _fake_client(campaign_group: str, offer_group: str):
    class FakeGroupsClient:
        """Трекер, в котором CRM только что открыли доступ к новой группе."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def affiliate_networks(self) -> list[dict]:
            return []

        async def groups(self, resource_type: str) -> list[dict]:
            if resource_type == "campaigns":
                return [{"id": 9301, "name": campaign_group}]
            return [{"id": 9302, "name": offer_group}]

        async def offers(self) -> list[dict]:
            return []

        async def campaigns(self) -> list[dict]:
            return []

    return FakeGroupsClient


class BrokenClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def affiliate_networks(self) -> list[dict]:
        raise KeitaroError("Keitaro отклонил ключ API")

    async def groups(self, resource_type: str) -> list[dict]:
        return []

    async def offers(self) -> list[dict]:
        return []

    async def campaigns(self) -> list[dict]:
        return []


async def test_team_pulls_a_newly_shared_keitaro_group(database, monkeypatch) -> None:
    """Группу, которой только что дали доступ в Keitaro, видно в команде сразу.

    Остальные подключения воркспейса на время теста выключены: пустые
    справочники из фейкового трекера не должны задеть данные соседних тестов.
    """
    from app.api.routers import integrations

    suffix = uuid.uuid4().hex[:8]
    campaign_group, offer_group = f"CRM buyer {suffix}", f"CRM offers {suffix}"
    stale_group = f"CRM stale {suffix}"
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        others = list(
            (
                await db.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.workspace_id == admin.workspace_id,
                        IntegrationConnection.kind == "keitaro",
                        IntegrationConnection.status == Status.active,
                    )
                )
            ).scalars()
        )
        paused = [row.id for row in others]
        for row in others:
            row.status = Status.inactive
        connection = IntegrationConnection(
            workspace_id=admin.workspace_id,
            name=f"Groups Keitaro {suffix}",
            base_url="https://tracker.example",
            api_key_encrypted=encrypt_secret("test-key"),
        )
        db.add(connection)
        await db.flush()
        db.add(
            KeitaroGroup(
                workspace_id=admin.workspace_id,
                connection_id=connection.id,
                resource_type="campaigns",
                external_id=f"stale-{suffix}",
                name=stale_group,
            )
        )
        await db.commit()
        connection_id = connection.id

    try:
        with TestClient(app) as client:
            assert client.get(GROUPS).status_code == 401
            assert client.post(f"{GROUPS}/refresh").status_code == 401
            login = client.post(
                "/api/v1/auth/login", json={"login": "admin", "password": "test-password"}
            )
            assert login.status_code == 200

            monkeypatch.setattr(
                integrations, "KeitaroClient", _fake_client(campaign_group, offer_group)
            )
            refreshed = client.post(f"{GROUPS}/refresh")
            assert refreshed.status_code == 200, refreshed.text
            payload = refreshed.json()
            assert payload["new_campaign_groups"] == [campaign_group]
            assert payload["new_offer_groups"] == [offer_group]
            assert campaign_group in payload["campaign_groups"]

            # Группа попадает в список формы сразу, хотя кампаний в ней ещё нет.
            assert offer_group in payload["offer_groups"]

            # Обычная загрузка формы читает сам актуальный справочник: пустая
            # новая группа видна, удалённая из Keitaro — уже нет.
            listed = client.get(GROUPS)
            assert listed.status_code == 200
            assert campaign_group in listed.json()["campaign_groups"]
            assert offer_group in listed.json()["offer_groups"]
            assert stale_group not in listed.json()["campaign_groups"]

            # Второй раз та же группа уже не «новая», но в списке остаётся.
            again = client.post(f"{GROUPS}/refresh").json()
            assert again["new_campaign_groups"] == []
            assert again["new_offer_groups"] == []
            assert campaign_group in again["campaign_groups"]

            monkeypatch.setattr(integrations, "KeitaroClient", BrokenClient)
            broken = client.post(f"{GROUPS}/refresh")
            assert broken.status_code == 422
            assert "ключ" in broken.json()["error"]["message"]
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(KeitaroGroup).where(KeitaroGroup.connection_id == connection_id)
            )
            await db.execute(
                delete(IntegrationConnection).where(IntegrationConnection.id == connection_id)
            )
            for row_id in paused:
                row = await db.get(IntegrationConnection, row_id)
                if row:
                    row.status = Status.active
            await db.commit()
