import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("RO_ADMIN_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("RO_ADMIN_DB_USER", "u")
    monkeypatch.setenv("RO_ADMIN_DB_PASSWORD", "p")
    from ro_admin.main import app
    return TestClient(app)


def test_openapi_is_generated_and_covers_every_route(client):
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert {
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/logs/commands",
        "/api/v1/system/capabilities",
    } <= paths


def test_no_debug_routes_exist(client):
    """Regression guard: the predecessor shipped an unauthenticated debug blueprint."""
    spec = client.get("/openapi.json").json()
    assert not [p for p in spec["paths"] if "debug" in p.lower()]


def test_every_operation_has_a_summary(client):
    """The skill reads summaries to choose endpoints, so they are not optional."""
    spec = client.get("/openapi.json").json()
    missing = [
        f"{method.upper()} {path}"
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if not op.get("summary")
    ]
    assert not missing, f"operations without a summary: {missing}"
