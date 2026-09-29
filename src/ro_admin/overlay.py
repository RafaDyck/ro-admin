"""The overlays: the command queue and the scripts that consume it.

The queue carries typed integer arguments, never a command string. The
predecessor queued text like "@zeny Aldebaran 1000000" and parsed it inside
the NPC, which produced two failure modes this module exists to make
impossible: a character name reaching SQL, and a failed parse silently
substituting a default (the observed case granted 1,000,000 zeny).

Validation happens here, once, before anything is written. The script does no
parsing at all and therefore has nothing to fall back to.

TWO consumers now poll that one queue -- Tier 1's script and Tier 2's -- and
they are kept apart by complementary `action` predicates, not by timing. Tier 2
claims `sync_character` and nothing else; Tier 1 claims everything else. Both
halves are asserted in tests/test_overlay_artifact.py, because two consumers
racing one queue is the defect this project was built to remove.

Both tiers announce themselves the same way, through a heartbeat row, so they
are read and classified by one function rather than two that agree today. What
differs between them is data -- table names, expected version, and the
sentences that tell an operator how to install that tier -- and that lives in
TIER1 and TIER2 below.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, NamedTuple, Protocol

# Bumped together with `.version$` in overlay/ro_admin_overlay.txt whenever the
# queue contract changes. The API refuses to report Tier 1 available against a
# script declaring a different version, so an operator who copies a new release
# but forgets to reload the script is told, rather than left to wonder.
OVERLAY_VERSION = "1"

# Bumped together with `.version$` in overlay/tier2/ro_admin_tier2.txt, and
# tracked separately from OVERLAY_VERSION on purpose: the two artifacts are
# installed independently -- Tier 2 needs a recompiled map server, Tier 1 needs
# only @reloadscript -- so a change to one must not declare the other stale.
TIER2_VERSION = "1"

COMMAND_TABLE = "ro_admin_commands"
HEARTBEAT_TABLE = "ro_admin_overlay"
TIER2_TABLE = "ro_admin_tier2"
SYNC_TABLE = "ro_admin_sync"


class InvalidCommand(ValueError):
    """A command that must not be written to the queue."""


class Action(StrEnum):
    GIVE_ITEM = "give_item"
    ADJUST_ZENY = "adjust_zeny"
    # Tier 2. Consumed by overlay/tier2/ro_admin_tier2.txt, which is the only
    # consumer of this action and consumes nothing else.
    SYNC_CHARACTER = "sync_character"


# The two values consumer_tier() can return, shared with system.py so
# ActionCapability.tier is typed against the same set rather than a bare str.
ConsumerTier = Literal["tier1", "tier2"]


def consumer_tier(action: str) -> ConsumerTier:
    """Which tier's script consumes this action: "tier1" or "tier2".

    The one statement of the split. The two scripts poll one queue and divide
    it by `action` -- Tier 2 claims `sync_character` and nothing else, Tier 1
    claims everything else -- so "is anything going to run this" is a question
    about one tier, and this names which. The command guard and the
    capabilities report both ask here.

    The two scripts' SQL claim predicates that implement this split are
    asserted in tests/test_overlay_artifact.py.
    """
    return "tier2" if action == Action.SYNC_CHARACTER else "tier1"


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
    # No arguments: the char_id on the row is the whole request. An empty spec
    # is not a special case in validate() -- the loop simply does not run and
    # the trailing-zero fill below returns (0, 0), which is what the script
    # reads past for this action. Asserted in tests/test_overlay.py rather
    # than assumed.
    Action.SYNC_CHARACTER: (),
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


@dataclass(frozen=True)
class TierSpec:
    """Everything that differs between the two tiers' heartbeats.

    The classification logic does not differ at all -- "tables missing",
    "script never ran", "heartbeat too old", "wrong version" are the same four
    answers for both tiers, calibrated the same way against the script's own
    poll interval. So there is one classifier and this carries the data.

    A second copy of that logic is the thing worth avoiding: the Tier 1 version
    was tuned twice already (the poll-derived threshold, then the clock-skew
    clamp) and a Tier 2 fork would have silently missed both.
    """
    # Every table this tier's own schema.sql creates. All of them must exist
    # before the tier counts as installed: a half-run schema is not an install,
    # and Tier 2's sync table being absent would make its freshness claim
    # unanswerable even with a live heartbeat.
    tables: tuple[str, ...]
    heartbeat_table: str
    version: str
    # Named in the version-mismatch reason. Parameterised rather than fixed
    # because telling a Tier 2 operator to recopy the Tier 1 script would send
    # them to change the one file that is not wrong.
    artifact: str
    not_installed: str
    never_ran: str
    # Closes the "heartbeat too old" reason, because that one observation means
    # different things for the two tiers and only one of them is answered by
    # "is the map server running?".
    #
    # For Tier 1 that question is the whole diagnosis: the script either loaded
    # or it did not, and a dead heartbeat points at the map server.
    #
    # For Tier 2 it was observed to be actively misleading. Unloading the Tier 2
    # script in the lab left its last heartbeat row behind, so the tier
    # classified as stale rather than never-run -- and the API asked "is the map
    # server running?" in the same response that reported Tier 1 at 0s. The one
    # fact the reader could already see ruled out the only cause offered. The
    # other causes are real, specific to this tier, and cost a rebuild to fix,
    # so the reason has to name them.
    stale_hint: str = "is the map server running?"


TIER1 = TierSpec(
    tables=(COMMAND_TABLE, HEARTBEAT_TABLE),
    heartbeat_table=HEARTBEAT_TABLE,
    version=OVERLAY_VERSION,
    artifact="overlay/ro_admin_overlay.txt",
    not_installed="overlay not installed: run overlay/schema.sql against this database",
    never_ran=(
        "overlay tables exist but the script has never run: copy "
        "overlay/ro_admin_overlay.txt into npc/custom/, enable it in "
        "npc/scripts_custom.conf, then @reloadscript"
    ),
)

TIER2 = TierSpec(
    tables=(TIER2_TABLE, SYNC_TABLE),
    heartbeat_table=TIER2_TABLE,
    version=TIER2_VERSION,
    artifact="overlay/tier2/ro_admin_tier2.txt",
    not_installed=(
        "tier 2 not installed: run overlay/tier2/schema.sql against this "
        "database, then follow overlay/tier2/README.md to compile the hook"
    ),
    # The absence of a heartbeat row is the tier detection, not a symptom of
    # one. A script naming an uncompiled buildin fails to parse, npc_parse_script
    # drops it (src/map/npc.cpp:4421-4424), and the server runs on with an inert
    # NPC -- so "no row" means the hook is not in this map server's binary, and
    # the fix is a recompile, not @reloadscript. Naming the install step is the
    # difference between a capability report and a shrug.
    never_ran=(
        "tier 2 tables exist but the hook has never reported in: the compiled "
        "hook is missing or the script is not loaded -- follow "
        "overlay/tier2/README.md (src/custom/script.inc, rebuild map-server, "
        "then load overlay/tier2/ro_admin_tier2.txt and @reloadscript)"
    ),
    stale_hint=(
        "is the map server running, is overlay/tier2/ro_admin_tier2.txt still "
        "loaded, and does this build still have the compiled hook? see "
        "overlay/tier2/README.md"
    ),
)


def classify_heartbeat(
    *, tier: TierSpec, tables_present: bool, row: dict | None, now: datetime
) -> OverlayStatus:
    if not tables_present:
        return OverlayStatus(
            installed=False, responding=False, compatible=False,
            reason=tier.not_installed,
        )

    if row is None:
        return OverlayStatus(
            installed=True, responding=False, compatible=False,
            reason=tier.never_ran,
        )

    poll_ms = int(row["poll_ms"])
    threshold = max(_MIN_STALE_SECONDS, (poll_ms / 1000.0) * STALE_AFTER_POLLS)
    # Clamped: a database clock ahead of the API host must not read as a
    # negative age, and a heartbeat from the future is still a heartbeat.
    age = max(0.0, (now - row["last_seen"]).total_seconds())
    version = str(row["version"])
    compatible = version == tier.version

    if age > threshold:
        return OverlayStatus(
            installed=True, responding=False, compatible=compatible,
            reason=(
                f"overlay script last responded {age:.0f}s ago "
                f"(stale after {threshold:.0f}s); {tier.stale_hint}"
            ),
            version=version, instance_id=int(row["instance_id"]), age_seconds=age,
        )

    if not compatible:
        return OverlayStatus(
            installed=True, responding=True, compatible=False,
            reason=(
                f"installed overlay is version {version}, this API expects "
                f"{tier.version}: copy the current {tier.artifact} "
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


def _read_tier(db: _Db, tier: TierSpec, now: datetime | None = None) -> OverlayStatus:
    """Ask the database what one tier's script is doing, and report only that.

    Shared by both tiers. The placeholders are generated from len(tier.tables),
    never from the names themselves -- the table names are module constants, but
    building an IN list by concatenation is the habit this codebase does not
    have anywhere else and will not acquire here.
    """
    placeholders = ", ".join(["%s"] * len(tier.tables))
    present = {
        r["t"].lower()
        for r in db.query(
            "SELECT table_name AS t FROM information_schema.tables "
            f"WHERE table_schema = DATABASE() AND table_name IN ({placeholders})",
            tier.tables,
        )
    }
    tables_present = set(tier.tables) <= present

    row = None
    if tables_present:
        rows = db.query(
            f"SELECT instance_id, version, poll_ms, last_seen, NOW() AS db_now "
            f"FROM {tier.heartbeat_table} WHERE id = 1"
        )
        row = rows[0] if rows else None

    # Compare against the DATABASE's clock, not the API host's. They are
    # routinely different machines, and a two-minute skew would otherwise
    # report a perfectly healthy overlay as dead.
    if now is None:
        now = row["db_now"] if row else datetime.now()

    return classify_heartbeat(
        tier=tier, tables_present=tables_present, row=row, now=now
    )


def read_status(db: _Db, now: datetime | None = None) -> OverlayStatus:
    """Tier 1: is the queue's consumer alive and speaking this contract."""
    return _read_tier(db, TIER1, now)


def read_tier2_status(db: _Db, now: datetime | None = None) -> OverlayStatus:
    """Tier 2: is the compiled hook present in the running map server.

    Nothing here trusts a configuration file. A heartbeat row in ro_admin_tier2
    can only have been written by a script that parsed, and that script names
    ro_admin_sync() -- an unknown buildin makes parse_script return nullptr
    (src/map/script.cpp:2503-2518) and npc_parse_script discards it
    (src/map/npc.cpp:4421-4424), leaving an inert NPC and no row. So the row's
    existence IS the evidence that the hook is compiled in.
    """
    return _read_tier(db, TIER2, now)
