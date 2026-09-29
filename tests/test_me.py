"""/auth/me tells a client what it may do, from the same function that
enforces it.

A UI renders an action only if the API would accept it. Without this, the
client would need its own copy of the permission table, and that copy would
drift the first time the table changed.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import TEST_JWT_SECRET, apply_test_env
from ro_admin.auth import issue_service_token
from ro_admin.deps import Principal, check_permission
from ro_admin.permissions import ALL_PERMISSIONS, Level, Permission
from ro_admin.routers.auth import me

PRINCIPALS = [
    Principal("player", Level.PLAYER),
    Principal("staff", Level.STAFF),
    Principal("admin", Level.ADMIN),
    Principal("agent", Level.PLAYER, scopes=(Permission.LOGS_READ,)),
    # A scoped token is a ceiling even for an admin-level subject.
    Principal("narrow-admin", Level.ADMIN, scopes=(Permission.CHARACTERS_READ,)),
]


def test_admin_is_told_every_permission():
    assert set(me(Principal("a", Level.ADMIN)).permissions) == {
        str(p) for p in ALL_PERMISSIONS
    }


def test_staff_is_told_reads_but_not_writes():
    told = set(me(Principal("s", Level.STAFF)).permissions)
    assert "characters.read" in told
    assert "commands.write" not in told


def test_a_scoped_token_is_told_exactly_its_scopes():
    agent = Principal("agent", Level.PLAYER, scopes=(Permission.LOGS_READ,))
    assert me(agent).permissions == ["logs.read"]


@pytest.mark.parametrize("principal", PRINCIPALS, ids=lambda p: p.subject)
def test_what_is_reported_is_what_is_enforced(principal):
    told = set(me(principal).permissions)
    for permission in ALL_PERMISSIONS:
        try:
            check_permission(principal, permission)
            enforced = True
        except HTTPException:
            enforced = False
        assert (str(permission) in told) == enforced, permission


def test_me_reflects_a_service_tokens_scopes_end_to_end(monkeypatch):
    """The tests above call me() directly, which skips JWT scope parsing and
    response serialisation entirely. This pins the same guarantee through the
    real HTTP path: mint a scoped service token, hit /auth/me, and check what
    comes back over the wire. No database needed -- current_principal decodes
    the token and me() reads only the principal it produces.
    """
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    client = TestClient(app)
    token = issue_service_token(
        secret=TEST_JWT_SECRET, name="me-e2e",
        scopes=[Permission.LOGS_READ, Permission.SYSTEM_READ], ttl_seconds=60,
    )
    body = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert body["level"] == 0
    # In ALL_PERMISSIONS order (LOGS_READ then SYSTEM_READ in _REQUIRED),
    # not scope-argument order, since a client should be able to rely on one
    # ordering regardless of how the token happened to list its scopes.
    assert body["permissions"] == ["logs.read", "system.read"]
