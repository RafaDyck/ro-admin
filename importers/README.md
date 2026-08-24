# Map import

`ro-admin` serves your server's map list from a table. Nothing populates that
table for you — you populate it once, from your own rAthena files, with the two
scripts in this directory. This file is how.

If you have never run anything from this repository before, read it end to end
first. The whole job is two commands, but one of them takes an argument that is
easy to get wrong in a way that looks like it worked.

## Why an import is needed at all

**rAthena has no map list in SQL.** There is no `map_db` table, no `maps` table,
nothing. Map names reach the database only as *values* in other rows — the
`map` column on a log row, `last_map` and `save_map` on a character.

So a service that only reads the database can name exactly the maps something
has already happened on. Measured on the reference lab, across every column
where a map name appears:

| Source | Distinct map names |
|---|---|
| `atcommandlog.map` | 2 |
| `picklog.map` | 3 |
| `zenylog.map` | 5 |
| `char.last_map` | 8 |
| `char.save_map` | 9 |
| **all five, unioned** | **10** |

Ten. The map server on that same lab reported `Map-Server 0 connected: 1241
maps` at startup. A database-only view is not a small sample of the map list;
it is a list of places, and it grows only when someone goes somewhere.

The real list is a file on the game server's disk. That is what this imports.

## Why this directory is separate from the service

**`ro-admin` never reads the game server's filesystem.** It opens a MySQL
connection and reads tables. That is the whole reason it can run on a different
host from the game server, in a container, or against a server whose files you
do not have and did not build.

These two scripts are the exception, and they are shaped so the rule survives
them:

- **You** run them, on **your** machine, against **your** files.
- They write one table and exit. Nothing stays running.
- The service only ever sees that table. It has no idea a file was involved, it
  holds no path to your rAthena checkout, and it would behave identically if
  you had populated the table by hand.

They are run from a repository checkout and are **deliberately not part of the
installed package**, exactly like [`overlay/`](../overlay/README.md). Installing
`ro_admin` does not give you `import_maps`; a `git clone` does. If you are
looking for these scripts on a production API host, they are not supposed to be
there.

## Install

Two steps. Both are safe to re-run.

### 1. Create the table

    mysql -u <your_user> -p <your_database> < importers/maps_schema.sql

`CREATE TABLE IF NOT EXISTS` — idempotent. It creates one table,
`ro_admin_maps`, prefixed like the Tier 1 overlay's tables so it cannot collide
with anything rAthena owns.

### 2. Import your maps

    python -m importers.import_maps --rathena-db /path/to/rathena/db

**Point it at the `db/` DIRECTORY, not at a file.** The next section is why
that matters more than it looks.

Database settings come from the same `RO_ADMIN_DB_*` environment variables the
service uses, so if you have configured the service you have already configured
this.

Real output, from the reference lab:

```
$ python -m importers.import_maps --rathena-db /opt/rathena/db
renewal mode: renewal (DBPATH = re/)
map caches, in rAthena's precedence order (first match wins):
  [1] /opt/rathena/db/import/map_cache.dat  -- not present, skipped
  [2] /opt/rathena/db/re/map_cache.dat  -- 8 maps, 28,232 bytes
  [3] /opt/rathena/db/map_cache.dat  -- 1263 maps, 3,014,820 bytes
layered 1271 maps (105,133,408 cells):
  8 from /opt/rathena/db/re/map_cache.dat
  1263 from /opt/rathena/db/map_cache.dat
imported 1271 maps into ro_admin_maps
```

Every path is printed whether or not it existed, with its map count. That is
deliberate: a silently skipped cache file looks exactly like a successful
import.

### The three counts, and why they differ

They are not the same number, and they are not supposed to be:

| | Reference lab | Where it comes from |
|---|---|---|
| **Indexed** | 1,271 | entries in `db/map_index.txt` |
| **Cached** | 1,271 | what this importer finds, and writes |
| **Loaded** | 1,241 | `map:` lines in `conf/maps_athena.conf` — and the count the map server logs at startup |

**Indexed ≥ cached ≥ loaded.** Importing more maps than your server currently
serves is normal and correct: the cache holds geometry for maps your `conf` has
not enabled. On the lab that 30-map gap is exactly the job-quest, `force_map*`
and disabled-field maps that `maps_athena.conf` does not list, and the reverse
set — enabled but not cached — was empty.

Do not go looking for a bug because the import reports more maps than your map
server logged.

## The layering: three files, first match wins

rAthena does not read one map cache. `map_readallmaps` (`src/map/map.cpp:3910`)
builds this list, under its own comment *"Load the map cache files in reverse
order to account for import"*:

```
db/import/map_cache.dat        <- your custom maps
db/<re|pre-re>/map_cache.dat   <- renewal-specific geometry
db/map_cache.dat               <- everything else
```

and then, for each map, uses the **first** of those files that contains it.
This importer resolves the same three files in the same order for the same
reason: so the list in your table is the list your server actually serves.

**Why passing one file would be wrong.** An earlier version of this tool took
`--map-cache <one file>`. Pointed at `db/map_cache.dat` on the reference lab it
produced 1,263 maps and no errors — and no **`prontera`**. Nor `morocc`, nor
`izlude`, nor `alberta`. Those four are among the eight maps that live only in
`db/re/map_cache.dat`; the base cache has never heard of them.

That is the failure this directory is shaped around. It does not crash, it does
not warn, and the only symptom is that the busiest map in the game 404s.

The eight, on the reference lab: `alberta`, `izlude`, `morocc`, `prontera`,
`prt_church`, `prt_fild05`, `prt_fild08`, `prt_in`. The two sets do not overlap
at all — 8 + 1,263 = 1,271, with nothing overridden.

`db/import/map_cache.dat` is absent on a stock rAthena; the file it ships in
`db/import-tmpl/` is an empty 8-byte template. That is not an error. It is where
**your** custom maps go, and if you have any, they win over both other layers.

## `--mode`: renewal or pre-renewal

    python -m importers.import_maps --rathena-db /opt/rathena/db --mode pre-renewal
    python -m importers.import_maps --rathena-db /opt/rathena/db --pre-renewal   # same thing

This picks whether `db/re/` or `db/pre-re/` is the middle layer — rAthena's
`DBPATH` (`src/config/const.hpp:38-40`).

**`DBPATH` is a compile-time define, and nothing on disk records which one your
server was built with.** The importer cannot detect it. So it is a flag, it
defaults to `renewal`, and if the cache it selects is missing it says so:

```
WARNING: /opt/rathena/db/re/map_cache.dat is not there, so no re/ maps were
layered. If this is a pre-renewal server, rerun with --mode pre-renewal.
```

### What a mode mistake costs

Not an error. **Wrong geometry, under the right names.**

On the reference lab `db/re/` and `db/pre-re/` hold the *same eight map names*.
So the wrong mode still imports 1,271 maps, still has a `prontera`, and still
prints a clean report. It just imports different dimensions for eight of them:

| Map | renewal (`db/re/`) | pre-renewal (`db/pre-re/`) |
|---|---|---|
| `izlude` | 268 x 300, 14,357 walkable | **268 x 268**, 10,983 walkable |
| `alberta` | 280 x 280, 27,146 walkable | 280 x 280, **22,667** walkable |
| `morocc` | 320 x 320, 52,529 walkable | 320 x 320, **51,642** walkable |
| `prontera` | 312 x 392, 61,014 walkable | 312 x 392, **61,522** walkable |
| `prt_church` | 200 x 200, 2,837 walkable | 200 x 200, 2,837 walkable — **but 12 cells differ** |
| `prt_fild05`, `prt_fild08`, `prt_in` | | byte-for-byte identical |

The two runs differ by 8,576 cells in a total of 105 million, and by nothing you
would notice in the output. But `izlude` is 32 rows shorter in pre-renewal: ask
`/api/v1/maps/izlude/cell?x=100&y=280` and a renewal import answers, while a
pre-renewal import returns 422 "outside izlude". If your warp scripts and the
API disagree about where a map ends, check this flag first.

`prt_church` is the version of this you would never catch by eye. Same
dimensions, same walkable total, and exactly twelve bytes different — the twelve
cells that are gat 2 in renewal are gat 0 in pre-renewal. Every summary number
the API reports for it is identical under either mode. Only `/cell` can tell
them apart.

If you do not know which your server is, watch it start: it logs the `db/re/…`
or `db/pre-re/…` files it reads.

## `--dry-run`

    python -m importers.import_maps --rathena-db /opt/rathena/db --dry-run

Parses everything, prints the same report, writes nothing, and ends with
`dry run: nothing written`.

**It needs no database credentials at all** — no host, no user, no password. It
never constructs a connection. Run it first: it is the cheapest way to confirm
you have the right directory, the right mode and the counts you expect, before
any of it reaches your database.

## Verify

Ask the API, which reports what it finds in the table rather than what a setting
claims:

    GET /api/v1/system/capabilities

Like every endpoint it needs a token, here with `system.read`:

    export RO_ADMIN_TOKEN=$(python scripts/mint_token.py verify system.read --days 1)
    python -m ro_admin.cli get system/capabilities

The `maps` object after a good import, observed on a live lab:

```json
"maps": {
  "imported": true,
  "count": 1271,
  "reason": "1271 maps imported",
  "imported_at": "2026-08-24T00:34:09"
}
```

`imported_at` is the time of the import, not of the query. If it is older than
you expect, your last import did not run.

There are two unimported states. They need different fixes, so they say
different things:

| What is wrong | `reason` |
|---|---|
| Table missing | `maps not imported: run importers/maps_schema.sql, then importers/import_maps.py` |
| Table exists, no rows | `map table exists but is empty: run importers/import_maps.py` |

Both report `"imported": false, "count": 0, "imported_at": null`. Both were
produced against a live server by actually dropping the table and actually
running the two steps, not read off the source.

**`capabilities` is still the better first call**, because it answers for the
whole install in one request rather than one endpoint at a time. But the map
endpoints no longer leave you guessing: while the table is missing, all four
answer **503 Service Unavailable** carrying that same `reason` as their
`detail` — literally the same sentence, from one definition in
`src/ro_admin/routers/maps.py` that `capabilities` imports. A 503 from
`/api/v1/maps` on a fresh install means step 1 has not been run.

They answered a bare **500** until this was fixed, which said "this service is
broken" about an install that is merely incomplete.

503 and not 404: the route exists and works. What is missing is the data.

## Re-importing

**Whenever your map list changes, run step 2 again.** Adding a custom map is the
usual reason: you drop it into `db/import/map_cache.dat`, and until you
re-import, the API does not know it exists.

    python -m importers.import_maps --rathena-db /opt/rathena/db

**There is no incremental mode.** The import is one transaction that deletes
every row and writes the whole list back. That is the point. A half-updated map
table would let `/system/capabilities` report maps as imported while the count
silently disagreed with your server — so either the whole list is current, or
the transaction rolled back and nothing changed.

You do not need to re-run `maps_schema.sql`, though re-running it is harmless.

Two columns tell you where each row came from, and when:

- **`source`** names, per row, the one cache file **that map** was taken from —
  not all three joined together. So you can see that `prontera` came from
  `db/re/map_cache.dat` while `payon` came from `db/map_cache.dat`, which is the
  entire fact the layering turns on. It is also how you confirm a `--mode`
  choice after the fact.
- **`imported_at`** is when that row was written. It is **not** a single instant
  across the run: the rows go in as several batched `INSERT`s inside the one
  transaction, and `NOW()` is evaluated per statement. Measured on the lab, one
  import of 1,271 maps landed on three consecutive second values (185 / 1,067 /
  19 rows). So treat it as "which run", not as a precise clock, and compare with
  `MAX` — which is what `/system/capabilities` reports.

```sql
SELECT source, COUNT(*), MIN(imported_at), MAX(imported_at)
FROM ro_admin_maps GROUP BY source;
```

## What you get

Four endpoints, all reads, all gated by the `system.read` scope:

| Endpoint | What it answers |
|---|---|
| `GET /api/v1/maps` | List and search. `q` (substring of the name), `limit` (1–500, default 50), `offset` |
| `GET /api/v1/maps/{name}` | One map's `width`, `height` and `walkable_cells` |
| `GET /api/v1/maps/{name}/cell?x=&y=` | What is at one coordinate |
| `GET /api/v1/maps/{name}/cells` | The whole grid: `width*height` raw bytes, row-major, with `X-Map-Width` and `X-Map-Height` headers |

```
$ curl -H "Authorization: Bearer $RO_ADMIN_TOKEN" "$RO_ADMIN_URL/api/v1/maps?q=prontera"
{"items":[{"name":"pprontera","width":312,"height":392,"walkable_cells":61522},
          {"name":"prontera","width":312,"height":392,"walkable_cells":61014}],
 "total":2,"limit":50,"offset":0}
```

`q` is a plain substring: `%` and `_` are escaped before the query runs and mean
themselves. Measured on the lab, `q=prt_` returns 42 maps; had `_` been left as
a LIKE wildcard it would have returned 52, sweeping in `prtg_cas01` through
`prtg_cas05`.

### The grid bytes are gat cell types. This is the part you cannot skip.

Each byte of the grid is **one rAthena gat cell type**, not a walkable flag. If
you are writing anything that consumes `/cells` or `/cell`, this table is the
contract:

| gat | Meaning | Walkable | Shootable |
|---|---|---|---|
| **0** | walkable ground | yes | yes |
| **1** | non-walkable ground | **no** | **no** |
| **2** | *(rAthena's own comment is `???`)* | yes | yes |
| **3** | **walkable water** | **yes** | yes |
| **4** | *(rAthena's own comment is `???`)* | yes | yes |
| **5** | **gap** — not walkable, but shootable over | **no** | **yes** |
| **6** | *(rAthena's own comment is `???`)* | yes | yes |

From `map_gat2cell`, `src/map/map.cpp:3270`. The `???` are upstream's own
comments, not ours: types 2, 4 and 6 behave exactly like 0, and rAthena does not
document what distinguishes them.

**So walkable is `gat not in {1, 5}`. It is not `gat == 0`.**

That shortcut is not a rounding error, and it is worth seeing the size of it
before writing it yourself. Assuming `0 == walkable` across the 1,263 maps of
the reference lab's base cache:

- undercounts walkable cells by **6.3%** overall;
- is outright wrong on **497 of those 1,263 maps**;
- reports `ba_2whs02` as having **zero** walkable cells. It has 86,274 of its
  129,600 — the map contains no gat 0 at all, and every walkable cell on it is
  water.

And it survives casual testing. `alb_ship` — the first map in the file, and so
the one a spot check reaches — contains only types 0 and 1, where the shortcut
is exactly right. Types 2 and 4 together occur **16 times in 104 million cells**,
so no amount of sampling would have found them either.

`/cell` returns the raw `gat` alongside the derived `walkable`, `shootable` and
`water` flags precisely so consumers need not redo any of this:

```
$ curl ".../api/v1/maps/prt_fild05/cell?x=208&y=31"
{"name":"prt_fild05","x":208,"y":31,"gat":3,"walkable":true,"shootable":true,"water":true}

$ curl ".../api/v1/maps/prt_fild05/cell?x=96&y=158"
{"name":"prt_fild05","x":96,"y":158,"gat":5,"walkable":false,"shootable":true,"water":false}
```

The raw byte is served rather than a boolean because collapsing it throws away
the difference between water and a snipable gap, and neither this API nor its
author knows what you are building.

A coordinate outside the map is **422**, which is a different answer from
"blocked":

```
$ curl ".../api/v1/maps/prontera/cell?x=400&y=10"
{"detail":"(400,10) is outside prontera, which is 312x392"}
```

## What this does not copy

**Geometry, not artwork.** The grid is a cell-type map that rAthena generates
for its own pathfinding and line-of-sight checks, and that is the entirety of
what gets imported and served.

Map **textures, models, scenery and minimap images** live in the Ragnarok
client's GRF archives. They are Gravity's copyrighted assets. This importer does
not read a GRF, the table has no column for one, and no endpoint serves a map
image. If you are drawing a map, the geometry here tells you where the walls
are; the picture is not ours to give you.

Nothing from your server is copied into this repository, either. The map cache
stays on your disk and the rows go into your database.

## Uninstall

    DROP TABLE ro_admin_maps;

That is all of it. Nothing else in your database was touched, and no file on
your rAthena install was ever written to.

The API notices immediately and says so, rather than pretending your server has
no maps:

```json
"maps": {
  "imported": false,
  "count": 0,
  "reason": "maps not imported: run importers/maps_schema.sql, then importers/import_maps.py",
  "imported_at": null
}
```

The four `/api/v1/maps` routes stay in the OpenAPI document — they are routes,
not capabilities — and answer 503 with the reason above until the table comes
back. The document declares that 503 on all four, so a consumer can find out
it is possible without provoking it.
