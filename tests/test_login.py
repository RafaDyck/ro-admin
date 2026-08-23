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


@pytest.mark.integration
def test_admin_can_log_in(client):
    r = client.post("/api/v1/auth/login", json={"userid": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    assert r.json()["level"] == 99


@pytest.mark.integration
def test_wrong_password_is_401(client):
    r = client.post("/api/v1/auth/login", json={"userid": ADMIN_USER, "password": "nope"})
    assert r.status_code == 401


@pytest.mark.integration
def test_player_can_log_in_but_gets_player_level(client):
    """A player authenticating is fine; authorization is what stops them."""
    r = client.post("/api/v1/auth/login", json={"userid": PLAYER_USER, "password": PLAYER_PASSWORD})
    assert r.status_code == 200
    assert r.json()["level"] == 0
