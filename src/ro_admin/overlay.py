"""Tier 1 overlay: the command queue and the script that consumes it.

The queue carries typed integer arguments, never a command string. The
predecessor queued text like "@zeny Aldebaran 1000000" and parsed it inside
the NPC, which produced two failure modes this module exists to make
impossible: a character name reaching SQL, and a failed parse silently
substituting a default (the observed case granted 1,000,000 zeny).

Validation happens here, once, before anything is written. The script does no
parsing at all and therefore has nothing to fall back to.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, NamedTuple, Protocol

# Bumped together with `.version$` in overlay/ro_admin_overlay.txt whenever the
# queue contract changes. The API refuses to report Tier 1 available against a
# script declaring a different version, so an operator who copies a new release
# but forgets to reload the script is told, rather than left to wonder.
OVERLAY_VERSION = "1"

COMMAND_TABLE = "ro_admin_commands"
HEARTBEAT_TABLE = "ro_admin_overlay"


class InvalidCommand(ValueError):
    """A command that must not be written to the queue."""


class Action(StrEnum):
    GIVE_ITEM = "give_item"
    ADJUST_ZENY = "adjust_zeny"


class _ArgSpec(NamedTuple):
    """One queued argument's bounds, in the order the script reads them:
    arg_int, then arg_int2. Missing trailing arguments are stored as 0.

    `forbidden` carves out specific values inside [minimum, maximum] that are
    still rejected -- a hole in an otherwise-contiguous range. It exists so a
    value that is numerically in-range but game-meaningless (see `delta`
    below) can be refused declaratively, here, instead of `validate()`
    growing a per-action special case.
    """
    name: str
    minimum: int
    maximum: int
    forbidden: frozenset[int] = frozenset()
    forbidden_reason: str = ""


_SPECS: dict[Action, tuple[_ArgSpec, ...]] = {
    # 30000 is rAthena's MAX_AMOUNT. A larger request is not a big grant, it
    # is a request the game server will refuse -- better to say so here.
    Action.GIVE_ITEM: (
        _ArgSpec("item_id", 1, 2_147_483_647),
        _ArgSpec("amount", 1, 30_000),
    ),
    # Relative, and signed. See overlay/README.md for why there is no
    # absolute "set zeny" action.
    Action.ADJUST_ZENY: (
        _ArgSpec(
            "delta", -1_000_000_000, 1_000_000_000,
            forbidden=frozenset({0}),
            forbidden_reason=(
                "@zeny 0 is refused outright by the game -- ACMD_FUNC(zeny) "
                "returns early on atoi(message) == 0 without touching the "
                "player (src/map/atcommand.cpp:2897-2900). The script's "
                "post-condition check (Zeny - .@before != .@a1) cannot tell "
                "that refusal apart from a real delta of zero, so a zero "
                "delta would be stamped 'executed' for work the game never "
                "did. Reject it before it reaches the queue."
            ),
        ),
    ),
}


def validate(action: Action | str, args: dict[str, int]) -> tuple[int, int]:
    """Return (arg_int, arg_int2) for a valid command, or raise InvalidCommand.

    Raises rather than defaulting, for both an unknown action and an
    out-of-range value.
    """
    try:
        key = Action(action)
    except ValueError as exc:
        raise InvalidCommand(f"unknown action: {action!r}") from exc

    values: list[int] = []
    for spec in _SPECS[key]:
        name, low, high = spec.name, spec.minimum, spec.maximum
        if name not in args:
            raise InvalidCommand(f"{key} requires {name!r}")
        value = args[name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidCommand(f"{name} must be an integer, got {type(value).__name__}")
        if not low <= value <= high:
            raise InvalidCommand(f"{name} must be between {low} and {high}, got {value}")
        if value in spec.forbidden:
            raise InvalidCommand(f"{name} must not be {value}: {spec.forbidden_reason}")
        values.append(value)

    while len(values) < 2:
        values.append(0)
    return values[0], values[1]


# How many polls may be missed before the script is considered unresponsive.
# Derived from the script's own reported poll_ms rather than fixed in seconds,
# so an operator who slows the overlay down is not told it is broken.
STALE_AFTER_POLLS = 10
_MIN_STALE_SECONDS = 5.0


@dataclass(frozen=True)
class OverlayStatus:
    """What was observed about the overlay -- never what was assumed.

    `installed` means the tables exist. `responding` means the script wrote a
    heartbeat recently enough. They are separate because the difference tells
    an operator whether to run schema.sql or to check @reloadscript.
    """
    installed: bool
    responding: bool
    compatible: bool
    reason: str
    version: str | None = None
    instance_id: int | None = None
    age_seconds: float | None = None

    @property
    def usable(self) -> bool:
        return self.responding and self.compatible


def classify_heartbeat(
    *, tables_present: bool, row: dict | None, now: datetime
) -> OverlayStatus:
    if not tables_present:
        return OverlayStatus(
            installed=False, responding=False, compatible=False,
            reason="overlay not installed: run overlay/schema.sql against this database",
        )

    if row is None:
        return OverlayStatus(
            installed=True, responding=False, compatible=False,
            reason=(
                "overlay tables exist but the script has never run: copy "
                "overlay/ro_admin_overlay.txt into npc/custom/, enable it in "
                "npc/scripts_custom.conf, then @reloadscript"
            ),
        )

    poll_ms = int(row["poll_ms"])
    threshold = max(_MIN_STALE_SECONDS, (poll_ms / 1000.0) * STALE_AFTER_POLLS)
    # Clamped: a database clock ahead of the API host must not read as a
    # negative age, and a heartbeat from the future is still a heartbeat.
    age = max(0.0, (now - row["last_seen"]).total_seconds())
    version = str(row["version"])
    compatible = version == OVERLAY_VERSION

    if age > threshold:
        return OverlayStatus(
            installed=True, responding=False, compatible=compatible,
            reason=(
                f"overlay script last responded {age:.0f}s ago "
                f"(stale after {threshold:.0f}s); is the map server running?"
            ),
            version=version, instance_id=int(row["instance_id"]), age_seconds=age,
        )

    if not compatible:
        return OverlayStatus(
            installed=True, responding=True, compatible=False,
            reason=(
                f"installed overlay is version {version}, this API expects "
                f"{OVERLAY_VERSION}: copy the current overlay/ro_admin_overlay.txt "
                f"and @reloadscript"
            ),
            version=version, instance_id=int(row["instance_id"]), age_seconds=age,
        )

    return OverlayStatus(
        installed=True, responding=True, compatible=True,
        reason=f"overlay responding, last seen {age:.0f}s ago",
        version=version, instance_id=int(row["instance_id"]), age_seconds=age,
    )


class _Db(Protocol):
    def query(self, sql: str, params: Any = None) -> list[dict]: ...
    def execute(self, sql: str, params: Any = None) -> int: ...


def enqueue(
    db: _Db, *, char_id: int, action: Action | str, args: dict[str, int],
    requested_by: str,
) -> int:
    """Validate, then write one pending row. Returns its id.

    Validation happens first and a rejected command never touches the
    database -- a queue full of rows that can never succeed is how the
    predecessor accumulated seventy dead entries nobody read.
    """
    arg_int, arg_int2 = validate(action, args)
    return db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, created_at) "
        "VALUES (%s, %s, %s, %s, 'pending', %s, NOW())",
        (int(char_id), str(Action(action)), arg_int, arg_int2, requested_by),
    )


def read_command(db: _Db, command_id: int) -> dict | None:
    rows = db.query(
        f"SELECT id, char_id, action, arg_int, arg_int2, status, requested_by, "
        f"created_at, claimed_by, claimed_at, finished_at, error_message "
        f"FROM {COMMAND_TABLE} WHERE id = %s",
        (int(command_id),),
    )
    return rows[0] if rows else None


def read_status(db: _Db, now: datetime | None = None) -> OverlayStatus:
    """Ask the database what the overlay is doing, and report only that."""
    present = {
        r["t"].lower()
        for r in db.query(
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name IN (%s, %s)",
            (COMMAND_TABLE, HEARTBEAT_TABLE),
        )
    }
    tables_present = {COMMAND_TABLE, HEARTBEAT_TABLE} <= present

    row = None
    if tables_present:
        rows = db.query(
            f"SELECT instance_id, version, poll_ms, last_seen, NOW() AS db_now "
            f"FROM {HEARTBEAT_TABLE} WHERE id = 1"
        )
        row = rows[0] if rows else None

    # Compare against the DATABASE's clock, not the API host's. They are
    # routinely different machines, and a two-minute skew would otherwise
    # report a perfectly healthy overlay as dead.
    if now is None:
        now = row["db_now"] if row else datetime.now()

    return classify_heartbeat(tables_present=tables_present, row=row, now=now)
