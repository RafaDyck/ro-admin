"""Parse rAthena's db/map_cache.dat.

Pure bytes in, records out -- no filesystem, no database, no rAthena checkout.
That is what lets the tests build inputs by hand instead of pinning a 3 MB
binary from one server version into the repository.

FORMAT, from rathena/src/map/map.cpp:156-167:

    struct map_cache_main_header { uint32 file_size; uint16 map_count; };
    struct map_cache_map_info { char name[MAP_NAME_LENGTH]; int16 xs; int16 ys;
                                int32 len; };

with MAP_NAME_LENGTH = 12 (src/common/mmo.hpp:163), each map's info followed by
`len` bytes of zlib-compressed cells, one byte per cell, each a gat cell
type -- NOT a walkable flag. See BLOCKED_GAT below.

THE TRAP: the main header occupies EIGHT bytes, not six. Its fields are 4 + 2,
but the struct aligns to its widest member. Reading it as six parses the first
name as "\\x00\\x00alb_ship" and every offset after it is garbage -- while still
looking like it worked, because the fields are all plausible integers. The zlib
magic (78 9c) at the expected data offset is what confirms the layout, and the
width*height check below is what turns a bad offset into an exception.

Verified against the reference lab's file: 1,263 maps, 3,014,820 of 3,014,820
bytes consumed exactly.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MAIN_HEADER = struct.Struct("<IH")      # fields; the struct itself occupies 8
MAIN_HEADER_SIZE = 8
MAP_INFO = struct.Struct("<12shhi")     # name, xs, ys, len -- 20 bytes, unpadded

# The cache stores rAthena GAT CELL TYPES, not a walkable flag. From
# map_gat2cell (rathena/src/map/map.cpp:3270):
#
#   0  walkable ground        4  ??? (walkable)
#   1  non-walkable ground    5  gap, snipable (NOT walkable)
#   2  ??? (walkable)         6  ??? (walkable)
#   3  walkable water
#
# The ??? are rAthena's own comments: 2, 4 and 6 behave exactly like 0 and
# upstream does not say what distinguishes them.
#
# So walkable is "not blocked", not "== 0". Getting this wrong is not a rounding
# error: across the reference server's 1,263 maps it undercounts walkable cells
# by 6.3%, and on 497 of them it is wrong at all -- ba_2whs02 reports ZERO
# walkable cells when 86,274 of its 129,600 are walkable. Measured on the
# reference lab: that map is 360x360, and its bytes are {1: 42,912, 3: 86,274,
# 5: 414}. It holds no gat 0 anywhere, so every walkable cell on it is water
# and the "== 0" rule finds none of them -- but a third of the map really is
# blocked ground, and it is not "entirely walkable". The original mistake
# survived because alb_ship, the first map in the file and the one that was
# spot-checked, happens to contain only 0 and 1.
BLOCKED_GAT = frozenset({1, 5})


@dataclass(frozen=True)
class MapRecord:
    name: str
    width: int
    height: int
    # rAthena's own zlib bytes, kept verbatim. See the module docstring.
    cells: bytes
    walkable_cells: int


def parse_map_cache(raw: bytes) -> list[MapRecord]:
    """Parse a whole map_cache.dat, or raise ValueError saying what is wrong.

    Every check here exists to make a misparse loud. A binary format read at
    the wrong offset yields plausible-looking integers, so "it returned some
    maps" is not evidence that it worked.
    """
    if len(raw) < MAIN_HEADER_SIZE:
        raise ValueError(f"too short to be a map cache: {len(raw)} bytes")

    file_size, map_count = MAIN_HEADER.unpack_from(raw, 0)
    if file_size != len(raw):
        raise ValueError(
            f"header file_size is {file_size} but the file is {len(raw)} bytes; "
            "truncated, concatenated, or not a map cache"
        )

    records: list[MapRecord] = []
    offset = MAIN_HEADER_SIZE
    for index in range(map_count):
        if offset + MAP_INFO.size > len(raw):
            raise ValueError(f"file ends inside the header of map {index}")
        name_raw, width, height, length = MAP_INFO.unpack_from(raw, offset)
        offset += MAP_INFO.size

        if offset + length > len(raw):
            raise ValueError(f"file ends inside the cells of map {index}")
        cells = raw[offset:offset + length]
        offset += length

        try:
            grid = zlib.decompress(cells)
        except zlib.error as exc:
            raise ValueError(f"map {index} cells are not valid zlib: {exc}") from exc

        expected = width * height
        if len(grid) != expected:
            # The check that catches a wrong offset. See the module docstring.
            raise ValueError(
                f"map {index} cell count is {len(grid)}, expected "
                f"{width}x{height}={expected}"
            )

        records.append(MapRecord(
            name=name_raw.split(b"\0", 1)[0].decode("ascii", "replace"),
            width=width,
            height=height,
            cells=cells,
            walkable_cells=sum(1 for c in grid if c not in BLOCKED_GAT),
        ))

    # No `len(records) != map_count` check here: the loop appends exactly
    # map_count records or raises, so it could never fire. A count that is too
    # high runs the loop out of bytes; one that is too low leaves the trailing
    # bytes the next check catches.
    if offset != len(raw):
        raise ValueError(
            f"{len(raw) - offset} trailing bytes after {len(records)} maps"
        )
    return records


@dataclass(frozen=True)
class LayeredMap:
    """One map, and the cache file it was taken from."""
    source: str
    record: MapRecord


def layer(sources: list[tuple[str, list[MapRecord]]]) -> list[LayeredMap]:
    """Collapse several map caches into one list, FIRST source winning.

    rAthena does not read one map cache. map_readallmaps
    (rathena/src/map/map.cpp:3910) builds this list:

        "db/" DBIMPORT "/map_cache.dat",     -- DBIMPORT is "import"
        "db/" DBPATH "map_cache.dat",        -- DBPATH is "re/" or "pre-re/"
        "db/map_cache.dat",

    with the comment "Load the map cache files in reverse order to account for
    import", reads each whole file into map_cache_buffer in that order, and then
    for every map does:

        for (const auto &cache : map_cache_buffer)
            if ((success = map_readfromcache(...)) != 0)
                break;

    -- so the FIRST buffer that holds the name is the one the server uses, and
    an operator's db/import/ cache overrides db/re/, which overrides db/. Note
    that the precedence lives in that loop, not in map_init_mapcache, which only
    slurps a file into memory.

    Within a single file the same rule applies: map_readfromcache
    (map.cpp:3662) walks entries and breaks on the first name match, so a
    duplicated name inside one cache resolves to its first occurrence.

    Getting the direction backwards is not cosmetic. On the reference lab
    db/map_cache.dat holds 1,263 maps and db/re/map_cache.dat holds 8 --
    alberta, izlude, morocc, prontera, prt_church, prt_fild05, prt_fild08 and
    prt_in. Ignoring the layering entirely loses the four most-used maps in the
    game; layering them the wrong way round would silently serve stale geometry
    for them.

    Returned sorted by name. The table is keyed by name and /api/v1/maps orders
    by name, so file order carries no information -- and a stable order makes
    the importer's own report reproducible between runs.
    """
    chosen: dict[str, LayeredMap] = {}
    for source, records in sources:
        for record in records:
            # setdefault, not assignment: first writer wins, at both levels.
            chosen.setdefault(record.name, LayeredMap(source=source, record=record))
    return sorted(chosen.values(), key=lambda m: m.record.name)
