"""The health endpoint must be able to FAIL.

An earlier container healthcheck probed /openapi.json, which is served from
code with no configuration at all. A container with no database credentials
therefore reported "healthy" while being completely unable to work -- a green
signal measuring nothing, which is the failure mode this project keeps finding.
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


@pytest.mark.integration
def test_healthz_reports_ok_when_the_database_answers(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["database"] == "ok"


@pytest.mark.integration
def test_healthz_needs_no_authentication(client):
    """A health probe runs before anyone has a token."""
    assert client.get("/healthz").status_code == 200


def test_healthz_fails_when_the_database_is_unreachable(monkeypatch):
    """The point of the endpoint: it must go unhealthy on a broken config."""
    apply_test_env(monkeypatch)
    # Deliberately point at somewhere nothing listens. This test exists to
    # prove /healthz CAN fail -- so it must NOT reuse the working test config.
    monkeypatch.setenv("RO_ADMIN_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("RO_ADMIN_DB_PORT", "1")
    monkeypatch.setenv("RO_ADMIN_DB_USER", "nobody")
    monkeypatch.setenv("RO_ADMIN_DB_PASSWORD", "wrong")
    from ro_admin.main import app
    r = TestClient(app, raise_server_exceptions=False).get("/healthz")
    assert r.status_code == 503
    assert r.json()["database"] != "ok"
