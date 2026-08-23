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
    # tier1 depends on whether the overlay script is loaded in THIS lab, so it
    # is asserted by the dedicated tests below rather than pinned here.
    assert isinstance(body["tier1"]["available"], bool)
    assert body["tier2"]["available"] is False


@pytest.mark.integration
def test_capabilities_lists_available_log_tables(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get(
        "/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"}
    ).json()
    # The lab has log_commands enabled, so atcommandlog exists.
    assert "atcommandlog" in body["tier0"]["log_tables"]


@pytest.mark.integration
def test_tier1_is_no_longer_a_stub(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    tier1 = client.get(
        "/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"}
    ).json()["tier1"]
    assert tier1["reason"] != "script overlay not implemented in this release"


@pytest.mark.integration
def test_tier1_reason_is_always_actionable(client):
    """Whatever the state, the reason must tell an operator what to do next.
    'unavailable' with no explanation is the message that sends people
    reading source code."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    tier1 = client.get(
        "/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"}
    ).json()["tier1"]
    assert len(tier1["reason"]) > 20
    if not tier1["available"]:
        assert any(
            hint in tier1["reason"]
            for hint in ("schema.sql", "@reloadscript", "map server", "version")
        )


@pytest.mark.integration
def test_tier1_reports_installed_and_responding_separately(client):
    """Two different problems -- 'you have not run schema.sql' and 'your map
    server is down' -- must not collapse into one flag."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    tier1 = client.get(
        "/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"}
    ).json()["tier1"]
    assert "installed" in tier1 and "responding" in tier1
