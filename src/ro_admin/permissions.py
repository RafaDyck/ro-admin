"""The authorization model. One table, one lookup, no checks anywhere else.

The predecessor had four mechanisms and three thresholds: an `admin_required`
decorator gating on group_id < 99, a `require_admin()` decorator gating on
group_id < 10, inline checks, and a blueprint with no gate at all. Two
decorators both named for "admin" disagreed by 89 levels, and both carried
comments beginning "Assuming". Nobody could answer "who may do what" without
reading every route.

Levels are rAthena's actual group ids from conf/groups.yml. Note there is NO
group 50 -- the predecessor gated on `< 50` as though a middle tier existed,
which on a real server silently means "Admin only".
"""
from enum import IntEnum, StrEnum


class Level(IntEnum):
    PLAYER = 0
    SUPER_PLAYER = 1
    SUPPORT = 2
    SCRIPT_MANAGER = 3
    EVENT_MANAGER = 4
    VIP = 5
    STAFF = 10        # rAthena calls this "Law Enforcement"
    ADMIN = 99


class Permission(StrEnum):
    LOGS_READ = "logs.read"
    ACCOUNTS_READ = "accounts.read"
    ACCOUNTS_WRITE = "accounts.write"
    CHARACTERS_READ = "characters.read"
    CHARACTERS_WRITE = "characters.write"
    SYSTEM_READ = "system.read"
    COMMANDS_READ = "commands.read"
    COMMANDS_WRITE = "commands.write"


_REQUIRED: dict[Permission, Level] = {
    Permission.LOGS_READ: Level.STAFF,
    Permission.ACCOUNTS_READ: Level.STAFF,
    Permission.ACCOUNTS_WRITE: Level.ADMIN,
    Permission.CHARACTERS_READ: Level.STAFF,
    Permission.CHARACTERS_WRITE: Level.ADMIN,
    Permission.SYSTEM_READ: Level.STAFF,
    Permission.COMMANDS_READ: Level.STAFF,
    # Enqueueing changes the game world. Same level as any other write.
    Permission.COMMANDS_WRITE: Level.ADMIN,
}

ALL_PERMISSIONS = tuple(_REQUIRED)


def required_level(permission: Permission | str) -> Level:
    """Look up a permission's required level.

    Raises KeyError for unknown permissions. Deliberately not defaulted:
    a typo must fail loudly rather than silently granting or denying.
    """
    try:
        key = Permission(permission)
    except ValueError as exc:
        raise KeyError(f"unknown permission: {permission!r}") from exc
    return _REQUIRED[key]
