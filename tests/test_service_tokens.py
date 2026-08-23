import pytest
from fastapi import HTTPException

from ro_admin.auth import issue_service_token, decode_token
from ro_admin.deps import Principal, check_permission
from ro_admin.permissions import Level, Permission


def test_service_token_carries_scopes():
    token = issue_service_token(
        secret="x" * 32, name="log-reader",
        scopes=[Permission.LOGS_READ], ttl_seconds=60,
    )
    claims = decode_token(token, secret="x" * 32)
    assert claims["typ"] == "service"
    assert claims["scopes"] == ["logs.read"]


def test_scoped_principal_may_use_its_scope():
    check_permission(
        Principal(subject="log-reader", level=Level.PLAYER, scopes=(Permission.LOGS_READ,)),
        Permission.LOGS_READ,
    )


def test_scoped_principal_may_not_exceed_its_scope():
    """A scoped token is a ceiling, not a floor -- level never rescues it."""
    with pytest.raises(HTTPException) as exc:
        check_permission(
            Principal(subject="root-ish", level=Level.ADMIN, scopes=(Permission.LOGS_READ,)),
            Permission.ACCOUNTS_WRITE,
        )
    assert exc.value.status_code == 403


def test_human_principal_still_uses_levels():
    check_permission(Principal(subject="gm", level=Level.STAFF, scopes=None), Permission.LOGS_READ)
