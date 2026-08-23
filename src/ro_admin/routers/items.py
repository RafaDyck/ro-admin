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
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.permissions import Permission

router = APIRouter(prefix="/api/v1/items", tags=["items"])


class Item(BaseModel):
    item_id: int
    name: str
    name_aegis: str | None = None
    type: str | None = None
    subtype: str | None = None


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
    "/{item_id}",
    response_model=Item,
    dependencies=[Depends(requires(Permission.LOGS_READ))],
    summary="Look up an item's name and type by id",
)
def get_item(item_id: int, settings: Settings = Depends(get_settings)) -> Item:
    rows = Database(settings).query(
        "SELECT id, name_english, name_aegis, type, subtype FROM item_db WHERE id = %s",
        (item_id,),
    )
    if not rows:
        # 404 rather than a placeholder name. "Unknown Item" is what the
        # predecessor returned, and it is indistinguishable from a real answer.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no item with id {item_id}"
        )
    r = rows[0]
    return Item(
        item_id=r["id"],
        name=r["name_english"],
        name_aegis=r["name_aegis"],
        type=r["type"],
        subtype=r["subtype"],
    )
