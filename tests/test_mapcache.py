"""The map_cache.dat parser, against bytes built in the test.

Deliberately synthetic rather than a checked-in fixture: a 3 MB binary from one
rAthena version would be both bundled game data and a version pin. Building the
bytes here also means the test states the format, which is the thing that was
actually hard to get right.
"""
import struct
import zlib

import pytest

from importers.mapcache import MapRecord, layer, parse_map_cache


def _cache(maps: list[tuple[str, int, int]]) -> bytes:
    """Build a map_cache.dat the way rAthena writes one.

    Header is uint32 + uint16 padded to EIGHT bytes -- the struct aligns to its
    widest member. Reading it as six is the mistake this parser exists to not
    make.
    """
    body = b""
    for name, xs, ys in maps:
        cells = zlib.compress(bytes(xs * ys))
        body += struct.pack("<12shhi", name.encode(), xs, ys, len(cells)) + cells
    size = 8 + len(body)
    return struct.pack("<IH", size, len(maps)) + b"\x00\x00" + body


def _parse_one(grid: bytes) -> MapRecord:
    """Build a one-map cache around an EXPLICIT grid and return the record.

    Separate from _cache(), which only ever builds all-zero grids -- every cell
    walkable ground, no other gat type anywhere in it. That is part of why the
    "walkable means == 0" mistake went unnoticed: no test could see a byte it
    got wrong.
    """
    cells = zlib.compress(grid)
    raw = struct.pack("<12shhi", b"tiny", len(grid), 1, len(cells)) + cells
    cache = struct.pack("<IH", 8 + len(raw), 1) + b"\x00\x00" + raw
    return parse_map_cache(cache)[0]


def test_parses_a_single_map():
    records = parse_map_cache(_cache([("prontera", 200, 200)]))
    assert len(records) == 1
    assert records[0].name == "prontera"
    assert records[0].width == 200
    assert records[0].height == 200


def test_parses_several_maps_in_order():
    records = parse_map_cache(_cache([("alb_ship", 200, 200), ("payon", 160, 160)]))
    assert [r.name for r in records] == ["alb_ship", "payon"]
    assert records[1].width == 160


def test_the_name_is_trimmed_of_its_null_padding():
    """char[12] is null-padded, and a name carrying \\x00 would poison every
    comparison and every URL built from it."""
    assert "\x00" not in parse_map_cache(_cache([("payon", 160, 160)]))[0].name


def test_the_compressed_cells_are_kept_verbatim():
    """Stored as rAthena has them: ~3MB across 1,263 maps, against ~90MB
    decompressed for data that is mostly zeros."""
    record = parse_map_cache(_cache([("prontera", 10, 10)]))[0]
    assert zlib.decompress(record.cells) == bytes(100)


def test_walkable_cells_counts_every_walkable_gat_type_not_just_zero():
    """The bytes are rAthena GAT CELL TYPES, not a walkable flag.
    map_gat2cell (rathena/src/map/map.cpp:3270) makes 0, 2, 3, 4 and 6 walkable
    and only 1 and 5 blocked.

    Counting == 0 undercounts by 6.3% across the reference server's 1,263 maps,
    and on ba_2whs02 it reports ZERO walkable cells for a map with 86,274
    walkable cells out of 129,600 -- every one of them gat 3, because that map
    holds no gat 0 at all. That mistake survived a spot check because alb_ship
    -- the first map in the file -- happens to contain only 0 and 1.

    Counted at parse time so a listing can show density without decompressing
    every blob to answer one query.
    """
    grid = bytes([0, 1, 2, 3, 4, 5, 6])       # one of each
    assert _parse_one(grid).walkable_cells == 5    # all but 1 and 5


def test_a_map_of_walkable_water_is_not_reported_as_impassable():
    """gat 3 is walkable water. Whole maps are made of it."""
    assert _parse_one(bytes([3] * 100)).walkable_cells == 100


def test_a_gap_is_not_walkable():
    """gat 5 is a snipable gap -- shootable through, not walkable."""
    assert _parse_one(bytes([5] * 100)).walkable_cells == 0


def test_non_walkable_ground_is_not_walkable():
    assert _parse_one(bytes([1] * 100)).walkable_cells == 0


def test_a_grid_that_is_not_width_times_height_is_rejected():
    """The one check that catches a wrong offset. If the header were read as
    six bytes instead of eight, every subsequent field would be garbage and the
    grid length would not match -- so this is the assertion that turns a silent
    misparse into an error."""
    grid = zlib.compress(bytes(50))          # 50 cells
    raw = struct.pack("<12shhi", b"bad", 10, 10, len(grid)) + grid   # claims 100
    cache = struct.pack("<IH", 8 + len(raw), 1) + b"\x00\x00" + raw
    with pytest.raises(ValueError, match="cell count"):
        parse_map_cache(cache)


def test_a_truncated_file_is_rejected():
    with pytest.raises(ValueError):
        parse_map_cache(_cache([("prontera", 200, 200)])[:40])


def test_a_file_that_is_not_a_map_cache_is_rejected():
    with pytest.raises(ValueError):
        parse_map_cache(b"this is not a map cache at all, not even close")


def test_a_map_count_higher_than_the_file_holds_is_rejected():
    """The loop runs out of bytes before it runs out of declared maps."""
    cache = _cache([("prontera", 200, 200)])
    lying = struct.pack("<IH", len(cache), 7) + cache[6:]   # header claims 7
    with pytest.raises(ValueError, match="ends inside"):
        parse_map_cache(lying)


def test_a_map_count_lower_than_the_file_holds_is_rejected():
    """Parsing stops early and leaves bytes over. Silently ignoring them would
    drop maps from the end of the list without anyone noticing."""
    cache = _cache([("prontera", 200, 200), ("payon", 160, 160)])
    lying = struct.pack("<IH", len(cache), 1) + cache[6:]   # header claims 1
    with pytest.raises(ValueError, match="trailing bytes"):
        parse_map_cache(lying)


def test_the_declared_file_size_must_match():
    """A mismatch means a truncated or concatenated file, and parsing on would
    produce plausible nonsense."""
    cache = _cache([("prontera", 200, 200)])
    lying = struct.pack("<IH", len(cache) + 999, 1) + cache[6:]
    with pytest.raises(ValueError, match="file_size"):
        parse_map_cache(lying)


# --- layering ----------------------------------------------------------------
#
# rAthena reads THREE map caches, not one (map_readallmaps,
# rathena/src/map/map.cpp:3910), and importers/import_maps.py used to read only
# the base file. On the reference lab that meant 1,263 maps with no prontera,
# morocc, izlude or alberta in them -- all four live in db/re/map_cache.dat,
# alongside prt_church, prt_fild05, prt_fild08 and prt_in. 1,263 + 8 = 1,271,
# exactly the number of entries in db/map_index.txt.
#
# These build records by hand: layering is about which record wins, not about
# bytes, and pinning a real cache here would bundle game data.


def _record(name: str, width: int = 10, height: int = 10) -> MapRecord:
    return MapRecord(
        name=name, width=width, height=height,
        cells=zlib.compress(bytes(width * height)),
        walkable_cells=width * height,
    )


def test_a_name_in_two_layers_takes_the_first_layers_record():
    """First file wins. map_readallmaps loads the caches in the order
    import, DBPATH, base -- "in reverse order to account for import", its own
    comment says -- and map_readallmaps breaks out of the buffer loop on the
    first cache that has the map. So an operator's db/import/ override beats
    db/re/, which beats db/.

    If this ever gets reversed, an operator who overrode prontera in db/import/
    would silently be served the stock geometry instead.
    """
    maps = layer([
        ("db/import/map_cache.dat", [_record("prontera", 200, 200)]),
        ("db/re/map_cache.dat", [_record("prontera", 40, 40)]),
    ])
    assert [m.record.width for m in maps] == [200]
    assert maps[0].source == "db/import/map_cache.dat"


def test_a_name_in_all_three_layers_takes_the_first():
    maps = layer([
        ("import", [_record("prontera", 200, 200)]),
        ("re", [_record("prontera", 40, 40)]),
        ("base", [_record("prontera", 8, 8)]),
    ])
    assert len(maps) == 1
    assert maps[0].record.width == 200


def test_names_unique_to_later_layers_are_all_included():
    """The bug was the other half of this: reading only one file. Every name in
    every layer has to survive, whichever layer it came from."""
    maps = layer([
        ("re", [_record("prontera"), _record("izlude")]),
        ("base", [_record("payon"), _record("geffen"), _record("ba_2whs02")]),
    ])
    assert {m.record.name for m in maps} == {
        "prontera", "izlude", "payon", "geffen", "ba_2whs02"
    }


def test_each_map_records_the_file_it_actually_came_from():
    """Not all three paths joined: per row, the one file. That is how an
    operator sees that prontera came from db/re/ and payon from db/."""
    maps = layer([
        ("db/re/map_cache.dat", [_record("prontera")]),
        ("db/map_cache.dat", [_record("payon")]),
    ])
    assert {m.record.name: m.source for m in maps} == {
        "prontera": "db/re/map_cache.dat",
        "payon": "db/map_cache.dat",
    }


def test_an_empty_layer_contributes_nothing_and_breaks_nothing():
    """db/import-tmpl/map_cache.dat is an 8-byte template holding zero maps, and
    a stock server's db/import/map_cache.dat is that file or absent. An empty
    first layer must not shadow, short-circuit or reorder anything."""
    maps = layer([
        ("import", []),
        ("re", [_record("prontera")]),
        ("base", [_record("payon")]),
    ])
    assert [m.record.name for m in maps] == ["payon", "prontera"]
    assert all(m.source != "import" for m in maps)


def test_no_layers_at_all_is_an_empty_list_not_an_error():
    assert layer([]) == []


def test_the_result_is_alphabetical_by_name():
    """Deterministic order, chosen as alphabetical because the table is keyed by
    name and /api/v1/maps orders by name -- so cache order carries no
    information, and a stable order makes the importer's report reproducible."""
    maps = layer([
        ("re", [_record("prontera"), _record("alberta"), _record("morocc")]),
        ("base", [_record("payon"), _record("geffen")]),
    ])
    assert [m.record.name for m in maps] == [
        "alberta", "geffen", "morocc", "payon", "prontera"
    ]


def test_the_order_does_not_depend_on_the_order_the_layers_were_read():
    """Same maps, layers swapped: the winner changes, the ordering does not."""
    a = layer([("x", [_record("payon")]), ("y", [_record("alberta")])])
    b = layer([("y", [_record("alberta")]), ("x", [_record("payon")])])
    assert [m.record.name for m in a] == [m.record.name for m in b]


def test_a_duplicate_name_inside_one_cache_resolves_to_its_first_entry():
    """map_readfromcache (rathena/src/map/map.cpp:3662) walks the entries and
    breaks on the first strcmp match, so within a file the rule is the same."""
    maps = layer([("base", [_record("prontera", 200, 200), _record("prontera", 9, 9)])])
    assert [m.record.width for m in maps] == [200]
