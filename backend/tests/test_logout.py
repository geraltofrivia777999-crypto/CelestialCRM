from fastapi.testclient import TestClient

from app.main import app


def test_no_content_responses_are_truly_empty(database) -> None:
    """A 204 must not advertise a JSON body: the API client parses on content-type,
    and an empty body fed to JSON.parse turned logout into a visible error."""
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "test-password"},
        )
        assert login.status_code == 200

        logout = client.post("/api/v1/auth/logout", json={})
        assert logout.status_code == 204
        assert logout.text == ""
        assert "content-type" not in logout.headers
        assert "crm_session" in logout.headers.get("set-cookie", "")

        # The session really is gone, so the client can safely redirect to /login.
        assert client.get("/api/v1/auth/me").status_code == 401
