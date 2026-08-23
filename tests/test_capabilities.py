import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    apply_test_env,
)


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


def _token(client, userid, password):
    r = client.post("/api/v1/auth/login", json={"userid": userid, "password": password})
    return r.json()["access_token"]


@pytest.mark.integration
def test_capabilities_reports_tier_zero_available(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier0"]["available"] is True
    assert body["tier1"]["available"] is False
    assert body["tier2"]["available"] is False


@pytest.mark.integration
def test_capabilities_lists_available_log_tables(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get(
        "/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"}
    ).json()
    # The lab has log_commands enabled, so atcommandlog exists.
    assert "atcommandlog" in body["tier0"]["log_tables"]
