import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models import (
    IntegrationConnection,
    MetaAdAccount,
    MetaEntity,
    MetaGeoRule,
    MetaGeoRuleEvent,
    MetaGeoRuleSet,
    MetaStatDaily,
    User,
)
from app.services.meta import MetaError
from app.services.meta_geo_rules import check_rule, is_due
from tests.test_meta_entity_actions import _admin

BASE = "/api/v1/meta/geo-rules"


def _rule(**limits):
    values = {key: None for key in (
        "no_clicks", "no_insts", "no_regs", "no_deps", "max_avg_inst", "max_avg_reg", "max_avg_dep",
    )}
    values.update({key: Decimal(str(value)) for key, value in limits.items()})
    return SimpleNamespace(**values)


def test_check_rule_zero_and_average_checks() -> None:
    rule = _rule(no_clicks=3, no_deps=5, max_avg_inst=2)
    idle = {"spend_usd": Decimal("3"), "clicks": 0, "insts": 0, "regs": 0, "deps": None}
    assert [key for key, _ in check_rule(rule, idle)] == ["no_clicks"]
    # Депозиты неизвестны (адсет, нет атрибуции) — проверка молчит, а не срабатывает.
    assert "no_deps" not in [key for key, _ in check_rule(rule, idle)]
    cheap = {"spend_usd": Decimal("2.99"), "clicks": 0, "insts": 0, "regs": 0, "deps": 0}
    assert check_rule(rule, cheap) == []
    pricey = {"spend_usd": Decimal("10"), "clicks": 40, "insts": 4, "regs": 1, "deps": 0}
    hits = dict(check_rule(rule, pricey))
    assert set(hits) == {"no_deps", "max_avg_inst"}
    assert hits["max_avg_inst"] == "AvgInst 2,50 $ выше 2,00 $"


def test_is_due_respects_interval_and_toggle() -> None:
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    config = SimpleNamespace(auto_enabled=True, interval_minutes=30, last_run_at=None)
    assert is_due(config, now)
    config.last_run_at = now - timedelta(minutes=29, seconds=30)
    assert is_due(config, now)
    config.last_run_at = now - timedelta(minutes=10)
    assert not is_due(config, now)
    config.auto_enabled = False
    config.last_run_at = None
    assert not is_due(config, now)


class FakeClient:
    def __init__(self) -> None:
        self.paused: list[str] = []

    async def set_status(self, external_id: str, status: str) -> dict:
        if external_id == "c-broken":
            raise MetaError("Meta: объект недоступен")
        self.paused.append(external_id)
        return {"success": True}


@pytest.fixture
async def geo_setup(database, monkeypatch):
    from app.api.routers import meta as meta_router

    fake = FakeClient()

    async def fake_client_for(connection, db=None):
        return fake

    monkeypatch.setattr(meta_router, "client_for", fake_client_for)
    suffix = uuid.uuid4().hex[:6]
    today = datetime.now(UTC).date()
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        workspace_id = admin.workspace_id
        connection = IntegrationConnection(
            workspace_id=workspace_id, owner_id=admin.id, name=f"Geo {suffix}", kind="meta",
            base_url="https://graph.facebook.com/v23.0",
            api_key_encrypted=encrypt_secret("meta-geo-token-long-enough"),
        )
        db.add(connection)
        await db.flush()
        account = MetaAdAccount(
            workspace_id=workspace_id, connection_id=connection.id, external_id=f"act_{suffix}",
            name="Geo cabinet", currency="USD", timezone_name="UTC", owner_id=admin.id,
        )
        db.add(account)
        await db.flush()
        campaigns = {
            # id: (статус, GEO, spend, clicks, installs)
            "c-idle": ("ACTIVE", "IN", "5", 0, 0),
            "c-ok": ("ACTIVE", "IN", "5", 10, 5),
            "c-pricey": ("ACTIVE", "IN", "10", 30, 2),
            "c-mx": ("ACTIVE", "MX", "50", 0, 0),
            "c-paused": ("PAUSED", "IN", "9", 0, 0),
            "c-broken": ("ACTIVE", "IN", "8", 0, 0),
        }
        for external_id, (status, geo, spend, clicks, installs) in campaigns.items():
            db.add(MetaEntity(
                workspace_id=workspace_id, connection_id=connection.id, account_id=account.id,
                level="campaign", external_id=external_id, name=f"Campaign {external_id}",
                effective_status=status, external_payload={},
            ))
            db.add(MetaStatDaily(
                workspace_id=workspace_id, connection_id=connection.id, account_id=account.id,
                record_date=today, campaign_external_id=external_id,
                adset_external_id=external_id + "-s", ad_external_id=external_id + "-a",
                country_code=geo, dimension_key=f"geo-{suffix}-{external_id}",
                spend=Decimal(spend), clicks=clicks,
                actions={"omni_app_install": installs} if installs else {},
            ))
        # Вчерашний расход не считается: правила смотрят только сегодня.
        db.add(MetaStatDaily(
            workspace_id=workspace_id, connection_id=connection.id, account_id=account.id,
            record_date=today - timedelta(days=1), campaign_external_id="c-ok",
            country_code="IN", dimension_key=f"geo-{suffix}-old", spend=Decimal("500"),
        ))
        await db.commit()
        made = {"workspace": workspace_id, "connection": connection.id, "account": account.id}
    yield fake, made
    async with SessionLocal() as db:
        await db.execute(delete(MetaGeoRuleEvent).where(
            MetaGeoRuleEvent.workspace_id == made["workspace"]))
        await db.execute(delete(MetaGeoRule).where(MetaGeoRule.workspace_id == made["workspace"]))
        await db.execute(delete(MetaGeoRuleSet).where(
            MetaGeoRuleSet.workspace_id == made["workspace"]))
        await db.execute(delete(MetaStatDaily).where(
            MetaStatDaily.connection_id == made["connection"]))
        await db.execute(delete(MetaEntity).where(MetaEntity.connection_id == made["connection"]))
        await db.execute(delete(MetaAdAccount).where(MetaAdAccount.id == made["account"]))
        await db.execute(delete(IntegrationConnection).where(
            IntegrationConnection.id == made["connection"]))
        await db.commit()


async def _status(external_id: str, connection_id) -> str:
    async with SessionLocal() as db:
        return (await db.scalar(select(MetaEntity).where(
            MetaEntity.connection_id == connection_id, MetaEntity.external_id == external_id,
        ))).effective_status


async def test_run_now_pauses_matching_objects_and_logs_history(geo_setup) -> None:
    fake, made = geo_setup
    client = _admin()
    try:
        assert client.get(BASE).json()["items"] == []
        created = client.post(BASE, json={"name": " Индия стоп ", "level": "campaign"})
        assert created.status_code == 201, created.text
        rule_set = created.json()
        assert rule_set["name"] == "Индия стоп" and rule_set["auto_enabled"] is False
        one = f"{BASE}/{rule_set['id']}"
        assert client.post(BASE, json={"name": "индия СТОП"}).status_code == 409
        assert client.post(BASE, json={"name": "  "}).status_code == 422

        saved = client.put(one + "/geo/in", json={"no_clicks": "3", "max_avg_inst": "2"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["country_code"] == "IN" and saved.json()["no_insts"] is None
        assert client.put(one + "/geo/IND", json={}).status_code == 422
        assert client.put(one + "/geo/MX", json={"no_clicks": "-1"}).status_code == 422

        updated = client.patch(one, json={"interval_minutes": 60, "auto_enabled": True})
        assert updated.json()["interval_minutes"] == 60 and updated.json()["auto_enabled"]
        assert client.patch(one, json={"interval_minutes": 7}).status_code == 422
        listed = client.get(BASE).json()
        assert [row["geos"] for row in listed["items"]] == [["IN"]]
        assert listed["countries"] and 60 in listed["intervals"]

        run = client.post(one + "/run")
        assert run.status_code == 200, run.text
        assert run.json() == {"checked": 4, "triggered": 3, "paused": 2, "failed": 1}
        assert sorted(fake.paused) == ["c-idle", "c-pricey"]
        assert await _status("c-idle", made["connection"]) == "PAUSED"
        assert await _status("c-ok", made["connection"]) == "ACTIVE"
        assert await _status("c-mx", made["connection"]) == "ACTIVE"
        assert await _status("c-broken", made["connection"]) == "ACTIVE"

        events = client.get(BASE + "/events").json()
        assert events["total"] == 3
        by_id = {row["external_id"]: row for row in events["items"]}
        assert by_id["c-idle"]["checks"] == ["no_clicks"]
        assert by_id["c-idle"]["reason"] == "Потрачено 5,00 $ без кликов (порог 3,00 $)"
        assert by_id["c-idle"]["rule_name"] == "Индия стоп"
        assert by_id["c-idle"]["trigger"] == "manual" and by_id["c-idle"]["status"] == "paused"
        assert by_id["c-pricey"]["checks"] == ["max_avg_inst"]
        assert by_id["c-broken"]["status"] == "failed"
        assert "недоступен" in by_id["c-broken"]["error"]
        scoped = client.get(BASE + f"/events?rule_set_id={rule_set['id']}").json()
        assert scoped["total"] == 3

        # Второй прогон не трогает уже остановленное.
        assert client.post(one + "/run").json()["triggered"] == 1

        # Выключенное GEO не срабатывает.
        client.put(one + "/geo/IN", json={"is_enabled": False, "no_clicks": "3"})
        assert client.post(one + "/run").json()["triggered"] == 0

        assert client.delete(one + "/geo/IN").status_code == 204
        assert client.delete(one + "/geo/IN").status_code == 404
        assert client.get(one).json()["rules"] == []

        # Удаление правила оставляет историю с его названием.
        assert client.delete(one).status_code == 204
        assert client.get(one).status_code == 404
        kept = client.get(BASE + "/events").json()["items"]
        assert kept and all(row["rule_name"] == "Индия стоп" for row in kept)
    finally:
        client.__exit__(None, None, None)


async def test_adset_level_uses_adset_objects(geo_setup) -> None:
    fake, made = geo_setup
    async with SessionLocal() as db:
        db.add(MetaEntity(
            workspace_id=made["workspace"], connection_id=made["connection"],
            account_id=made["account"], level="adset", external_id="c-idle-s",
            parent_external_id="c-idle", name="Idle set", effective_status="ACTIVE",
            external_payload={},
        ))
        await db.commit()
    client = _admin()
    try:
        rule_set = client.post(BASE, json={"name": "Адсеты", "level": "adset"}).json()
        one = f"{BASE}/{rule_set['id']}"
        client.put(one + "/geo/IN", json={"no_clicks": "3", "no_deps": "1"})
        run = client.post(one + "/run").json()
        assert run["triggered"] == 1 and fake.paused == ["c-idle-s"]
        event = client.get(BASE + "/events").json()["items"][0]
        # На уровне адсетов депозитов нет — сработали только клики.
        assert event["level"] == "adset" and event["checks"] == ["no_clicks"]
    finally:
        client.__exit__(None, None, None)


async def test_due_rule_sets_run_on_schedule(geo_setup) -> None:
    from app.services.meta_geo_rules import run_due

    fake, made = geo_setup

    async def getter(connection, db=None):
        return fake

    async with SessionLocal() as db:
        auto = MetaGeoRuleSet(workspace_id=made["workspace"], name="Авто", level="campaign",
                              interval_minutes=15, auto_enabled=True, last_run_result={})
        manual = MetaGeoRuleSet(workspace_id=made["workspace"], name="Ручное", level="campaign",
                                interval_minutes=15, auto_enabled=False, last_run_result={})
        db.add_all([auto, manual])
        await db.flush()
        for rule_set in (auto, manual):
            db.add(MetaGeoRule(workspace_id=made["workspace"], rule_set_id=rule_set.id,
                               country_code="IN", is_enabled=True, no_clicks=Decimal("3")))
        await db.commit()
    result = await run_due(SessionLocal, getter)
    assert result["rule_sets"] == 1 and result["paused"] == 1
    assert fake.paused == ["c-idle"]
