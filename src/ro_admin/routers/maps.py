"""Map reads.

Served from ro_admin_maps, which the operator populates from their own rAthena
map cache with importers/import_maps.py. See importers/README.md.

Why an import at all: rAthena has no map list in SQL. Map names appear only as
values in log and player rows, so a database-only view sees the handful of maps
something happened on -- ten on the reference lab, against 1,241 the server
loaded. The predecessor filled that gap with 35 hardcoded maps and invented
descriptions. This reads the operator's real list instead.

The grid is GEOMETRY, and specifically it is rAthena GAT CELL TYPES rather than
a walkable flag -- see importers/mapcache.py for the mapping and for what
assuming otherwise cost. It is served raw so a consumer can distinguish water
from a snipable gap, which collapsing to a boolean would discard.

Map artwork -- textures, models, scenery -- lives in the client's GRF archives,
is Gravity's copyrighted material, and is neither imported nor served.
"""
import zlib
from typing import Any, Sequence

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.deps import get_settings, requires
from ro_admin.permissions import Permission

router = APIRouter(prefix="/api/v1/maps", tags=["maps"])

# From map_gat2cell (rathena/src/map/map.cpp:3270). Kept here rather than
# imported from importers/, because importers/ is a repo-checkout tool that is
# not part of the installed package -- the service must not depend on it.
BLOCKED_GAT = frozenset({1, 5})
SHOOTABLE_GAT = frozenset({0, 2, 3, 4, 5, 6})   # everything except 1
WATER_GAT = frozenset({3})

# The one sentence an operator gets when ro_admin_maps is not there, written
# once. /system/capabilities reports it as maps.reason; system.py imports it
# from here rather than keeping a second copy, because two copies of an
# instruction drift and the agent-facing skill quotes this one verbatim.
#
# The dependency runs system -> maps and never back, so there is no cycle.
# This module is where the sentence belongs: it names the map importer, and
# the maps router is the thing that cannot work without it.
MAPS_NOT_IMPORTED = (
    "maps not imported: run importers/maps_schema.sql, then importers/import_maps.py"
)

# ER_NO_SUCH_TABLE (mysql_com.h). pymysql puts the MySQL errno in exc.args[0].
_ER_NO_SUCH_TABLE = 1146

# So a consumer reading the generated document learns this can happen without
# having to provoke it.
_UNIMPORTED = {503: {"description": MAPS_NOT_IMPORTED}}


def _query(db: Database, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
    """Every map read goes through here, so an absent table is answered once.

    A missing ro_admin_maps used to surface as a 500 from pymysql. That says
    "this service is broken"; the truth is "this install has not been given the
    map data yet, and here is the command" -- a different fact, and the only
    one of the two an operator can act on. /system/capabilities already drew
    that distinction; the map endpoints undid it.

    503 rather than 404: the resource is fine, the data is missing. A 404 would
    say the endpoint does not exist on this server, which is a different and
    equally wrong answer.

    Caught from the query rather than pre-checked against information_schema:
    a pre-check costs a round trip on every request and still races a DROP
    between the two statements. The errno guard matters -- 1064 (syntax) and
    1054 (unknown column) are ProgrammingError too, and those really are bugs
    in here, so they must keep reaching the 500 handler.

    Deliberately narrow. A server pointed at information_schema answers 1109
    (ER_UNKNOWN_TABLE, and an OperationalError) instead, and is NOT covered
    here on purpose: that install is misconfigured, not un-imported, and
    telling its operator to run the importer would send them to fix the one
    thing that is not wrong.
    """
    try:
        return db.query(sql, params)
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == _ER_NO_SUCH_TABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MAPS_NOT_IMPORTED,
            ) from exc
        raise


class MapSummary(BaseModel):
    name: str
    width: int
    height: int
    walkable_cells: int


class MapPage(BaseModel):
    items: list[MapSummary]
    total: int
    limit: int
    offset: int


class Cell(BaseModel):
    name: str
    x: int
    y: int
    # The raw rAthena gat type, so a caller is never limited to the three
    # booleans this service happens to derive today.
    gat: int
    walkable: bool
    shootable: bool
    water: bool


def _like_literal(text: str) -> str:
    """Escape a search term for use inside a LIKE pattern.

    Parameterisation stops injection; it does not stop `%` and `_` being read
    as wildcards. Same escape as the item search, for the same reason.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _load_grid(db: Database, name: str) -> tuple[bytes, int, int]:
    """Fetch and decompress one map's cells, or 404."""
    rows = _query(
        db, "SELECT width, height, cells FROM ro_admin_maps WHERE name = %s", (name,)
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no map named {name}"
        )
    row = rows[0]
    grid = zlib.decompress(row["cells"])
    if len(grid) != row["width"] * row["height"]:
        # The import checks this, so reaching it means the row was written by
        # something else or corrupted since. Better a 500 than serving a grid
        # whose shape is a lie.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"stored grid for {name} does not match its dimensions",
        )
    return grid, row["width"], row["height"]


@router.get(
    "",
    response_model=MapPage,
    dependencies=[Depends(requires(Permission.SYSTEM_READ))],
    summary="List and search the server's maps",
    responses=_UNIMPORTED,
)
def list_maps(
    q: str | None = Query(default=None, min_length=1, max_length=24,
                          description="Substring of the map name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    settings: Settings = Depends(get_settings),
) -> MapPage:
    db = Database(settings)
    where, params = "", []
    if q is not None:
        where = "WHERE name LIKE %s ESCAPE '\\\\'"
        params.append(f"%{_like_literal(q)}%")

    total = _query(db, f"SELECT COUNT(*) AS n FROM ro_admin_maps {where}", params)[0]["n"]
    rows = _query(
        db,
        f"SELECT name, width, height, walkable_cells FROM ro_admin_maps {where} "
        f"ORDER BY name LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    return MapPage(
        items=[MapSummary(**r) for r in rows],
        total=int(total), limit=limit, offset=offset,
    )


@router.get(
    "/{name}",
    response_model=MapSummary,
    dependencies=[Depends(requires(Permission.SYSTEM_READ))],
    summary="One map's dimensions",
    responses=_UNIMPORTED,
)
def get_map(name: str, settings: Settings = Depends(get_settings)) -> MapSummary:
    rows = _query(
        Database(settings),
        "SELECT name, width, height, walkable_cells FROM ro_admin_maps WHERE name = %s",
        (name,),
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no map named {name}"
        )
    return MapSummary(**rows[0])


@router.get(
    "/{name}/cells",
    dependencies=[Depends(requires(Permission.SYSTEM_READ))],
    summary="The gat cell grid, one byte per cell",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}},
                     "description": "width*height bytes, row-major, rAthena gat types"},
               **_UNIMPORTED},
)
def get_cells(name: str, settings: Settings = Depends(get_settings)) -> Response:
    grid, width, height = _load_grid(Database(settings), name)
    return Response(
        content=grid,
        media_type="application/octet-stream",
        # A bare byte array is unusable without its row width, and a consumer
        # should not have to make a second request to learn it.
        headers={"X-Map-Width": str(width), "X-Map-Height": str(height)},
    )


@router.get(
    "/{name}/cell",
    response_model=Cell,
    dependencies=[Depends(requires(Permission.SYSTEM_READ))],
    summary="What is at one coordinate",
    description=(
        "What a warp target needs. Fetching the whole grid to ask about a "
        "single cell is the kind of thing that makes an API worth working "
        "around. Out of bounds is 422, not 'blocked' -- they are different "
        "answers. Returns the raw gat type alongside the derived flags."
    ),
    responses=_UNIMPORTED,
)
def get_cell(
    name: str,
    x: int = Query(ge=0),
    y: int = Query(ge=0),
    settings: Settings = Depends(get_settings),
) -> Cell:
    grid, width, height = _load_grid(Database(settings), name)
    if x >= width or y >= height:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"({x},{y}) is outside {name}, which is {width}x{height}",
        )
    gat = grid[y * width + x]
    return Cell(
        name=name, x=x, y=y, gat=gat,
        walkable=gat not in BLOCKED_GAT,
        shootable=gat in SHOOTABLE_GAT,
        water=gat in WATER_GAT,
    )
