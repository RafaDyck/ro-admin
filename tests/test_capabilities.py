import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    apply_test_env,
)
from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.overlay import Action, OverlayStatus
from ro_admin.routers import system as system_router


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
    # tier2 was pinned False while it WAS a stub. It is now read from a
    # heartbeat, so it depends on whether this server's map-server was built
    # with the compiled hook -- the same reason tier1 is not pinned. What it
    # reports, and that an unavailable tier2 names its install step, are
    # asserted on both branches in tests/test_tier2.py.
    assert isinstance(body["tier2"]["available"], bool)


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


@pytest.mark.integration
def test_every_action_reports_the_tier_that_consumes_it(client):
    """Per action, so a client never needs its own action-to-tier mapping. The
    values are the consuming tier's own, from the same read, so they cannot
    disagree with it."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get(
        "/api/v1/system/capabilities", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert set(body["actions"]) == {a.value for a in Action}
    assert body["actions"]["sync_character"]["tier"] == "tier2"
    assert body["actions"]["give_item"]["tier"] == "tier1"
    for name, action in body["actions"].items():
        tier = body[action["tier"]]
        assert action["available"] == tier["available"], name
        assert action["reason"] == tier["reason"], name


def test_each_action_routes_through_the_tier_it_actually_needs(monkeypatch):
    """Deterministic, unlike the loop above: on a lab where both tiers are
    healthy, `available` is equal for every action regardless of the mapping,
    and the two `reason` strings come from one template that differs only
    when the two heartbeat ages happen to round to different seconds -- see
    tests/test_tier2.py::test_the_two_tiers_are_reported_independently for
    the history of that exact flake. Two DISTINCT canned statuses make a
    wrong mapping fail every run instead of sometimes.

    No database needed for the tiers themselves: read_status and
    read_tier2_status are replaced outright. Database.query is still faked
    because capabilities() also asks information_schema (for tier0's log
    tables and _maps) before it gets to the actions.
    """
    apply_test_env(monkeypatch)
    tier1_status = OverlayStatus(
        installed=True, responding=True, compatible=True, reason="tier1-reason",
    )
    tier2_status = OverlayStatus(
        installed=True, responding=False, compatible=True, reason="tier2-reason",
    )
    monkeypatch.setattr(system_router, "read_status", lambda db: tier1_status)
    monkeypatch.setattr(system_router, "read_tier2_status", lambda db: tier2_status)
    # No table is "present", so _maps stops at "ro_admin_maps not in present"
    # without a second query.
    monkeypatch.setattr(Database, "query", lambda self, sql, params=None: [])

    body = system_router.capabilities(Settings()).model_dump(mode="json")

    for action in ("give_item", "adjust_zeny"):
        assert body["actions"][action]["available"] is True
        assert body["actions"][action]["reason"] == "tier1-reason"
    assert body["actions"]["sync_character"]["available"] is False
    assert body["actions"]["sync_character"]["reason"] == "tier2-reason"
