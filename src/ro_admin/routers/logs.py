"""Command-log forensics. Tier 0 -- pure database reads, works on any rAthena.

This is the largest gap against FluxCP: its logdata module has 13 actions and
the predecessor had none. Logs are also the highest-value surface for an agent
driving this API, since "who did what, when" is the question operators actually
ask.

atcommandlog has NO surrogate primary key, so ordering is by atcommand_date and
pagination is limit/offset rather than keyset. Said plainly here so nobody
assumes stable cursors.

character_timeline() also reads ro_admin_commands, the Tier 1/2 action queue,
when that table exists -- see the comment above the function for why a queued
write belongs in the same history as the game's own logs.
"""
from datetime import datetime

import pymysql
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.logtypes import decode_pick_type
from ro_admin.overlay import COMMAND_TABLE
from ro_admin.routers.items import lookup_names
from ro_admin.permissions import Permission
# Same MySQL errno, from one definition rather than a second copy that agrees
# today -- see maps.py's reasoning for why the guard is this narrow.
from ro_admin.routers.maps import _ER_NO_SUCH_TABLE

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


class CommandLogEntry(BaseModel):
    date: datetime
    account_id: int
    char_id: int
    char_name: str
    map: str
    command: str


class CommandLogPage(BaseModel):
    items: list[CommandLogEntry]
    limit: int
    offset: int


@router.get(
    "/commands",
    response_model=CommandLogPage,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="GM commands executed on the server",
)
def command_logs(
    char_name: str | None = Query(default=None, description="Exact character name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> CommandLogPage:
    where, params = "", []
    if char_name:
        where = "WHERE char_name = %s"
        params.append(char_name)
    params.extend([limit, offset])

    rows = Database(settings).query(
        f"""
        SELECT atcommand_date, account_id, char_id, char_name, `map`, command
        FROM atcommandlog
        {where}
        ORDER BY atcommand_date DESC
        LIMIT %s OFFSET %s
        """,
        params,
    )
    return CommandLogPage(
        items=[
            CommandLogEntry(
                date=r["atcommand_date"],
                account_id=r["account_id"],
                char_id=r["char_id"],
                char_name=r["char_name"],
                map=r["map"],
                command=r["command"],
            )
            for r in rows
        ],
        limit=limit,
        offset=offset,
    )


# --- economy forensics -------------------------------------------------------
#
# Zeny and items are what players actually cheat with, and what disputes are
# about. Unlike atcommandlog these tables DO have a surrogate key (`id`), so
# ordering is by id DESC -- stable even when several rows share a timestamp.


class ZenyLogEntry(BaseModel):
    date: datetime
    char_id: int
    src_id: int
    type: str
    type_name: str
    amount: int
    map: str


class ZenyLogPage(BaseModel):
    items: list[ZenyLogEntry]
    limit: int
    offset: int


@router.get(
    "/zeny",
    response_model=ZenyLogPage,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="Zeny changes, with the source of each change decoded",
)
def zeny_logs(
    char_id: int | None = Query(default=None, description="Restrict to one character"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> ZenyLogPage:
    where, params = "", []
    if char_id is not None:
        where = "WHERE char_id = %s"
        params.append(char_id)
    params.extend([limit, offset])

    rows = Database(settings).query(
        f"""
        SELECT time, char_id, src_id, type, amount, `map`
        FROM zenylog
        {where}
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """,
        params,
    )
    return ZenyLogPage(
        items=[
            ZenyLogEntry(
                date=r["time"],
                char_id=r["char_id"],
                src_id=r["src_id"],
                type=r["type"],
                type_name=decode_pick_type(r["type"]),
                amount=r["amount"],
                map=r["map"],
            )
            for r in rows
        ],
        limit=limit,
        offset=offset,
    )


class ItemLogEntry(BaseModel):
    date: datetime
    char_id: int
    type: str
    type_name: str
    item_id: int
    # Resolved server-side on purpose. Returning a bare id pushes every caller
    # toward keeping its own id-to-name table, which is exactly how the
    # predecessor ended up with 285 hardcoded items and "Unknown Item".
    item_name: str
    amount: int
    refine: int
    unique_id: int
    map: str


class ItemLogPage(BaseModel):
    items: list[ItemLogEntry]
    limit: int
    offset: int


@router.get(
    "/items",
    response_model=ItemLogPage,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="Item transactions, with the source of each transaction decoded",
)
def item_logs(
    char_id: int | None = Query(default=None, description="Restrict to one character"),
    item_id: int | None = Query(default=None, description="Restrict to one item id"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> ItemLogPage:
    clauses, params = [], []
    if char_id is not None:
        clauses.append("char_id = %s")
        params.append(char_id)
    if item_id is not None:
        clauses.append("nameid = %s")
        params.append(item_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    db = Database(settings)
    rows = db.query(
        f"""
        SELECT time, char_id, type, nameid, amount, refine, unique_id, `map`
        FROM picklog
        {where}
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """,
        params,
    )
    names = lookup_names(db, [r["nameid"] for r in rows])
    return ItemLogPage(
        items=[
            ItemLogEntry(
                date=r["time"],
                char_id=r["char_id"],
                type=r["type"],
                type_name=decode_pick_type(r["type"]),
                item_id=r["nameid"],
                item_name=names.get(r["nameid"], f"unknown item {r['nameid']}"),
                amount=r["amount"],
                refine=r["refine"],
                unique_id=r["unique_id"],
                map=r["map"],
            )
            for r in rows
        ],
        limit=limit,
        offset=offset,
    )


# --- merged character timeline ----------------------------------------------
#
# FluxCP has a page per log table, so "what happened to this character?" means
# cross-referencing several screens by hand. Merging them is cheap here and is
# the question operators (and agents) actually ask.
#
# Each source is queried with its own LIMIT, then the union is re-sorted and
# truncated. That means asking for 50 entries fetches up to 50 from each table
# first -- correct, and fine at these volumes. If a deployment ever has log
# tables large enough for that to hurt, the fix is a UNION ALL in SQL, not a
# bigger limit here.
#
# ro_admin_commands (the Tier 1/2 action queue) is one of these sources, not
# an afterthought bolted beside them. Reason: on a stock rAthena install
# log_zeny ships 0 (see overlay/README.md), so adjust_zeny writes no zenylog
# row at all -- the game's own logs simply cannot show the write an operator
# is asking "did this take?" about. The queue is the only durable record of
# that request (kind="queued"), and an empty timeline in front of that
# question is exactly what invites a double-send. `action <> 'sync_character'`
# is NOT filtered here, deliberately, unlike the write-detection subquery in
# characters.py -- sync_character rows are part of this character's own
# history too, and the operator asking "what happened" is entitled to see
# that a sync was attempted, same as any other queued action.


class TimelineEntry(BaseModel):
    """One event, from whichever source produced it.

    `summary` is prose for a human (or an agent) to read, not a value to
    parse -- its wording is not a stable contract, and it can change between
    releases without notice. `detail` is the authoritative, structured
    answer: for a `kind="queued"` entry specifically, `detail.status` is the
    real status ('pending', 'processing', 'executed' or 'failed') and must be
    what any caller branches on. Never infer status from whether a word like
    "executed" or "failed" appears in `summary`.
    """
    date: datetime
    kind: str
    char_id: int
    summary: str
    detail: dict


class TimelinePage(BaseModel):
    items: list[TimelineEntry]
    limit: int
    sources: list[str]


@router.get(
    "/timeline",
    response_model=TimelinePage,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="Everything that happened to one character, newest first, across all logs",
)
def character_timeline(
    char_id: int = Query(description="Character to build the timeline for"),
    limit: int = Query(default=100, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> TimelinePage:
    db = Database(settings)
    entries: list[TimelineEntry] = []

    for row in db.query(
        "SELECT atcommand_date, char_name, `map`, command FROM atcommandlog "
        "WHERE char_id = %s ORDER BY atcommand_date DESC LIMIT %s",
        (char_id, limit),
    ):
        entries.append(TimelineEntry(
            date=row["atcommand_date"],
            kind="command",
            char_id=char_id,
            summary=f"{row['char_name']} ran {row['command']} on {row['map']}",
            detail={"command": row["command"], "map": row["map"]},
        ))

    for row in db.query(
        "SELECT time, src_id, type, amount, `map` FROM zenylog "
        "WHERE char_id = %s ORDER BY id DESC LIMIT %s",
        (char_id, limit),
    ):
        source = decode_pick_type(row["type"])
        sign = "+" if row["amount"] >= 0 else ""
        entries.append(TimelineEntry(
            date=row["time"],
            kind="zeny",
            char_id=char_id,
            summary=f"zeny {sign}{row['amount']} via {source} on {row['map']}",
            detail={"amount": row["amount"], "type": row["type"],
                    "type_name": source, "src_id": row["src_id"], "map": row["map"]},
        ))

    item_rows = db.query(
        "SELECT time, type, nameid, amount, refine, `map` FROM picklog "
        "WHERE char_id = %s ORDER BY id DESC LIMIT %s",
        (char_id, limit),
    )

    sources = ["atcommandlog", "zenylog", "picklog"]
    try:
        command_rows = db.query(
            f"SELECT id, action, arg_int, arg_int2, status, requested_by, "
            f"created_at, finished_at, error_message FROM {COMMAND_TABLE} "
            f"WHERE char_id = %s ORDER BY id DESC LIMIT %s",
            (char_id, limit),
        )
    except pymysql.err.ProgrammingError as exc:
        # No Tier 1/2 install: the queue never existed, so it is left out of
        # `sources` rather than listed and answering nothing -- `sources` must
        # name only the tables actually read, or a caller checking it to
        # explain an empty timeline would be told a table was consulted that
        # never was.
        if exc.args and exc.args[0] == _ER_NO_SUCH_TABLE:
            command_rows = []
        else:
            raise
    else:
        sources.append(COMMAND_TABLE)

    # Names resolved here too, not just on /logs/items. The timeline is the
    # endpoint an agent reaches for first, and a summary (or a `detail`) that
    # only carries a bare item_id forces the caller to keep its own
    # id-to-name map -- the exact habit the "no privileged UI knowledge" rule
    # exists to prevent. Caught during the final end-to-end run, after the
    # rule had already been written down.
    #
    # ONE lookup_names call for BOTH picklog rows and give_item command rows,
    # not two: they are the same question (what does this item_id mean) asked
    # of two sources, and item_db has no reason to be queried for it twice in
    # what is already the same request.
    item_ids = [r["nameid"] for r in item_rows] + [
        r["arg_int"] for r in command_rows if r["action"] == "give_item"
    ]
    item_names = lookup_names(db, item_ids)

    for row in item_rows:
        source = decode_pick_type(row["type"])
        name = item_names.get(row["nameid"], f"item {row['nameid']}")
        entries.append(TimelineEntry(
            date=row["time"],
            kind="item",
            char_id=char_id,
            summary=f"{name} x{row['amount']} via {source} on {row['map']}",
            detail={"item_id": row["nameid"], "item_name": name,
                    "amount": row["amount"],
                    "refine": row["refine"], "type": row["type"],
                    "type_name": source, "map": row["map"]},
        ))

    for row in command_rows:
        status_text = row["status"]
        if row["status"] == "failed" and row["error_message"]:
            status_text = f"{row['status']} ({row['error_message']})"
        # A row that finished later than it was queued must not read as if it
        # ran at request time -- the overlay polls at most once a second, but
        # a queue backlog, a busy player who was retried, or simply reading
        # this hours later all mean created_at and finished_at can be far
        # apart. Silent (no suffix) in the common case, where the overlay
        # finished within the same wall-clock second it claimed the row.
        if row["finished_at"] is not None and row["finished_at"] != row["created_at"]:
            status_text = f"{status_text} at {row['finished_at']}"

        # Args by name, per overlay.py's _SPECS ordering (arg_int, then
        # arg_int2): give_item is (item_id, amount), adjust_zeny is (delta,)
        # with arg_int2 unused, sync_character takes none. Read there before
        # changing this -- a mismatch here mislabels what was actually queued.
        if row["action"] == "give_item":
            item_id, amount = row["arg_int"], row["arg_int2"]
            name = item_names.get(item_id, f"item {item_id}")
            summary = (
                f"give_item {name} x{amount} requested by "
                f"{row['requested_by']}: {status_text}"
            )
            args = {"item_id": item_id, "item_name": name, "amount": amount}
        elif row["action"] == "adjust_zeny":
            delta = row["arg_int"]
            sign = "+" if delta >= 0 else ""
            summary = (
                f"adjust_zeny {sign}{delta} requested by "
                f"{row['requested_by']}: {status_text}"
            )
            args = {"delta": delta}
        else:
            # sync_character, and anywhere-forward-compatible with a future
            # no-argument action: the action name and status already say what
            # happened, and there is nothing else on the row to name.
            summary = f"{row['action']} requested by {row['requested_by']}: {status_text}"
            args = {}

        entries.append(TimelineEntry(
            date=row["created_at"],
            kind="queued",
            char_id=char_id,
            summary=summary,
            detail={
                "id": row["id"], "action": row["action"], "status": row["status"],
                "requested_by": row["requested_by"], "finished_at": row["finished_at"],
                "error_message": row["error_message"], **args,
            },
        ))

    entries.sort(key=lambda e: e.date, reverse=True)
    return TimelinePage(
        items=entries[:limit],
        limit=limit,
        sources=sources,
    )
