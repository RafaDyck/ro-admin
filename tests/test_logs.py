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
def test_admin_can_read_command_logs(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get("/api/v1/logs/commands", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and isinstance(body["items"], list)
    if body["items"]:
        assert {"date", "account_id", "char_id", "char_name", "map", "command"} <= set(body["items"][0])


@pytest.mark.integration
def test_player_is_forbidden_from_command_logs(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/logs/commands", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_unauthenticated_is_401(client):
    assert client.get("/api/v1/logs/commands").status_code == 401


@pytest.mark.integration
def test_char_name_filter_is_not_injectable(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/commands",
        params={"char_name": "x'; DROP TABLE atcommandlog;--"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    # The table must still exist.
    assert client.get("/api/v1/logs/commands", headers={"Authorization": f"Bearer {token}"}).status_code == 200
