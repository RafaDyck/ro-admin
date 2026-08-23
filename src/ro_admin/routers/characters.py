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
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.permissions import Permission
from ro_admin.projections import (
    CHARACTER_COLUMNS, CHARACTER_VOLATILE, select_clause,
)
from ro_admin.routers.items import lookup_names

router = APIRouter(prefix="/api/v1/characters", tags=["characters"])


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
            "True while the character is online. The map server holds this "
            "state in memory and flushes on logout or every autosave_time "
            "(300s by default), so the values in stale_fields may be up to "
            "that old. They are reported anyway -- old is more useful than "
            "absent -- but they are not live."
        )
    )
    stale_fields: list[str]

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
    stale: bool


def _to_character(row: dict) -> Character:
    online = bool(row["online"])
    return Character(
        online=online,
        # stale IS online, today. They are separate fields because they answer
        # separate questions -- "is someone playing this character" and "can I
        # trust these numbers" -- and a Tier 1 install could later answer the
        # second one better without changing the first.
        stale=online,
        stale_fields=sorted(CHARACTER_VOLATILE) if online else [],
        **{k: row[k] for k in CHARACTER_COLUMNS if k != "online"},
    )


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

    rows = db.query(
        f"SELECT {select_clause(CHARACTER_COLUMNS)} FROM `char` {where} "
        f"ORDER BY char_id LIMIT %s OFFSET %s",
        params,
    )
    return CharacterPage(
        items=[_to_character(r) for r in rows], limit=limit, offset=offset
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
    rows = Database(settings).query(
        f"SELECT {select_clause(CHARACTER_COLUMNS)} FROM `char` WHERE char_id = %s",
        (char_id,),
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no character with id {char_id}",
        )
    return _to_character(rows[0])


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
