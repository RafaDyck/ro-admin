import pytest

from ro_admin.permissions import Level, Permission, required_level, ALL_PERMISSIONS


def test_levels_match_rathena_groups():
    # conf/groups.yml defines exactly these ids. There is no group 50.
    assert Level.PLAYER == 0
    assert Level.VIP == 5
    assert Level.STAFF == 10       # rAthena "Law Enforcement"
    assert Level.ADMIN == 99


def test_every_permission_has_a_required_level():
    for permission in ALL_PERMISSIONS:
        assert isinstance(required_level(permission), Level)


def test_reads_are_staff_and_mutations_are_admin():
    assert required_level(Permission.LOGS_READ) == Level.STAFF
    assert required_level(Permission.ACCOUNTS_WRITE) == Level.ADMIN


def test_unknown_permission_is_refused_not_defaulted():
    with pytest.raises(KeyError):
        required_level("logs.invented")


def test_all_permissions_covers_every_member():
    # A Permission missing from _REQUIRED would be enforceable for a scoped
    # token (Permission membership alone) but never reported by /auth/me
    # (which iterates ALL_PERMISSIONS) -- so the two sets must agree.
    assert set(Permission) == set(ALL_PERMISSIONS)
