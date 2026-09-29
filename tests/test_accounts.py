"""Account reads.

The predecessor had seven account routes and ro-admin had none, which is why
this exists. It is reads only: mutation belongs with an audit trail, and the
queue that would carry it has no account actions yet.
"""
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    apply_test_env,
)
from ro_admin.config import Settings
from ro_admin.db import Database


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
    assert client.get("/api/v1/accounts").status_code == 401


@pytest.mark.integration
def test_listing_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/accounts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_listing_returns_accounts(client, headers):
    body = client.get("/api/v1/accounts", headers=headers).json()
    assert body["items"], "the lab has accounts"
    entry = body["items"][0]
    assert {"account_id", "userid", "group_id", "state"} <= set(entry)


@pytest.mark.integration
def test_userid_filter_narrows_results(client, headers):
    body = client.get(
        "/api/v1/accounts", params={"userid": ADMIN_USER}, headers=headers
    ).json()
    assert body["items"]
    assert {e["userid"] for e in body["items"]} == {ADMIN_USER}


@pytest.mark.integration
def test_group_filter_narrows_results(client, headers):
    body = client.get(
        "/api/v1/accounts", params={"min_group_id": 99}, headers=headers
    ).json()
    assert body["items"]
    assert all(e["group_id"] >= 99 for e in body["items"])


@pytest.mark.integration
def test_paging_is_honoured(client, headers):
    first = client.get("/api/v1/accounts", params={"limit": 1}, headers=headers).json()
    second = client.get(
        "/api/v1/accounts", params={"limit": 1, "offset": 1}, headers=headers
    ).json()
    assert len(first["items"]) == 1
    assert first["items"][0]["account_id"] != second["items"][0]["account_id"]


@pytest.mark.integration
def test_banned_is_derived_not_guessed(client, headers):
    """rAthena encodes a ban in two different places -- `state` = 5 for a
    permanent ban, and `unban_time` in the future for a temporary one. A caller
    should not have to know that; it is exactly the server-side knowledge this
    API owes them."""
    body = client.get("/api/v1/accounts", headers=headers).json()
    assert all(isinstance(e["banned"], bool) for e in body["items"])


@pytest.mark.integration
def test_single_account_is_returned(client, headers):
    listed = client.get("/api/v1/accounts", params={"limit": 1}, headers=headers).json()
    account_id = listed["items"][0]["account_id"]
    r = client.get(f"/api/v1/accounts/{account_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["account_id"] == account_id


@pytest.mark.integration
def test_unknown_account_is_404(client, headers):
    assert client.get("/api/v1/accounts/999999999", headers=headers).status_code == 404


@pytest.mark.integration
def test_an_accounts_characters_are_listed(client, headers):
    """The join an operator always makes next."""
    r = client.get("/api/v1/accounts/2000005/characters", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "account 2000005 has characters in the reference lab"
    assert all(e["account_id"] == 2000005 for e in items)


@pytest.mark.integration
def test_characters_of_unknown_account_is_404_not_empty(client, headers):
    """An empty list would say 'this account has no characters', which is a
    different and false statement.

    Asserts on the detail message, not just the status: a route that does not
    exist ALSO returns 404, so a bare status check here passes whether or not
    the endpoint was ever built.
    """
    r = client.get("/api/v1/accounts/999999999/characters", headers=headers)
    assert r.status_code == 404
    assert "999999999" in r.json()["detail"], (
        "got FastAPI's generic 404 -- the route is missing, not the account"
    )


# --- prefix search -----------------------------------------------------------
#
# Same design as the character search: a range scan on rAthena's index on
# login.userid (named `name`), ordered by userid, with has_more instead of a
# count.


def _everyone(client, headers):
    return client.get(
        "/api/v1/accounts", params={"limit": 500}, headers=headers
    ).json()["items"]


def _userids(client, headers, **params):
    r = client.get("/api/v1/accounts", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [a["userid"] for a in r.json()["items"]]


@pytest.mark.integration
def test_userid_prefix_matches_from_the_start(client, headers):
    target = next(a["userid"] for a in _everyone(client, headers) if len(a["userid"]) >= 3)
    # limit=500: on a larger install the target might not land on the
    # default first page of 50.
    found = _userids(client, headers, userid_prefix=target[:3], limit=500)
    assert target in found
    assert all(u.casefold().startswith(target[:3].casefold()) for u in found)


@pytest.mark.integration
def test_userid_prefix_treats_underscore_literally(client, headers):
    target = next(
        a["userid"] for a in _everyone(client, headers)
        if len(a["userid"]) >= 2 and a["userid"][1] != "_"
    )
    assert target not in _userids(client, headers, userid_prefix=target[0] + "_")


@pytest.mark.integration
def test_userid_prefix_results_are_ordered_by_userid(client, headers):
    everyone = _everyone(client, headers)
    letter = Counter(a["userid"][0].casefold() for a in everyone).most_common(1)[0][0]
    found = _userids(client, headers, userid_prefix=letter)
    # Compared only among userids that are ASCII letters and digits: under
    # utf8mb4_0900_ai_ci, ASCII digits sort before letters exactly as they do
    # in Python, so they're safe to include (this is what lets demo00..
    # demo14 be compared below). Punctuation, symbols and accented letters
    # are the ones MySQL's collation and str.casefold() are not guaranteed to
    # agree on, so a userid outside this set could legitimately sort
    # differently under the two orderings without the endpoint being wrong.
    # Filtering doesn't reorder anything, so a subsequence of a
    # correctly-sorted response is still sorted.
    comparable = [u for u in found if u.isascii() and u.isalnum()]
    if len(comparable) < 2:
        pytest.skip("the lab needs two ASCII alphanumeric userids sharing a first letter")
    assert comparable == sorted(comparable, key=str.casefold)


@pytest.mark.integration
def test_account_has_more_reports_a_further_page(client, headers):
    first = client.get("/api/v1/accounts", params={"limit": 1}, headers=headers).json()
    assert first["has_more"] is True
    everyone = client.get("/api/v1/accounts", params={"limit": 500}, headers=headers).json()
    if everyone["has_more"]:
        pytest.skip("the lab has 500 or more accounts; this assumes it fits one page")
    assert everyone["has_more"] is False


@pytest.mark.integration
@pytest.mark.parametrize("value", ["", "x" * 24])
def test_userid_prefix_bounds_are_enforced(client, headers, value):
    """23 is `login`.`userid`'s width."""
    r = client.get("/api/v1/accounts", params={"userid_prefix": value}, headers=headers)
    assert r.status_code == 422


@pytest.mark.integration
def test_userid_prefix_is_served_by_the_userid_index(client, headers, monkeypatch):
    """EXPLAIN of the statement this endpoint actually sends. See the character
    equivalent for why the statement is captured rather than rebuilt.

    The prefix is the rarest leading letter among the lab's own userids, not
    a hardcoded one. On a table this small, MySQL's cost model abandons the
    index once a letter is common enough: measured, "d" matches 15 of the
    lab's 21 accounts (the demo00..demo14 seed accounts) and plans as a full
    scan with a filesort, while a rare letter still ranges over the index.
    """
    everyone = _everyone(client, headers)
    counts = Counter(a["userid"][0].casefold() for a in everyone)
    letter = min(counts, key=counts.get)

    seen = []
    real_query = Database.query

    def spy(self, sql, params=None):
        rows = real_query(self, sql, params)
        seen.append((sql, params))
        return rows

    monkeypatch.setattr(Database, "query", spy)
    r = client.get("/api/v1/accounts", params={"userid_prefix": letter}, headers=headers)
    assert r.status_code == 200
    monkeypatch.setattr(Database, "query", real_query)

    sql, params = next((s, p) for s, p in reversed(seen) if "FROM login" in s)
    plan = Database(Settings()).query("EXPLAIN " + sql, params)
    row = next(p for p in plan if p["table"] == "login")
    assert row["key"] == "name", plan
    assert row["type"] == "range", plan
    # Pins ORDER BY-by-the-searched-column, not just the index use: ordering
    # by account_id instead would still find key=name/type=range for the
    # WHERE clause, but would need a filesort to satisfy an ORDER BY on a
    # different column afterwards. This is the assertion that actually
    # catches that regression -- the key/type pair alone does not.
    extra = row["Extra"] or ""
    assert "Using filesort" not in extra, plan
    assert "Using temporary" not in extra, plan
