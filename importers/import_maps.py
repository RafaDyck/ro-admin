"""Load an rAthena server's map caches into ro_admin_maps.

Run by the OPERATOR, from a checkout, on the machine that has the rAthena files:

    python -m importers.import_maps --rathena-db /path/to/rathena/db

Point it at the DIRECTORY, not a file. rAthena reads THREE map caches, layered
(map_readallmaps, rathena/src/map/map.cpp:3910), and this finds the same three
in the same order, so the imported list is the list the server actually serves.
An earlier version of this tool took --map-cache <one file>; against the
reference lab that produced 1,263 maps containing no prontera, morocc, izlude
or alberta, because those four are among the eight in db/re/map_cache.dat.

Which of db/re/ and db/pre-re/ is layered depends on rAthena's DBPATH, set at
COMPILE time from the RENEWAL define (src/config/const.hpp:38-40); nothing on
disk records which one a server was built with. So it is a flag, defaulting to
renewal, and a PRE-RENEWAL SERVER MUST BE IMPORTED WITH --pre-renewal
(equivalently --mode pre-renewal). The two caches are not interchangeable:
mixing them imports maps the server does not have while missing ones it does.

This is the only part of ro-admin that reads a file from the game server, and
it is deliberately not part of the installed package -- like overlay/, which is
also something you run from a checkout. The service reads a database over TCP
and knows nothing about paths or checkouts; that is what lets it run against
someone else's install, and the predecessor's config routes, which edited conf/
files on disk, are exactly what it exists to avoid.

Database settings come from the same RO_ADMIN_DB_* environment variables the
service uses, so an operator who has configured one has configured both.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import pymysql

from importers.mapcache import MapRecord, layer, parse_map_cache
from ro_admin.config import Settings

TABLE = "ro_admin_maps"

# rAthena's DBPATH, src/config/const.hpp:38-40, keyed by renewal mode.
DBPATH = {"renewal": "re", "pre-renewal": "pre-re"}


def cache_paths(db_dir: pathlib.Path, mode: str) -> list[pathlib.Path]:
    """The three caches, in rAthena's own precedence order.

    Verbatim from map_readallmaps (rathena/src/map/map.cpp:3910), whose comment
    reads "Load the map cache files in reverse order to account for import".
    First match wins; see importers.mapcache.layer for the source that proves
    the direction.
    """
    return [
        db_dir / "import" / "map_cache.dat",      # DBIMPORT, const.hpp:43
        db_dir / DBPATH[mode] / "map_cache.dat",  # DBPATH
        db_dir / "map_cache.dat",
    ]


def read_layers(
    paths: list[pathlib.Path],
) -> list[tuple[pathlib.Path, list[MapRecord]]] | None:
    """Parse each cache that exists, reporting every path either way.

    A missing file is normal, not an error: a stock rAthena has no
    db/import/map_cache.dat at all -- the one it ships, in db/import-tmpl/, is
    an empty 8-byte template -- and rAthena itself only complains and carries
    on. An empty cache is fine too, and is reported as 0 maps.

    But EVERY path is printed with its map count, because a silently skipped
    db/re/map_cache.dat looks exactly like a successful import: 1,263 maps,
    no prontera, no error.

    Returns None if a file could not be parsed, or if none of them existed.
    """
    layers: list[tuple[pathlib.Path, list[MapRecord]]] = []
    print("map caches, in rAthena's precedence order (first match wins):")
    for index, path in enumerate(paths, 1):
        if not path.is_file():
            print(f"  [{index}] {path}  -- not present, skipped")
            continue
        raw = path.read_bytes()
        try:
            records = parse_map_cache(raw)
        except ValueError as exc:
            # Loud, and specific about which check failed. A binary read at the
            # wrong offset produces plausible integers, so a vague failure here
            # would be worse than useless.
            print(f"  [{index}] {path}  -- COULD NOT PARSE: {exc}", file=sys.stderr)
            return None
        print(f"  [{index}] {path}  -- {len(records)} maps, {len(raw):,} bytes")
        layers.append((path, records))

    if not layers:
        print(f"none of those files exist -- is {paths[-1].parent} really an "
              "rAthena db/ directory?", file=sys.stderr)
        return None
    return layers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--rathena-db", required=True, type=pathlib.Path,
        help="path to your rAthena db/ DIRECTORY (not a single file): the three "
             "layered map caches are found inside it the way rAthena finds them",
    )
    parser.add_argument(
        "--mode", choices=sorted(DBPATH), default="renewal",
        help="the server's renewal mode, which decides whether db/re/ or "
             "db/pre-re/ is layered (rAthena's DBPATH, a compile-time define "
             "that nothing on disk records). Default: renewal",
    )
    parser.add_argument(
        "--pre-renewal", dest="mode", action="store_const", const="pre-renewal",
        help="shorthand for --mode pre-renewal",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="parse and report, write nothing (needs no database credentials)",
    )
    args = parser.parse_args(argv)

    if not args.rathena_db.is_dir():
        print(f"not a directory: {args.rathena_db}", file=sys.stderr)
        print("pass your rAthena db/ directory, e.g. --rathena-db /opt/rathena/db",
              file=sys.stderr)
        return 1

    paths = cache_paths(args.rathena_db, args.mode)
    print(f"renewal mode: {args.mode} (DBPATH = {DBPATH[args.mode]}/)")
    layers = read_layers(paths)
    if layers is None:
        return 1

    if not any(path == paths[1] for path, _ in layers):
        # The exact shape of the defect this tool was rewritten to fix: without
        # db/<DBPATH>/map_cache.dat you get the base cache alone, which on the
        # reference lab is 1,263 maps and no prontera.
        other = "renewal" if args.mode == "pre-renewal" else "pre-renewal"
        print(f"WARNING: {paths[1]} is not there, so no {DBPATH[args.mode]}/ maps "
              f"were layered. If this is a {other} server, rerun with "
              f"--mode {other}.", file=sys.stderr)

    maps = layer([(str(path), records) for path, records in layers])
    total_cells = sum(m.record.width * m.record.height for m in maps)
    print(f"layered {len(maps)} maps ({total_cells:,} cells):")
    for path, records in layers:
        taken = sum(1 for m in maps if m.source == str(path))
        overridden = len(records) - taken
        note = f" ({overridden} overridden by an earlier layer)" if overridden else ""
        print(f"  {taken} from {path}{note}")
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    settings = Settings()
    conn = pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            # One transaction. A half-written map table is worse than none:
            # /system/capabilities would report maps as imported while the
            # count silently disagreed with the operator's server.
            cur.execute(f"DELETE FROM {TABLE}")
            cur.executemany(
                f"INSERT INTO {TABLE} "
                "(name, width, height, walkable_cells, cells, imported_at, source) "
                "VALUES (%s, %s, %s, %s, %s, NOW(), %s)",
                # source is per row, and per row it names the ONE file that map
                # came from -- not all three joined together. That is what lets
                # an operator see that prontera came from db/re/ while payon
                # came from db/, which is the whole fact the layering turns on.
                [(m.record.name, m.record.width, m.record.height,
                  m.record.walkable_cells, m.record.cells, m.source)
                 for m in maps],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"imported {len(maps)} maps into {TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
