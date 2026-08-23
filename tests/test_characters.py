"""Character reads, and the staleness contract.

The second half of this file is the important half. char.zeny is a stale
mirror while a character is online -- measured during the Tier 1 work, where
an in-game +777 change did not appear in the table at t+20s but read exactly
+777 after logout. An API that hands that number over unlabelled is inviting
the reader to treat a five-minute-old value as live.
"""
import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    CHAR_WITH_ECONOMY, apply_test_env,
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


@pytest.fixture()
def headers(client):
    return {"Authorization": f"Bearer {_token(client, ADMIN_USER, ADMIN_PASSWORD)}"}


@pytest.mark.integration
def test_listing_rejects_anonymous(client):
    assert client.get("/api/v1/characters").status_code == 401


@pytest.mark.integration
def test_listing_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/characters", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_listing_returns_characters(client, headers):
    items = client.get("/api/v1/characters", headers=headers).json()["items"]
    assert items
    assert {"char_id", "account_id", "name", "base_level", "zeny", "online"} <= set(items[0])


@pytest.mark.integration
def test_name_filter_narrows_results(client, headers):
    listed = client.get("/api/v1/characters", params={"limit": 1}, headers=headers).json()
    name = listed["items"][0]["name"]
    filtered = client.get(
        "/api/v1/characters", params={"name": name}, headers=headers
    ).json()["items"]
    assert filtered
    assert {e["name"] for e in filtered} == {name}


@pytest.mark.integration
def test_online_filter_narrows_results(client, headers):
    items = client.get(
        "/api/v1/characters", params={"online": False}, headers=headers
    ).json()["items"]
    assert items
    assert all(e["online"] is False for e in items)


@pytest.mark.integration
def test_single_character_is_returned(client, headers):
    r = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers)
    assert r.status_code == 200
    assert r.json()["char_id"] == CHAR_WITH_ECONOMY


@pytest.mark.integration
def test_unknown_character_is_404(client, headers):
    """Asserts on the detail message, not just the status: a route that does
    not exist ALSO returns 404, so a bare status check here would pass whether
    or not the endpoint was ever built."""
    r = client.get("/api/v1/characters/999999999", headers=headers)
    assert r.status_code == 404
    assert "999999999" in r.json()["detail"], (
        "got FastAPI's generic 404 -- the route is missing, not the character"
    )


@pytest.mark.integration
def test_class_is_a_bare_id_and_no_name_is_invented(client, headers):
    """Checked, not assumed: rAthena has NO job table in SQL. It keeps job data
    in YAML on the server's filesystem, which this API deliberately never
    reads. So Tier 0 cannot resolve a job name, and the honest thing is to
    return the id and say so -- not to ship a lookup table (which
    scripts/check_no_game_data.py exists to prevent) and not to invent a label.

    Contrast items, where item_db IS a SQL table and the API therefore owes the
    caller the name. The rule is the same; only the server's answer differs.
    """
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers).json()
    assert isinstance(body["class"], int)
    assert "job_name" not in body, (
        "a job name here could only have been fabricated or bundled"
    )


# --- the staleness contract --------------------------------------------------


@pytest.mark.integration
def test_an_offline_character_is_not_marked_stale(client, headers):
    """char is authoritative once the map server has flushed on logout."""
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers).json()
    assert body["online"] is False
    assert body["stale"] is False
    assert body["stale_fields"] == []


@pytest.mark.integration
def test_the_response_always_carries_the_staleness_fields(client, headers):
    """Present on every response, not only when true. A field that appears only
    sometimes trains callers to ignore it."""
    for entry in client.get("/api/v1/characters", headers=headers).json()["items"]:
        assert "stale" in entry and "stale_fields" in entry


@pytest.mark.integration
def test_a_stale_character_still_reports_its_values(client, headers):
    """Labelled, never withheld. A null would be indistinguishable from zero,
    and an operator asking a character's zeny deserves the best answer
    available even when it is a few minutes old."""
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers).json()
    assert body["zeny"] is not None


# --- inventory ---------------------------------------------------------------


@pytest.mark.integration
def test_inventory_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}/inventory",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_inventory_of_unknown_character_is_404(client, headers):
    """Not an empty item list. "This character owns nothing" and "there is no
    such character" are different answers and only one of them is true.

    Checks the detail message for the same reason as above -- a missing route
    would otherwise satisfy a bare 404 assertion.
    """
    r = client.get("/api/v1/characters/999999999/inventory", headers=headers)
    assert r.status_code == 404
    assert "999999999" in r.json()["detail"], (
        "got FastAPI's generic 404 -- the route is missing, not the character"
    )


@pytest.mark.integration
def test_inventory_carries_item_names(client, headers):
    """The rule that produced the items endpoint, applied here. Returning bare
    nameids would push every consumer toward keeping its own id-to-name map --
    exactly the defect scripts/check_no_game_data.py exists to prevent.

    Character 200000 is seeded with items in the reference lab, and the Tier 1
    work granted it Jellopy (909) -- the item the predecessor's panel rendered
    as "Unknown Item" because it was outside that component's hardcoded list.
    """
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}/inventory",
                      headers=headers).json()
    assert body["items"], "the seeded demo character holds items"
    for entry in body["items"]:
        assert entry["item_name"], f"item {entry['item_id']} resolved to an empty name"
        assert entry["item_name"] != str(entry["item_id"])


@pytest.mark.integration
def test_inventory_reports_its_own_staleness(client, headers):
    """`inventory` is flushed on the same schedule as `char` -- measured during
    the Tier 1 work, where a grant to a logged-in character was absent from the
    table at +5s and present after logout. A caller checking whether a grant
    landed must be told the table may not know yet."""
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}/inventory",
                      headers=headers).json()
    assert "stale" in body
    assert body["stale"] is False, "character 200000 is expected offline"
