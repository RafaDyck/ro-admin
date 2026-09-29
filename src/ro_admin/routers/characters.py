"""Character reads, and an honest statement of how fresh they are.

The `char` table is a MIRROR of state the map server holds in memory. While a
character is online the map server does not write through: it flushes on
logout, or every autosave_time (300s by default). Measured during the Tier 1
work -- an in-game +777 zeny change was absent from the table at t+0s, +2s,
+5s, +10s and +20s, then read exactly +777 after logout.

That is the founding incident of this project inverted. There an impostor
process wrote straight to the database, making it fresh but WRONG while the
game was right, and a human caught it by reading 592,213 off the client while
the database insisted on 7,777,777. Either way the lesson is the same: the
`char` row is not authoritative for a character who is logged in.

So these responses label it. `stale` and `stale_fields` are present on every
character, true or false, because a field that only appears when something is
wrong is a field callers learn to stop reading.

WITH TIER 2 INSTALLED, `stale` stops being a restatement of `online`. The Tier
2 overlay flushes a connected character through the compiled hook, then reads
`char`.zeny back and compares it against live memory, writing
ro_admin_sync.synced_at only when they match. So synced_at is evidence -- "the
stored row was OBSERVED to match live memory at this time" -- and a recent one
makes `stale` false -- UNLESS a queued write (give_item, adjust_zeny) was
ATTEMPTED AFTER that sync. "Attempted", not "executed": overlay/ro_admin_overlay.txt
and overlay/tier2/ro_admin_tier2.txt both claim a row (status='processing',
claimed_at=NOW()) BEFORE touching the game, and a row that later reads
'failed' can still have moved live state first -- adjust_zeny's own comment
says so for a partial debit (truncated to the balance) or a gain clamped at
MAX_ZENY. So every claimed row (processing, executed, OR failed) counts as a
write that needs checking against the sync, not just the ones that finished
clean; see _to_character and _select_characters below for how that write is
found and compared. Without Tier 2 nothing ever writes ro_admin_sync, synced_at
is null, and every answer here is exactly what it was before.

How far that evidence reaches is the delicate part, and it stops at the `char`
row. Zeny travels inside mmo_charstatus in packet 0x2b01, so an observed zeny
is evidence the row landed; the inventory and cart go out earlier, in separate
intif_storage_save calls (rathena-source/src/map/chrif.cpp:299-302), and nothing
observes those. get_inventory's staleness therefore stays keyed on `online`.
"""
from datetime import datetime

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.like import like_literal
from ro_admin.overlay import COMMAND_TABLE, SYNC_TABLE
from ro_admin.permissions import Permission
from ro_admin.projections import (
    CHARACTER_COLUMNS, CHARACTER_VOLATILE, select_clause,
)
from ro_admin.routers.items import lookup_names
# The same MySQL errno, from one definition rather than two that agree today.
# maps.py's reasoning for why the guard is narrow -- 1054 and 1064 are
# ProgrammingError too, and those really are bugs in here -- applies unchanged.
from ro_admin.routers.maps import _ER_NO_SUCH_TABLE

router = APIRouter(prefix="/api/v1/characters", tags=["characters"])


def _missing_table_error(exc: pymysql.err.ProgrammingError) -> bool:
    """True for MySQL 1146 (unknown table) -- the one ProgrammingError this
    file treats as 'the tier isn't installed' rather than a bug. Narrow on
    purpose: 1054 (unknown column) and 1064 (syntax) are ProgrammingError too,
    and those are real defects in the SQL below that must keep reaching the
    500 handler."""
    return bool(exc.args) and exc.args[0] == _ER_NO_SUCH_TABLE


def _missing_table_names(exc: pymysql.err.ProgrammingError, table: str) -> bool:
    """True when a 1146 names THIS table specifically, not some other one in
    the same statement -- and not merely a table (or database) whose name
    happens to CONTAIN this one, e.g. a `ro_admin_sync_test` table on some
    other install must not be mistaken for `ro_admin_sync`.

    MySQL 8's message is `Table '<database>.<table>' doesn't exist` --
    verified live against this project's lab database. The table name always
    appears verbatim right before the closing quote, preceded by the
    database name and a dot, so matching on `.<table>'` anchors to that exact
    boundary rather than doing a bare substring check, which is what lets
    _select_characters skip a retry that is guaranteed to fail the same way a
    second time without also skipping it for the wrong table.
    """
    return len(exc.args) > 1 and f".{table}'" in str(exc.args[1])

# How long a verified sync keeps a character's row out of `stale`.
#
# Chosen against what it competes with. What it replaces is autosave_time, 300s,
# so anything well under that is an improvement. The overlay polls every 1000ms,
# so a caller who needs better than this can always queue a sync and get an
# answer within about a second. And what bounds it from below is that a
# character can spend zeny the instant after a sync completes -- no window is
# ever *safe*, so this is not a correctness knob. It is how long an observation
# stays worth reporting.
#
# 60s: a minute-old verified read is still the best evidence anyone has, and
# short enough that a caller who needs more knows to ask rather than trusting a
# number that may be five minutes old. Deliberately not per-request: `stale`
# must mean one thing across every response, or two callers comparing notes on
# the same character disagree about a word.
FRESH_WITHIN_SECONDS = 60.0


class Character(BaseModel):
    char_id: int
    account_id: int
    name: str
    class_: int = Field(
        alias="class",
        description=(
            "rAthena job id. Returned as a bare id on purpose: unlike item_db, "
            "there is no job table in the database -- rAthena keeps job data in "
            "YAML on the server's filesystem, which this API never reads. The "
            "game server can resolve it (the script function jobname() does "
            "exactly that), so a name is a Tier 1 capability, not a Tier 0 one."
        ),
    )
    base_level: int
    job_level: int
    base_exp: int
    job_exp: int
    zeny: int
    status_point: int
    skill_point: int
    party_id: int
    guild_id: int
    last_map: str
    last_x: int
    last_y: int
    online: bool = Field(
        description=(
            "The CHAR server's record, written at session start and end. It is "
            "not the same question as whether the map server currently holds a "
            "session; the two disagree after a crash."
        )
    )
    last_login: datetime | None = None
    delete_date: int
    unban_time: int
    stale: bool = Field(
        description=(
            "Whether the values in stale_fields might be behind the game. True "
            "while the character is online and nothing has verified the stored "
            "row recently: the map server holds this state in memory and "
            "flushes on logout or every autosave_time (300s by default), so "
            "those values may be up to that old. They are reported anyway -- "
            "old is more useful than absent -- but they are not live. False "
            "when the character is offline (nothing is holding newer state, so "
            "the row is authoritative), and false on a Tier 2 install when "
            "synced_at is recent AND no queued write (give_item, adjust_zeny) "
            "has been ATTEMPTED since -- because then, and only then, the row "
            "was OBSERVED to match memory and nothing has since tried to move "
            "it. 'Attempted' and not 'executed': the overlay claims a row "
            "before touching the game, and a row that later reads 'failed' "
            "can still have changed live zeny first (a partial debit or a "
            "MAX_ZENY clamp), so a claimed write counts here whether it "
            "finished 'processing', 'executed' or 'failed'. stale_fields is "
            "derived from this flag and cannot contradict it: when this is "
            "false the list is empty."
        )
    )
    stale_fields: list[str]
    synced_at: datetime | None = Field(
        default=None,
        description=(
            "When this character's stored row was last OBSERVED to match the "
            "map server's live memory, or null if that has never happened. "
            "Requires Tier 2, which is the only thing that writes it, so it is "
            "null on every Tier 0 and Tier 1 install. "
            "NOT 'when a flush was requested': the Tier 2 overlay reads "
            "`char`.zeny back and compares it against the attached player's "
            "live Zeny, and records nothing unless they match -- chrif_save "
            "hands packet 0x2b01 to the char server and returns, so the commit "
            "happens afterwards and the return code proves nothing. "
            "COVERS THE `char` ROW, NOT THE INVENTORY. Zeny travels inside "
            "mmo_charstatus in that same packet, so every field in stale_fields "
            "is covered; inventory and cart are sent by separate earlier calls "
            "(src/map/chrif.cpp:299-302) that nothing reads back, which is why "
            "GET /characters/{char_id}/inventory reports its own staleness. "
            "Reported even when stale is true -- when the evidence went stale "
            "is still worth knowing. A non-null value here does NOT by itself "
            "mean stale is false: a write ATTEMPTED after this timestamp "
            "reopens staleness even though the sync itself is recent, whether "
            "or not that write finished successfully -- see stale."
        ),
    )

    model_config = {"populate_by_name": True}


class CharacterPage(BaseModel):
    items: list[Character]
    limit: int
    offset: int
    has_more: bool = Field(
        description=(
            "Whether a further page exists. Reported instead of a total: "
            "counting has to visit every match, so its cost grows with the "
            "player base, while this costs one extra row."
        )
    )


class InventoryEntry(BaseModel):
    item_id: int
    item_name: str
    amount: int
    refine: int
    identified: bool
    equipped: bool


class Inventory(BaseModel):
    char_id: int
    items: list[InventoryEntry]
    # Same reason as the character response: `inventory` is flushed on the same
    # schedule as `char`, so a grant made seconds ago may not be here yet.
    #
    # Unlike the character response, Tier 2 does NOT improve this -- see the
    # comment where it is set. There is no synced_at field here for the same
    # reason: this service has never observed an inventory land, so it has no
    # such time to report and will not imply that it does.
    stale: bool


def _to_character(
    row: dict,
    synced_at: datetime | None = None,
    now: datetime | None = None,
    written_at: datetime | None = None,
) -> Character:
    """All three new arguments are optional, and their absence is the
    pre-Tier-2 answer: no evidence, so an online character is stale. That is
    not a convenience for callers, it is the correct default -- an install
    without Tier 2 has nothing that could have observed the row.

    `now` must come from the SAME CLOCK as synced_at, which means the database's
    -- synced_at is a MySQL DATETIME with no timezone, written by NOW() inside
    the game server's session, and the API host is routinely a different
    machine. Callers pass the `NOW()` the same SELECT returned. Using
    datetime.now() here instead would let a two-minute skew either report a
    year-old sync as fresh or a one-second-old sync as stale, silently.

    `written_at` is when this character's most recently ATTEMPTED
    give_item/adjust_zeny row moved (or may have moved) live state -- None
    means no known write, which is also the pre-Tier-2 default (and the
    default before write evidence was added here). "Attempted" rather than
    "executed" on purpose: both overlay
    scripts claim a row (status='processing', claimed_at=NOW()) strictly
    before the game is touched, and a row that ends 'failed' can still have
    changed live zeny first -- adjust_zeny's own post-condition comment notes
    a partial debit (truncated to the balance) and a MAX_ZENY clamp as cases
    where the row fails but zeny moved anyway. So `written_at` is read off
    every claimed row (processing, executed, or failed), using
    COALESCE(finished_at, claimed_at) so a still-'processing' row -- no
    finished_at yet -- is still counted, from the moment it was claimed. This
    only ever errs toward MORE staleness, never less: a failed write that
    never touched the game (character offline, player busy) still counts and
    can hold `stale` true until the next sync, which is the safe direction to
    be wrong in. It comes from the same correlated subquery, off the same
    MySQL clock, as `now` and `synced_at` -- see _select_characters -- so
    comparing it against synced_at directly is legitimate for the same reason
    comparing now against synced_at is.
    """
    online = bool(row["online"])

    # Evidence, with a shelf life. Both halves have to be present: a synced_at
    # with no clock to compare it against is not evidence of anything.
    fresh = False
    if online and synced_at is not None and now is not None:
        # Clamped, like the heartbeat age in overlay.py. The two timestamps come
        # from one clock so a negative age should be impossible; if one turns up
        # anyway, a sync from a moment ago is still a sync and must not read as
        # 300 seconds stale.
        age = max(0.0, (now - synced_at).total_seconds())
        within_window = age <= FRESH_WITHIN_SECONDS

        # A queued write ATTEMPTED after the sync means the row may have moved
        # again after the evidence was taken: the sync proves what the row
        # looked like at synced_at, not what it looks like now. Without this,
        # an operator could see "verified against the game at T" beside a
        # pre-change zeny sitting right next to "executed" -- the founding
        # incident (src/ro_admin/routers/characters.py's module docstring) in
        # reverse: the row would look fresh and wrong instead of stale and
        # honest. `written_at` excludes sync_character rows -- a sync IS the
        # evidence, not a write that needs to be checked against it.
        #
        # A strict `>`: a sync and a write landing in the same wall-clock
        # second are ambiguous about which happened first, and staying stale
        # is the safe answer either way.
        not_undone_by_a_later_write = written_at is None or synced_at > written_at

        fresh = within_window and not_undone_by_a_later_write

    # Offline is not stale: nothing is holding newer state, so the stored row is
    # authoritative and a sync time (or a write time) is irrelevant to it.
    stale = online and not fresh
    return Character(
        online=online,
        stale=stale,
        # Derived from `stale`, never computed a second way. The two cannot
        # disagree because there is only one decision -- a response saying
        # stale=false while naming ten stale fields would be worse than either
        # answer alone.
        stale_fields=sorted(CHARACTER_VOLATILE) if stale else [],
        synced_at=synced_at,
        **{k: row[k] for k in CHARACTER_COLUMNS if k != "online"},
    )


def _select_characters(
    db: Database, suffix: str, params: list | tuple, order_by: str | None = None
) -> list[dict]:
    """Read `char` rows with each one's verified sync time and latest write,
    if there is one of either.

    A LEFT JOIN, not an inner one: a character that has never been synced -- or
    an install where nothing ever syncs -- must still be returned. `USING
    (char_id)` rather than `ON`, because it coalesces the join column so that
    `char_id` stays unambiguous in the select list and the ORDER BY (an
    unqualified one across the join is MySQL error 1052).

    WHEN order_by IS GIVEN, `char` IS PAGED BEFORE THE JOIN, not after. The
    naive shape -- join first, then ORDER BY/LIMIT on the joined result --
    hands ordering to the optimizer, and once ro_admin_sync is small (true of
    almost any real install) MySQL is liable to plan it as a hash join: read
    every WHERE-matching row, join it, sort the lot, and only THEN apply
    LIMIT -- throwing away the index order the range scan gave it for free.
    Measured: 139 ms -> 0.26 ms for an 83,333-match `K%` prefix on 1,000,000
    characters with 3 synced rows; the default list (ORDER BY char_id) 999 ms
    -> 0.12 ms the same way. So `suffix` carries its own complete
    WHERE/ORDER BY/LIMIT/OFFSET and is run first, inside a derived table that
    `char` alone can satisfy straight from its index; the sync join is then
    applied to at most one page of rows. The outer ORDER BY repeats the same
    column because a JOIN does not preserve a derived table's row order --
    omitting it would leave the response order unspecified again.

    order_by IS None for a read that never had a LIMIT worth protecting --
    get_character's single row by primary key, and account_characters'
    unlimited per-account listing (already bounded by character_slots, a
    handful of rows). There the join stays the old, flat shape: there is no
    over-fetch for paging-first to avoid.

    WRITTEN_AT is a correlated subquery over ro_admin_commands, added to the
    outer SELECT in both shapes -- in the paged form it runs over the already
    paged derived table `c`, exactly like the sync join, so it still costs at
    most one page's worth of correlated lookups rather than one per match.
    `action <> 'sync_character'` because a sync is the evidence itself, not a
    write that needs to be checked against it (see _to_character). It answers
    a DIFFERENT question from the sync join -- "was this row written after the
    evidence was taken" rather than "is there evidence at all" -- so it is not
    folded into that join; a character can have a write with no sync, a sync
    with no write, both, or neither. It reads `status IN (...)` and
    `COALESCE(finished_at, claimed_at)`, not just `status = 'executed'` and
    `finished_at` -- see written_at_sql()'s own docstring below for why a
    claimed-but-not-yet-successful row still counts.

    THE SUBQUERY'S OWN INDEX is overlay/schema.sql's `idx_char_status
    (char_id, status, action, finished_at, claimed_at)`, not the narrower
    `idx_char_id (char_id, id)` the paged-join reordering above first shipped
    with. Measured in this
    project's lab, where one character's rows dominate the table (86% of
    1,402 rows belong to one char_id): EXPLAIN ANALYZE on `idx_char_id` reads
    every one of that character's rows via a non-covering index lookup and
    then filters status/action against the base row (~1ms observed); an index
    that also carries status, action, finished_at and claimed_at lets MySQL
    answer the whole subquery as a covering range scan with no base-row reads
    at all (~0.4ms observed, and a much bigger win as the table grows,
    because it is the base-row I/O that scales with row count, not the index
    scan). `idx_char_id` stays -- the timeline's queue query (`GET
    /logs/timeline`) still ORDERs BY id on it, and a status-only or
    char_id-only index does not serve that.

    AND NEITHER THE JOIN NOR THE SUBQUERY MAY BE LOAD-BEARING. ro_admin_sync
    and ro_admin_commands are independent Tier 2/Tier 1 artifacts -- an install
    can have one table without the other, e.g. a hand-built Tier 2 with no
    command queue -- and on any install missing either, every endpoint here
    has to answer exactly as it did before that table existed. So a missing
    table is retried with successively less of the query rather than treated
    as an error:

      1. sync join + written_at subquery (both tables present);
      2. sync join alone, written_at always None (ro_admin_commands missing --
         a missing command queue must not also erase ro_admin_sync's
         evidence, since that table answers an unrelated question);
      3. the plain `char`-only query (ro_admin_sync missing too, or a Tier 0
         install with neither).

    Stage 2 is SKIPPED, straight to stage 3, when the 1146 from stage 1 names
    ro_admin_sync itself rather than ro_admin_commands: retrying the sync-only
    query would just fail the identical way a second time, since it still
    joins the table that is not there, and written_at is meaningless without a
    synced_at to compare it against anyway. Skipping it matters because "no
    Tier 2" (Tier 0, or Tier 1 without Tier 2) is the common case, not the
    exception -- see _missing_table_names().

    Caught from the query rather than pre-checked against information_schema,
    for the reasons routers/maps.py gives: a pre-check costs a round trip on
    EVERY request and still races a DROP between the two statements. The errno
    guard is what makes that safe to catch at each stage -- 1054 (unknown
    column) and 1064 (syntax) are ProgrammingError too, and those are bugs in
    this file that must keep reaching the 500 handler rather than being
    silently answered with less evidence than the schema actually offers.

    A failed statement costs a round trip, but only on an install missing that
    table, and (after the stage-2 skip above) at most once per request even
    when the missing table is ro_admin_sync itself.
    """
    columns = select_clause(CHARACTER_COLUMNS)

    def written_at_sql(char_ref: str) -> str:
        """The correlated write-evidence subquery. `char_ref` is filled in per
        shape below: the paged form correlates against the derived table's
        alias (`c.char_id`), the flat form against `char` itself
        (`` `char`.char_id ``) -- USING coalesces the join's char_id, so inside
        a subquery, which has its own FROM, only the qualified table name
        still reaches the outer row.

        `status IN ('processing', 'executed', 'failed')`, not
        `= 'executed'`: both overlay scripts claim a row -- status moves to
        'processing', claimed_at = NOW() -- strictly BEFORE the game is
        touched (overlay/ro_admin_overlay.txt and
        overlay/tier2/ro_admin_tier2.txt, both right after the compare-and-
        swap UPDATE). A row that later reads 'failed' can still have changed
        live state first: adjust_zeny's own post-condition comment names a
        partial debit (truncated to the balance the player has) and a
        MAX_ZENY clamp on a gain as cases where the write fails but zeny
        moved anyway. Excluding 'pending' is what still lets an UNCLAIMED
        row -- nothing has touched the game for it yet -- leave written_at
        alone.

        `COALESCE(finished_at, claimed_at)`, not `finished_at` alone: a row
        still 'processing' has no finished_at yet, and claimed_at -- stamped
        before the game is touched, same as above -- is the earliest honest
        time it might have written. This can only make written_at EARLIER
        than the row's real write (or, for a row that never reached the game
        at all -- character offline, player busy in another script -- a time
        nothing actually happened), never later. That is the safe direction:
        the worst it costs is a character staying `stale` until the next
        sync, never a stale write being reported fresh.
        """
        return (
            f"(SELECT MAX(COALESCE(cmd.finished_at, cmd.claimed_at)) "
            f"FROM {COMMAND_TABLE} cmd WHERE cmd.char_id = {char_ref} "
            f"AND cmd.status IN ('processing', 'executed', 'failed') "
            f"AND cmd.action <> 'sync_character') AS written_at"
        )

    if order_by is not None:
        base = (
            f"FROM (SELECT {columns} FROM `char` {suffix}) AS c "
            f"LEFT JOIN {SYNC_TABLE} USING (char_id) "
        )
        with_written_at = (
            f"SELECT c.*, {SYNC_TABLE}.synced_at, "
            f"{written_at_sql('c.char_id')}, NOW() AS db_now "
            f"{base}ORDER BY {order_by}"
        )
        sync_only = (
            f"SELECT c.*, {SYNC_TABLE}.synced_at, NOW() AS db_now "
            f"{base}ORDER BY {order_by}"
        )
    else:
        base = f"FROM `char` LEFT JOIN {SYNC_TABLE} USING (char_id) {suffix}"
        with_written_at = (
            f"SELECT {columns}, {SYNC_TABLE}.synced_at, "
            f"{written_at_sql('`char`.char_id')}, "
            f"NOW() AS db_now {base}"
        )
        sync_only = f"SELECT {columns}, {SYNC_TABLE}.synced_at, NOW() AS db_now {base}"

    plain = f"SELECT {columns} FROM `char` {suffix}"

    try:
        return db.query(with_written_at, params)
    except pymysql.err.ProgrammingError as exc:
        if not _missing_table_error(exc):
            raise
        if _missing_table_names(exc, SYNC_TABLE):
            # ro_admin_sync is the table that is missing, not ro_admin_commands
            # -- retrying `sync_only` (which still joins ro_admin_sync) would
            # fail the identical way a second time, and written_at means
            # nothing without a synced_at to compare it against regardless.
            # Go straight to the plain query.
            return db.query(plain, params)
        try:
            return db.query(sync_only, params)
        except pymysql.err.ProgrammingError as exc2:
            if _missing_table_error(exc2):
                return db.query(plain, params)
            raise


@router.get(
    "",
    response_model=CharacterPage,
    dependencies=[Depends(requires(Permission.CHARACTERS_READ))],
    summary="List characters",
)
def list_characters(
    name: str | None = Query(default=None, description="Exact match"),
    name_prefix: str | None = Query(
        default=None, min_length=1, max_length=30,
        description=(
            "Names beginning with this text, ordered by name. Served by "
            "rAthena's own unique index on `char`.`name` as a range scan, so it "
            "stays fast at any number of characters. Case sensitivity follows "
            "the database collation (case- and accent-insensitive on a MySQL 8 "
            "default install). There is deliberately no substring search: a leading "
            "wildcard cannot use the index, and was measured at 1,452 ms per "
            "query on 1,000,000 characters."
        ),
    ),
    account_id: int | None = Query(default=None, ge=1),
    online: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> CharacterPage:
    db = Database(settings)
    clauses, params = [], []
    if name is not None:
        clauses.append("name = %s")
        params.append(name)
    if name_prefix is not None:
        clauses.append("name LIKE %s ESCAPE '\\\\'")
        params.append(like_literal(name_prefix) + "%")
    if account_id is not None:
        clauses.append("account_id = %s")
        params.append(account_id)
    if online is not None:
        clauses.append("online = %s")
        params.append(1 if online else 0)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    # By the searched column when there is one, so the index returns rows
    # already in order and LIMIT stops the scan after one page. By id
    # otherwise, as before.
    order = "name" if name_prefix is not None else "char_id"
    # One row past the page answers "is there more" without a COUNT(*).
    params.extend([limit + 1, offset])

    rows = _select_characters(
        db, f"{where} ORDER BY {order} LIMIT %s OFFSET %s", params, order_by=order
    )
    return CharacterPage(
        items=[
            # .get() rather than [], because the no-Tier-2 fallback returns rows
            # that have neither column.
            _to_character(
                r, synced_at=r.get("synced_at"), now=r.get("db_now"),
                written_at=r.get("written_at"),
            )
            for r in rows[:limit]
        ],
        limit=limit, offset=offset, has_more=len(rows) > limit,
    )


@router.get(
    "/{char_id}",
    response_model=Character,
    dependencies=[Depends(requires(Permission.CHARACTERS_READ))],
    summary="One character",
)
def get_character(
    char_id: int, settings: Settings = Depends(get_settings)
) -> Character:
    rows = _select_characters(
        Database(settings), "WHERE char_id = %s", (char_id,)
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no character with id {char_id}",
        )
    row = rows[0]
    return _to_character(
        row, synced_at=row.get("synced_at"), now=row.get("db_now"),
        written_at=row.get("written_at"),
    )


@router.get(
    "/{char_id}/inventory",
    response_model=Inventory,
    dependencies=[Depends(requires(Permission.CHARACTERS_READ))],
    summary="A character's inventory, with item names resolved",
)
def get_inventory(
    char_id: int, settings: Settings = Depends(get_settings)
) -> Inventory:
    db = Database(settings)
    char = db.query("SELECT online FROM `char` WHERE char_id = %s", (char_id,))
    if not char:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no character with id {char_id}",
        )
    rows = db.query(
        "SELECT nameid, amount, refine, identify, equip FROM inventory "
        "WHERE char_id = %s ORDER BY nameid",
        (char_id,),
    )
    names = lookup_names(db, [r["nameid"] for r in rows])
    return Inventory(
        char_id=char_id,
        # STILL KEYED ON `online`, AND DELIBERATELY NOT ON synced_at.
        #
        # A Tier 2 sync does flush the inventory -- the hook passes
        # CSAVE_INVENTORY|CSAVE_CART -- but nothing observes it land. The
        # overlay's post-condition reads `char`.zeny back, and zeny travels
        # inside mmo_charstatus in packet 0x2b01 while the inventory and cart go
        # out earlier, in separate intif_storage_save calls
        # (rathena-source/src/map/chrif.cpp:299-302). Those are different writes
        # to a different table by a different code path, and a verified zeny is
        # no evidence at all about them.
        #
        # So reporting this inventory as fresh on the strength of a `char` row's
        # synced_at would be borrowed evidence: a claim about one table proved by
        # a read of another. It would also be the founding defect in miniature --
        # a fresh-looking answer that nobody checked -- which is precisely what
        # the sync's read-back exists to avoid.
        stale=bool(char[0]["online"]),
        items=[
            InventoryEntry(
                item_id=r["nameid"],
                # Resolved here so no consumer needs its own id-to-name table.
                item_name=names.get(r["nameid"], f"item {r['nameid']}"),
                amount=r["amount"],
                refine=r["refine"],
                identified=bool(r["identify"]),
                equipped=r["equip"] != 0,
            )
            for r in rows
        ],
    )
