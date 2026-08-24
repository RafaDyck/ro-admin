"""Item search.

The endpoint that finishes the argument /items/{id} started: a caller should
never need its own copy of item_db. The predecessor's panel carried 285 items
in a React component against a table of 28,525, so item 909 rendered as
"Unknown Item" while the database knew exactly what it was.
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


@pytest.fixture()
def headers(client):
    return {"Authorization": f"Bearer {_token(client, ADMIN_USER, ADMIN_PASSWORD)}"}


@pytest.mark.integration
def test_search_rejects_anonymous(client):
    assert client.get("/api/v1/items").status_code == 401


@pytest.mark.integration
def test_search_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/items", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_listing_returns_items(client, headers):
    body = client.get("/api/v1/items", headers=headers).json()
    assert body["items"]
    assert {"id", "name_english", "type", "slots"} <= set(body["items"][0])


@pytest.mark.integration
def test_the_list_does_not_carry_script_source(client, headers):
    """Kept out of the page for size. It is on the detail endpoint."""
    entry = client.get("/api/v1/items", headers=headers).json()["items"][0]
    assert "script" not in entry


@pytest.mark.integration
def test_name_search_matches_a_substring(client, headers):
    body = client.get("/api/v1/items", params={"q": "potion"}, headers=headers).json()
    assert body["items"]
    for entry in body["items"]:
        haystack = f"{entry['name_english']} {entry['name_aegis']}".lower()
        assert "potion" in haystack


@pytest.mark.integration
def test_name_search_is_case_insensitive(client, headers):
    lower = client.get("/api/v1/items", params={"q": "jellopy"}, headers=headers).json()
    upper = client.get("/api/v1/items", params={"q": "JELLOPY"}, headers=headers).json()
    assert lower["total"] == upper["total"]
    assert lower["total"] > 0


@pytest.mark.integration
def test_search_finds_the_item_the_old_panel_could_not(client, headers):
    """909 rendered as "Unknown Item" in the predecessor because it was outside
    that component's hardcoded list."""
    body = client.get("/api/v1/items", params={"q": "Jellopy"}, headers=headers).json()
    assert 909 in [e["id"] for e in body["items"]]


@pytest.mark.integration
def test_a_percent_sign_in_the_query_is_matched_literally(client, headers):
    """`%` and `_` are LIKE wildcards even when the value is a bound parameter
    -- parameterisation prevents injection, not wildcard interpretation. An
    unescaped `%` here would match every item and silently return nonsense."""
    everything = client.get("/api/v1/items", headers=headers).json()["total"]
    wildcard = client.get("/api/v1/items", params={"q": "%"}, headers=headers).json()
    assert wildcard["total"] < everything, (
        "a literal '%' matched everything -- the LIKE wildcard was not escaped"
    )


@pytest.mark.integration
def test_an_underscore_in_the_query_is_matched_literally(client, headers):
    """`_` matches any SINGLE character, so an unescaped one matches every item
    whose name is at least one character long -- i.e. all of them.

    Written as a count comparison rather than "every result contains an
    underscore", because that form passes vacuously when nothing matches.
    """
    everything = client.get("/api/v1/items", headers=headers).json()["total"]
    single = client.get("/api/v1/items", params={"q": "_"}, headers=headers).json()
    assert single["total"] < everything, (
        "a literal '_' matched everything -- the LIKE wildcard was not escaped"
    )


@pytest.mark.integration
def test_type_filter_narrows_results(client, headers):
    body = client.get("/api/v1/items", params={"type": "card"}, headers=headers).json()
    assert body["items"]
    assert all(e["type"] == "card" for e in body["items"])


@pytest.mark.integration
def test_slots_filter_narrows_results(client, headers):
    body = client.get("/api/v1/items", params={"slots": 4}, headers=headers).json()
    assert body["items"]
    assert all(e["slots"] == 4 for e in body["items"])


@pytest.mark.integration
def test_filters_combine(client, headers):
    """The haystack is name_english AND name_aegis -- the same pair
    test_name_search_matches_a_substring uses.

    Asserting on name_english alone fails against a correct search: item 11621
    is "Red Syrup", name_aegis "High_RedPotion", a healing item that matches
    "potion" through the aegis name exactly as this endpoint documents. Nine
    of the 54 healing matches are of that shape.
    """
    body = client.get(
        "/api/v1/items", params={"q": "potion", "type": "healing"}, headers=headers
    ).json()
    assert body["items"]
    for entry in body["items"]:
        assert entry["type"] == "healing"
        haystack = f"{entry['name_english']} {entry['name_aegis']}".lower()
        assert "potion" in haystack


@pytest.mark.integration
def test_total_is_the_match_count_not_the_page_size(client, headers):
    """A UI needs to size its pager. Returning len(items) would make every
    page look like the last one."""
    body = client.get(
        "/api/v1/items", params={"type": "card", "limit": 5}, headers=headers
    ).json()
    assert len(body["items"]) == 5
    assert body["total"] > 5


@pytest.mark.integration
def test_paging_advances(client, headers):
    first = client.get("/api/v1/items", params={"limit": 1}, headers=headers).json()
    second = client.get(
        "/api/v1/items", params={"limit": 1, "offset": 1}, headers=headers
    ).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]


@pytest.mark.integration
def test_a_search_matching_nothing_is_an_empty_page_not_a_404(client, headers):
    """"No items match" is a successful answer to a search. 404 would mean the
    search endpoint does not exist."""
    r = client.get(
        "/api/v1/items", params={"q": "zzzzznosuchitemzzzzz"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 0


@pytest.mark.integration
def test_limit_is_capped(client, headers):
    """28,525 rows is a response nobody meant to ask for."""
    assert client.get(
        "/api/v1/items", params={"limit": 5000}, headers=headers
    ).status_code == 422


# --- facets ------------------------------------------------------------------


@pytest.mark.integration
def test_types_are_derived_from_the_data_not_hardcoded(client, headers):
    """A UI populating a type filter must not carry its own list -- that list
    rots exactly the way the predecessor's 285-item map rotted. A server with
    custom types gets its custom types."""
    body = client.get("/api/v1/items/types", headers=headers).json()
    types = {e["type"]: e["count"] for e in body["types"]}
    assert "card" in types and "weapon" in types
    assert types["card"] > 1000


@pytest.mark.integration
def test_types_require_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/items/types", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_the_types_route_is_not_shadowed_by_the_id_route(client, headers):
    """/items/types and /items/{item_id} share a path shape. If the id route is
    registered first, "types" is parsed as an id and this returns 422."""
    assert client.get("/api/v1/items/types", headers=headers).status_code == 200
