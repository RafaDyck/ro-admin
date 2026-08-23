"""One chronological view of everything that happened to a character.

FluxCP has a page per log table, which means answering "what happened to this
character?" is a manual cross-reference across several screens. This merges
them, which is the question operators actually ask.
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
def test_timeline_merges_multiple_log_sources(client):
    """Character 150002 has a zeny entry and an item entry -- both must appear."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150002},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 2
    assert {"zeny", "item"} <= {e["kind"] for e in items}


@pytest.mark.integration
def test_timeline_entries_carry_a_readable_summary(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150002},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"]
    for entry in items:
        assert {"date", "kind", "summary", "char_id", "detail"} <= set(entry)
        assert entry["summary"].strip(), "every entry needs a human-readable summary"


@pytest.mark.integration
def test_timeline_is_newest_first(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150000, "limit": 20},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"]
    assert len(items) > 1
    dates = [e["date"] for e in items]
    assert dates == sorted(dates, reverse=True), "merged sources must be re-sorted, not concatenated"


@pytest.mark.integration
def test_timeline_includes_command_source(client):
    """Character 150000 has GM command history, so 'command' must appear.

    Asserts membership, not equality. An earlier version asserted the kind set
    was exactly {"command"} -- true when written, and false the moment anything
    touched that character's zeny. A test pinned to a snapshot of mutable world
    data decays into a false alarm and trains you to ignore it.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150000},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"]
    assert items
    assert "command" in {e["kind"] for e in items}


@pytest.mark.integration
def test_timeline_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150002},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_timeline_for_unknown_character_is_empty_not_an_error(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 99999999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
