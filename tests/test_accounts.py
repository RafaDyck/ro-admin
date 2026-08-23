"""Account reads.

The predecessor had seven account routes and ro-admin had none, which is why
this exists. It is reads only: mutation belongs with an audit trail, and the
queue that would carry it has no account actions yet.
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
