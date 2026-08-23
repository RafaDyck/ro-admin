"""Zeny and item forensics -- the two things players actually cheat with."""
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
def test_zeny_log_returns_decoded_entries(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/logs/zeny", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "zenylog should have at least one row by now"
    entry = items[0]
    assert {"date", "char_id", "src_id", "type", "type_name", "amount", "map"} <= set(entry)
    # The code must be decoded, not passed through raw.
    assert entry["type_name"] != entry["type"]


@pytest.mark.integration
def test_item_log_returns_decoded_entries(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/logs/items", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "picklog should have at least one row by now"
    entry = items[0]
    assert {"date", "char_id", "type", "type_name", "item_id", "amount", "refine", "map"} <= set(entry)


@pytest.mark.integration
def test_economy_logs_require_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    for path in ("/api/v1/logs/zeny", "/api/v1/logs/items"):
        assert client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 403


@pytest.mark.integration
def test_economy_logs_reject_anonymous(client):
    for path in ("/api/v1/logs/zeny", "/api/v1/logs/items"):
        assert client.get(path).status_code == 401


@pytest.mark.integration
def test_char_id_filter_narrows_results(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    everything = client.get("/api/v1/logs/zeny", headers=headers).json()["items"]
    assert everything
    target = everything[0]["char_id"]
    filtered = client.get(
        "/api/v1/logs/zeny", params={"char_id": target}, headers=headers
    ).json()["items"]
    assert filtered
    assert {e["char_id"] for e in filtered} == {target}


@pytest.mark.integration
def test_char_id_filter_rejects_non_numeric(client):
    """char_id is typed as int, so injection cannot reach the query at all."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/zeny",
        params={"char_id": "1 OR 1=1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
