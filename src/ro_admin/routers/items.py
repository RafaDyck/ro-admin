"""Item lookup. The server owns what things are called.

Exists because of a defect in the predecessor: its web panel resolved item
names from a hardcoded 285-entry map inside a React component, while `item_db`
held 28,525 rows. Anything outside that map rendered as "Unknown Item" even
though the database knew perfectly well what it was.

That is not a frontend bug, it is a missing endpoint. A name, label, category
or enum the client needs is something the API owes it. An agent cannot read a
React component at all, so knowledge kept only there is a capability the
product does not really have.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.permissions import Permission
from ro_admin.projections import (
    ITEM_DETAIL_COLUMNS,
    ITEM_LIST_COLUMNS,
    select_clause,
)

router = APIRouter(prefix="/api/v1/items", tags=["items"])


class Item(BaseModel):
    # item_id and name are the original published shape. Kept because they are
    # already in the OpenAPI document and the agent skill; a rename would break
    # existing callers to make two field names match.
    item_id: int
    name: str
    # The same values under the names the search endpoint uses, so a caller can
    # hold one shape for both.
    id: int
    name_english: str
    name_aegis: str | None = None
    alias_name: str | None = None
    type: str | None = None
    subtype: str | None = None
    slots: int | None = None
    weight: int | None = None
    price_buy: int | None = None
    price_sell: int | None = None
    attack: int | None = None
    defense: int | None = None
    range: int | None = None
    weapon_level: int | None = None
    armor_level: int | None = None
    equip_level_min: int | None = None
    equip_level_max: int | None = None
    refineable: int | None = None
    view: int | None = None
    # rAthena script source. None means the item has no script -- distinct from
    # "" which would read as a script that is blank.
    script: str | None = None
    equip_script: str | None = None
    unequip_script: str | None = None


class ItemSummary(BaseModel):
    id: int
    name_english: str
    name_aegis: str | None = None
    type: str | None = None
    subtype: str | None = None
    slots: int | None = None
    weight: int | None = None
    price_buy: int | None = None
    price_sell: int | None = None
    equip_level_min: int | None = None


class ItemPage(BaseModel):
    items: list[ItemSummary]
    # The number of MATCHES, not the length of this page. A UI cannot size a
    # pager without it, and returning len(items) would make every page look
    # like the last one.
    total: int
    limit: int
    offset: int


class ItemTypeCount(BaseModel):
    type: str
    count: int


class ItemTypes(BaseModel):
    types: list[ItemTypeCount]


def _like_literal(text: str) -> str:
    """Escape a user's search text for use inside a LIKE pattern.

    Parameterisation stops injection; it does NOT stop `%` and `_` being read
    as wildcards. Without this, searching for "50%" matches every item
    beginning "50" and searching for "%" matches the whole table -- a query
    that looks safe, is safe, and quietly returns nonsense.

    The backslash must be escaped first, or it would escape the escapes.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def lookup_names(db: Database, item_ids: list[int]) -> dict[int, str]:
    """Resolve many ids at once.

    Batched deliberately: enriching a page of log rows one query at a time
    would be an N+1, and the alternative -- returning bare ids and letting the
    caller keep a lookup table -- is the defect this module exists to prevent.
    """
    unique = sorted({int(i) for i in item_ids})
    if not unique:
        return {}
    placeholders = ",".join(["%s"] * len(unique))
    rows = db.query(
        f"SELECT id, name_english FROM item_db WHERE id IN ({placeholders})",
        unique,
    )
    return {r["id"]: r["name_english"] for r in rows}


@router.get(
    "",
    response_model=ItemPage,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="Search and filter the server's item database",
    description=(
        "Substring search over name_english, name_aegis and alias_name. "
        "There is no index on name_english, so a search is a full scan of "
        "item_db -- fine at 28,525 rows, and the limit is capped accordingly. "
        "An operator whose table is much larger should add their own index."
    ),
)
def search_items(
    q: str | None = Query(default=None, min_length=1, max_length=100,
                          description="Substring of the item's name"),
    type: str | None = Query(default=None, description="Exact match, e.g. 'card'"),
    subtype: str | None = Query(default=None),
    slots: int | None = Query(default=None, ge=0, le=4),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> ItemPage:
    db = Database(settings)
    clauses, params = [], []
    if q is not None:
        pattern = f"%{_like_literal(q)}%"
        clauses.append(
            "(name_english LIKE %s ESCAPE '\\\\' OR name_aegis LIKE %s ESCAPE '\\\\' "
            "OR alias_name LIKE %s ESCAPE '\\\\')"
        )
        params.extend([pattern, pattern, pattern])
    if type is not None:
        clauses.append("type = %s")
        params.append(type)
    if subtype is not None:
        clauses.append("subtype = %s")
        params.append(subtype)
    if slots is not None:
        clauses.append("slots = %s")
        params.append(slots)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = db.query(f"SELECT COUNT(*) AS n FROM item_db {where}", params)[0]["n"]
    rows = db.query(
        f"SELECT {select_clause(ITEM_LIST_COLUMNS)} FROM item_db {where} "
        f"ORDER BY id LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    return ItemPage(
        items=[ItemSummary(**r) for r in rows],
        total=int(total), limit=limit, offset=offset,
    )


@router.get(
    "/types",
    response_model=ItemTypes,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="The item types present on this server, with counts",
    description=(
        "Derived by GROUP BY, so a server with custom types gets its custom "
        "types. A client carrying its own list of types would rot the same way "
        "the predecessor's hardcoded item map rotted."
    ),
)
def item_types(settings: Settings = Depends(get_settings)) -> ItemTypes:
    rows = Database(settings).query(
        "SELECT type, COUNT(*) AS n FROM item_db WHERE type IS NOT NULL "
        "GROUP BY type ORDER BY n DESC"
    )
    return ItemTypes(
        types=[ItemTypeCount(type=r["type"], count=int(r["n"])) for r in rows]
    )


# Declared AFTER the two routes above on purpose: FastAPI matches in
# registration order, so `/types` placed below this one would be swallowed by
# `{item_id}` and answered with a 422 for "types" not being an int.
@router.get(
    "/{item_id}",
    response_model=Item,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="Look up one item in full, including its script",
)
def get_item(item_id: int, settings: Settings = Depends(get_settings)) -> Item:
    rows = Database(settings).query(
        f"SELECT {select_clause(ITEM_DETAIL_COLUMNS)} FROM item_db WHERE id = %s",
        (item_id,),
    )
    if not rows:
        # 404 rather than a placeholder name. "Unknown Item" is what the
        # predecessor returned, and it is indistinguishable from a real answer.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no item with id {item_id}"
        )
    r = rows[0]
    return Item(item_id=r["id"], name=r["name_english"], **r)
