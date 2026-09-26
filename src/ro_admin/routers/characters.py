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
makes `stale` false. Without Tier 2 nothing ever writes that table, synced_at
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
from ro_admin.overlay import SYNC_TABLE
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
            "synced_at is recent, because then the row was OBSERVED to match "
            "memory. stale_fields is derived from this flag and cannot "
            "contradict it: when this is false the list is empty."
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
            "is still worth knowing."
        ),
    )

    model_config = {"populate_by_name": True}


class CharacterPage(BaseModel):
    items: list[Character]
    limit: int
    offset: int


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
    row: dict, synced_at: datetime | None = None, now: datetime | None = None
) -> Character:
    """Both new arguments are optional, and their absence is the pre-Tier-2
    answer: no evidence, so an online character is stale. That is not a
    convenience for callers, it is the correct default -- an install without
    Tier 2 has nothing that could have observed the row.

    `now` must come from the SAME CLOCK as synced_at, which means the database's
    -- synced_at is a MySQL DATETIME with no timezone, written by NOW() inside
    the game server's session, and the API host is routinely a different
    machine. Callers pass the `NOW()` the same SELECT returned. Using
    datetime.now() here instead would let a two-minute skew either report a
    year-old sync as fresh or a one-second-old sync as stale, silently.
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
        fresh = age <= FRESH_WITHIN_SECONDS

    # Offline is not stale: nothing is holding newer state, so the stored row is
    # authoritative and a sync time is irrelevant to it.
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
    db: Database, suffix: str, params: list | tuple
) -> list[dict]:
    """Read `char` rows with each one's verified sync time, if there is one.

    A LEFT JOIN, not an inner one: a character that has never been synced -- or
    an install where nothing ever syncs -- must still be returned. `USING
    (char_id)` rather than `ON`, because it coalesces the join column so that
    `char_id` stays unambiguous in the select list and the ORDER BY (an
    unqualified one across the join is MySQL error 1052).

    AND THE JOIN MUST NOT BE LOAD-BEARING. ro_admin_sync only exists on a Tier 2
    install, and on any other install every endpoint here has to answer exactly
    as it did before Tier 2 was written -- so a missing table falls back to the
    plain query, and the rows then simply have no synced_at for _to_character to
    read.

    Caught from the query rather than pre-checked against information_schema,
    for the reasons routers/maps.py gives: a pre-check costs a round trip on
    EVERY request and still races a DROP between the two statements. The errno
    guard is what makes that safe to catch -- 1054 (unknown column) and 1064
    (syntax) are ProgrammingError too, and those are bugs in this file that must
    keep reaching the 500 handler rather than being silently answered without a
    sync time.

    The failed statement costs a round trip, but only on an install that does
    not have the table, and only until it does.
    """
    columns = select_clause(CHARACTER_COLUMNS)
    try:
        return db.query(
            f"SELECT {columns}, {SYNC_TABLE}.synced_at, NOW() AS db_now "
            f"FROM `char` LEFT JOIN {SYNC_TABLE} USING (char_id) {suffix}",
            params,
        )
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == _ER_NO_SUCH_TABLE:
            return db.query(f"SELECT {columns} FROM `char` {suffix}", params)
        raise


@router.get(
    "",
    response_model=CharacterPage,
    dependencies=[Depends(requires(Permission.CHARACTERS_READ))],
    summary="List characters",
)
def list_characters(
    name: str | None = Query(default=None, description="Exact match"),
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
    if account_id is not None:
        clauses.append("account_id = %s")
        params.append(account_id)
    if online is not None:
        clauses.append("online = %s")
        params.append(1 if online else 0)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    rows = _select_characters(
        db, f"{where} ORDER BY char_id LIMIT %s OFFSET %s", params
    )
    return CharacterPage(
        items=[
            # .get() rather than [], because the no-Tier-2 fallback returns rows
            # that have neither column.
            _to_character(r, synced_at=r.get("synced_at"), now=r.get("db_now"))
            for r in rows
        ],
        limit=limit, offset=offset,
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
    return _to_character(row, synced_at=row.get("synced_at"), now=row.get("db_now"))


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
