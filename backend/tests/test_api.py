from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_and_me(database) -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        assert login.json()["login"] == "admin"
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"]["name"] == "Administrator"


def test_protected_endpoint_requires_auth() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/users")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "http_401"


def _admin_client() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return client


def test_team_crud_and_blocking(database) -> None:
    with _admin_client() as client:
        roles = client.get("/api/v1/roles")
        assert roles.status_code == 200
        buyer_role = next(role for role in roles.json() if role["name"] == "Buyer")
        created = client.post(
            "/api/v1/users",
            json={
                "name": "Test Buyer",
                "login": "test-buyer",
                "password": "strong-password",
                "role_id": buyer_role["id"],
                "parent_ids": [],
            },
        )
        assert created.status_code == 201
        user_id = created.json()["id"]
        blocked = client.patch(
            f"/api/v1/users/{user_id}/status",
            params={"new_status": "blocked"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"


def test_team_hierarchy_editing_and_cycle_protection(database) -> None:
    with _admin_client() as client:
        current = client.get("/api/v1/auth/me").json()
        roles = client.get("/api/v1/roles").json()
        lead_role = next(role for role in roles if role["name"] == "Team Lead")
        buyer_role = next(role for role in roles if role["name"] == "Buyer")
        lead = client.post(
            "/api/v1/users",
            json={
                "name": "Hierarchy Lead",
                "login": "hierarchy-lead",
                "password": "strong-password",
                "role_id": lead_role["id"],
                "parent_ids": [current["id"]],
            },
        )
        assert lead.status_code == 201
        assert lead.json()["parents"][0]["id"] == current["id"]
        buyer = client.post(
            "/api/v1/users",
            json={
                "name": "Keitaro Buyer",
                "login": "keitaro-buyer",
                "password": "strong-password",
                "role_id": buyer_role["id"],
                "parent_ids": [lead.json()["id"]],
                "keitaro_company_group": "BEE",
                "keitaro_offer_group": "BEE",
            },
        )
        assert buyer.status_code == 201
        user_id = buyer.json()["id"]
        updated = client.patch(
            f"/api/v1/users/{user_id}",
            json={
                "name": "Keitaro Buyer Updated",
                "login": "keitaro-buyer",
                "role_id": buyer_role["id"],
                "status": "active",
                "parent_ids": [lead.json()["id"]],
                "keitaro_company_group": "BEE",
                "keitaro_offer_group": None,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Keitaro Buyer Updated"
        assert updated.json()["keitaro_company_group"] == "BEE"
        assert updated.json()["keitaro_offer_group"] is None
        assert updated.json()["parents"][0]["id"] == lead.json()["id"]
        cycle = client.patch(
            f"/api/v1/users/{current['id']}",
            json={"parent_ids": [user_id]},
        )
        assert cycle.status_code == 422
        assert "cycle" in cycle.json()["error"]["message"].lower()
        password = client.post(f"/api/v1/users/{user_id}/reset-password")
        assert password.status_code == 200
        relogin = client.post(
            "/api/v1/auth/login",
            json={
                "login": "keitaro-buyer",
                "password": password.json()["temporary_password"],
            },
        )
        assert relogin.status_code == 200


def test_role_create_and_update(database) -> None:
    with _admin_client() as client:
        created = client.post(
            "/api/v1/roles",
            json={
                "name": "Senior Buyer",
                "description": "Custom role",
                "permission_codes": ["dashboard.view", "offers.view"],
            },
        )
        assert created.status_code == 201
        updated = client.patch(
            f"/api/v1/roles/{created.json()['id']}",
            json={
                "description": "Updated custom role",
                "permission_codes": [
                    "dashboard.view",
                    "offers.view",
                    "media.view",
                ],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["description"] == "Updated custom role"
        assert {item["code"] for item in updated.json()["permissions"]} == {
            "dashboard.view",
            "offers.view",
            "media.view",
        }


def test_settings_crud(database) -> None:
    with _admin_client() as client:
        created = client.post(
            "/api/v1/services",
            json={
                "name": "Test Service",
                "install_cost": "0.055",
                "commission_pct": "2.5",
                "status": "active",
            },
        )
        assert created.status_code == 201
        services = client.get("/api/v1/services", params={"search": "Test Service"})
        assert services.status_code == 200
        assert services.json()["total"] == 1
