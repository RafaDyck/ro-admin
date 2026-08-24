"""Item names must come from the server, never from a client-side table.

The predecessor's panel resolved item names from a hardcoded 285-item map in a
React component while item_db held 28,525 rows, so anything outside that map
rendered as "Unknown Item". An agent cannot read a React component at all, so
knowledge kept there is a capability the product does not actually have.

These tests pin the rule for this API: if a response mentions an item id, the
caller must be able to learn its name without knowing anything in advance.
"""
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
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.mark.integration
def test_item_lookup_returns_a_name(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/items/909", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["item_id"] == 909
    assert body["name"] == "Jellopy"
    assert body["type"] == "etc"


@pytest.mark.integration
def test_the_item_that_broke_the_old_panel_resolves_here(client):
    """909 rendered as 'Unknown Item' in the predecessor because it was not in
    the frontend's hardcoded list. It is in the database, so it resolves here."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    assert client.get(
        "/api/v1/items/909", headers={"Authorization": f"Bearer {token}"}
    ).json()["name"] == "Jellopy"


@pytest.mark.integration
def test_unknown_item_id_is_404_not_a_guess(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/items/99999999", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


@pytest.mark.integration
def test_item_lookup_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/items/909", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_item_logs_carry_names_so_callers_need_no_lookup_table(client):
    """The log response must be readable on its own.

    Returning a bare item_id would push every consumer toward keeping its own
    id-to-name map -- which is exactly the defect this rule exists to prevent.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/items", headers={"Authorization": f"Bearer {token}"}
    ).json()["items"]
    assert items
    for entry in items:
        assert "item_name" in entry
        assert entry["item_name"], f"item {entry['item_id']} resolved to an empty name"


@pytest.mark.integration
def test_detail_carries_the_stats_that_answer_what_is_this(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get("/api/v1/items/909", headers={"Authorization": f"Bearer {token}"}).json()
    for field in ("weight", "price_buy", "price_sell", "slots", "equip_level_min"):
        assert field in body


@pytest.mark.integration
def test_detail_carries_the_script_so_behaviour_is_visible(client):
    """"What does this item actually do" is answered by its rAthena script,
    and that is knowledge only the server has. Red Potion (501) has one."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get("/api/v1/items/501", headers={"Authorization": f"Bearer {token}"}).json()
    assert "script" in body
    assert body["script"], "Red Potion has a heal script"


@pytest.mark.integration
def test_an_item_with_no_script_reports_null_not_an_empty_string(client):
    """Jellopy is an etc item and does nothing. null says "no script"; ""
    would be indistinguishable from "a script that is blank"."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get("/api/v1/items/909", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["script"] is None


@pytest.mark.integration
def test_the_detail_response_is_a_superset_of_the_search_row(client):
    """Fetching the detail of a row you just listed must not lose a field."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    listed = client.get(
        "/api/v1/items", params={"q": "Jellopy"}, headers=headers
    ).json()["items"][0]
    detail = client.get(f"/api/v1/items/{listed['id']}", headers=headers).json()
    assert set(listed) <= set(detail)


@pytest.mark.integration
def test_the_original_field_names_still_work(client):
    """item_id and name are published in the OpenAPI document and referenced in
    skill/SKILL.md. They are kept deliberately, not by oversight -- renaming
    them would break existing callers so that two field names could match."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    body = client.get("/api/v1/items/909", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["item_id"] == 909
    assert body["name"] == "Jellopy"
    assert body["id"] == body["item_id"]
    assert body["name_english"] == body["name"]
