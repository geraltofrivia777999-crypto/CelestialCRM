import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.meta_tree import build_tree
from tests.test_media_finance import _admin_client


def test_tree_includes_zero_metric_entities_and_keeps_grain() -> None:
    account_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    account = SimpleNamespace(
        id=account_id, external_id="act_123", name="Main", owner_id=owner_id,
        connection_id=connection_id,
        account_status="ACTIVE", status=SimpleNamespace(value="active"),
        currency="EUR", timezone_name="Europe/Berlin",
    )
    entities = [
        SimpleNamespace(account_id=account_id, level="campaign", external_id="c1",
                        parent_external_id=None, name="Campaign", effective_status="ACTIVE",
                        daily_budget=Decimal("10"), lifetime_budget=None),
        SimpleNamespace(account_id=account_id, level="adset", external_id="s1",
                        parent_external_id="c1", name="Set", effective_status="PAUSED",
                        daily_budget=Decimal("0"), lifetime_budget=None),
        SimpleNamespace(account_id=account_id, level="ad", external_id="a1",
                        parent_external_id="s1", name="Ad", effective_status="ACTIVE",
                        daily_budget=None, lifetime_budget=None),
        SimpleNamespace(account_id=account_id, level="campaign", external_id="c2",
                        parent_external_id=None, name="Quiet", effective_status="ACTIVE",
                        daily_budget=None, lifetime_budget=None),
    ]
    fact = SimpleNamespace(
        account_id=account_id, record_date=date(2026, 9, 24),
        campaign_external_id="c1", adset_external_id="s1", ad_external_id="a1",
        country_code="DE", impressions=100, clicks=10, spend=Decimal("5.25"),
        actions={"omni_app_install": 4, "complete_registration": 2},
    )
    roots = build_tree(
        [account], entities, [fact], {"c1": {"sales": 3}}, {connection_id: "RAMP2"}
    )
    root = roots[0]
    campaign, quiet = root["children"]
    adset = campaign["children"][0]
    ad = adset["children"][0]
    assert (root["spend"], root["insts"], root["regs"], root["deps"]) == (5.25, 4, 2, 3)
    assert (campaign["spend"], campaign["deps"], campaign["geo"]) == (5.25, 3, "DE")
    assert (adset["spend"], ad["spend"], adset["deps"], ad["deps"]) == (5.25, 5.25, None, None)
    assert adset["budget"] == 0
    assert quiet["spend"] == 0
    assert root["currency"] == "EUR" and root["agent"] == "RAMP2"
    assert root["connection_id"] == str(connection_id)


def test_tree_endpoint_returns_period_and_geo_options(database) -> None:
    with _admin_client() as client:
        response = client.get("/api/v1/meta/tree?date_from=2026-09-24&date_to=2026-09-24")
    assert response.status_code == 200
    assert response.json()["period"] == {"from": "2026-09-24", "to": "2026-09-24"}
    assert isinstance(response.json()["rows"], list)
    assert isinstance(response.json()["available_geos"], list)


def test_tree_endpoint_rejects_non_administrator(database) -> None:
    login = "tree-buyer-" + uuid.uuid4().hex[:8]
    with _admin_client() as admin:
        buyer_role = next(role for role in admin.get("/api/v1/roles").json()
                          if role["name"] == "Buyer")
        created = admin.post("/api/v1/users", json={
            "name": "Tree Buyer", "login": login, "password": "strong-password",
            "role_id": buyer_role["id"], "parent_ids": [],
        })
        assert created.status_code == 201
    with _admin_client() as buyer:
        buyer.post("/api/v1/auth/logout")
        response = buyer.post("/api/v1/auth/login", json={
            "login": login, "password": "strong-password",
        })
        assert response.status_code == 200
        result = buyer.get("/api/v1/meta/tree")
        assert result.status_code == 403
