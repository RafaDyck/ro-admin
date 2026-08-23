import pytest
from fastapi import HTTPException

from ro_admin.deps import Principal, check_permission
from ro_admin.permissions import Level, Permission


def test_staff_may_read_logs():
    check_permission(Principal(subject="gm", level=Level.STAFF), Permission.LOGS_READ)


def test_player_may_not_read_logs():
    with pytest.raises(HTTPException) as exc:
        check_permission(Principal(subject="bob", level=Level.PLAYER), Permission.LOGS_READ)
    assert exc.value.status_code == 403


def test_staff_may_not_write_accounts():
    with pytest.raises(HTTPException) as exc:
        check_permission(Principal(subject="gm", level=Level.STAFF), Permission.ACCOUNTS_WRITE)
    assert exc.value.status_code == 403


def test_admin_may_write_accounts():
    check_permission(Principal(subject="root", level=Level.ADMIN), Permission.ACCOUNTS_WRITE)
