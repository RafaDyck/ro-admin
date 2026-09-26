# Maps: the server's real map list, and what is on the ground

Read this when the question is about the map list, a map's dimensions, or
whether a coordinate is walkable.


Four Tier 0 reads, all gated by `system.read`.

| Endpoint | Answers |
|---|---|
| `maps` | List and search. `q` (substring of the name), `limit` (1–500, default 50), `offset` |
| `maps/{name}` | One map's `width`, `height`, `walkable_cells` |
| `maps/{name}/cell?x=&y=` | What is at one coordinate |
| `maps/{name}/cells` | The whole grid as raw bytes. Rarely what you want — see below |

### Check `capabilities.maps.imported` first

The map list does not come from rAthena's database — **rAthena has no map list in
SQL at all.** It is imported once by the operator, from their own server's map
cache. On a server where that has not happened, these endpoints have nothing to
serve.

```
python -m ro_admin.cli get system/capabilities
```

```json
"maps": {
  "imported": true,
  "count": 1271,
  "reason": "1271 maps imported",
  "imported_at": "2026-08-24T00:34:09"
}
```

If `imported` is false, **relay `reason` verbatim and stop.** It names the
operator's next step. Both of these are real, and observed:

```
"maps not imported: run importers/maps_schema.sql, then importers/import_maps.py"
"map table exists but is empty: run importers/import_maps.py"
```

**Do not say "this server has no maps."** It has maps — the map server on the
reference lab loaded 1,241 of them. What has not happened is that anyone gave
*the API* the list. Those are entirely different statements, and the first one
sends an operator hunting a problem that does not exist while the actual fix is
one command they have not run yet.

Check this before the map endpoints rather than after — it answers for the
whole install in one call. But the map endpoints tell you the same thing if you
reach one first: while the table is missing, all four answer **503** with that
same sentence as `detail`. Relay it and stop, exactly as above.

A 503 here does not mean the server is overloaded or that you should retry.
It means the data has not been imported, and no amount of retrying imports it.

### Resolve a map by searching. Do not spell one from memory

Names are exact, lowercase, and frequently not what a person calls the place.
Someone asks about "the Geffen dungeon". The obvious guess:

```
python -m ro_admin.cli get maps/geffen_dungeon
```
```json
{"detail": "no map named geffen_dungeon"}
```

404. Searching instead:

```
python -m ro_admin.cli get maps q=gef_dun
```
```json
{"items": [{"name": "gef_dun00", "width": 200, "height": 200, "walkable_cells": 10748},
           {"name": "gef_dun01", "width": 300, "height": 300, "walkable_cells": 29501},
           {"name": "gef_dun02", "width": 260, "height": 260, "walkable_cells": 16463},
           {"name": "gef_dun03", "width": 240, "height": 240, "walkable_cells": 16538}],
 "total": 4, "limit": 50, "offset": 0}
```

Four floors, and the person meant one of them — ask which rather than picking.
Note that `q=geffen` would have found **none** of these: it returns `ch1_geffen`,
`geffen` and `geffen_in`. Search the fragment you are actually confident about,
then widen or narrow from what comes back.

Search is a plain substring over the name only. `%` and `_` are escaped and mean
themselves: `q=prt_` returns the 42 maps containing a literal `prt_`, not the 52
that would match if `_` were a LIKE wildcard.

**Use a name exactly as the API returned it.** On the reference lab
`maps/Prontera` happens to resolve, because that database's collation is
case-insensitive — but that is the operator's collation, not a promise this API
makes, and a case-sensitive server would 404 the identical request. Copy the
name out of a search result instead of retyping it.

Two names differing by one character are two different maps: `prontera` has
61,014 walkable cells, `pprontera` has 61,522.

### The gat table, which you must not simplify

Every byte of the grid, and the `gat` field on `/cell`, is an **rAthena gat cell
type**. It is not a boolean and it does not collapse into one:

| gat | Meaning | Walkable | Shootable |
|---|---|---|---|
| **0** | walkable ground | yes | yes |
| **1** | non-walkable ground | **no** | **no** |
| **2** | *(rAthena's own comment is `???`)* | yes | yes |
| **3** | **walkable water** | **yes** | yes |
| **4** | *(rAthena's own comment is `???`)* | yes | yes |
| **5** | **gap** — not walkable, but shootable across | **no** | **yes** |
| **6** | *(rAthena's own comment is `???`)* | yes | yes |

Walkable is `gat not in {1, 5}`. **It is not `gat == 0`.**

**An agent that reports "the cell is blocked" for gat 3 is wrong.** Gat 3 is
walkable water — a player stands there. Calling gat 5 simply "blocked" is wrong
in a way that matters too: an arrow crosses a gap and a person cannot.

You do not have to derive any of this. `/cell` hands you the raw `gat` *and* the
three flags, precisely so it is never guesswork:

```
python -m ro_admin.cli get maps/prt_fild05/cell x=208 y=31
```
```json
{"name": "prt_fild05", "x": 208, "y": 31, "gat": 3,
 "walkable": true, "shootable": true, "water": true}
```

```
python -m ro_admin.cli get maps/prt_fild05/cell x=96 y=158
```
```json
{"name": "prt_fild05", "x": 96, "y": 158, "gat": 5,
 "walkable": false, "shootable": true, "water": false}
```

```
python -m ro_admin.cli get maps/prt_fild05/cell x=0 y=0
```
```json
{"name": "prt_fild05", "x": 0, "y": 0, "gat": 1,
 "walkable": false, "shootable": false, "water": false}
```

**Report the flags the server sent.** Do not re-derive them from `gat`, and do
not describe a cell in words the response does not support. Where someone needs
a distinction the flags do not carry — one of the `???` types — give the raw type
and say that rAthena does not document what it means.

The size of the shortcut, measured: assuming `0 == walkable` across the 1,263
maps of the lab's base cache undercounts walkable cells by 6.3%, is wrong on 497
of them, and reports `ba_2whs02` as having zero walkable cells when 86,274 of its
129,600 cells are walkable water.

### Out of bounds is 422, and it does not mean "blocked"

```
python -m ro_admin.cli get maps/prontera/cell x=400 y=10
```
```json
{"detail": "(400,10) is outside prontera, which is 312x392"}
```

**That coordinate does not exist.** It is not a wall and not impassable terrain;
there is nothing there to be blocked. Say the coordinate is off the map and give
the real dimensions, which `detail` already contains. Answering "blocked" here
invents a fact about the geometry that the server just told you it has no opinion
on.

An unknown map name is a **404** — `{"detail":"no map named geffen_dungeon"}` —
which is different again: the coordinate may be perfectly valid on a map that
actually exists.

### `/cells` is raw bytes, and you rarely want it

`maps/{name}/cells` returns `width * height` **raw bytes**, not JSON — one byte
per cell, row-major, with `X-Map-Width` and `X-Map-Height` response headers
carrying the row width you need to index it. `prontera` is 122,304 bytes.

**The CLI will not print it, and says so.** Gat types 0–6 are unprintable
control characters, so writing the grid to a terminal shows nothing at all —
which used to look exactly like an empty response, with exit 0 to match. It now
summarises the body and exits **2**:

```
python -m ro_admin.cli get maps/prontera/cells
```
```
<122304 bytes of application/octet-stream> (HTTP 200)
first bytes: 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01...

Not printed: this is not JSON, and a binary body written to a terminal
looks exactly like an empty response.

Save it with curl, which can write it to a file:

  curl -sS -o out.bin -H "Authorization: Bearer $RO_ADMIN_TOKEN" \
    "http://localhost:8000/api/v1/maps/prontera/cells"
```

**That exit 2 is not a server error.** The request succeeded — the `(HTTP 200)`
says so — and the byte count is the real size of the real body. What failed is
this tool's ability to hand you binary on stdout. Do not retry it, do not report
the map as unreachable, and above all **do not read the byte count as zero when
it is not**: `<0 bytes ...>` is what a genuinely empty body looks like, and the
two are deliberately different.

So use `curl` and write to a file:

```
curl -sD- -o grid.bin -H "Authorization: Bearer $RO_ADMIN_TOKEN" \
  "$RO_ADMIN_URL/api/v1/maps/prontera/cells"
```
```
HTTP/1.1 200 OK
x-map-width: 312
x-map-height: 392
content-length: 122304
content-type: application/octet-stream
```

The cell at `(x, y)` is byte `y * width + x`, and it is a gat type — the table
above applies unchanged.

**Ask `/cell` instead.** Nearly every real question — is this warp target on
solid ground, what is under this coordinate — is about one cell, and fetching
122 KB to look at one byte of it is the kind of thing that gets an API worked
around. Reach for `/cells` only when you are genuinely processing a whole map,
and never paste its bytes into a transcript.

### Geometry only: there is no map image, and there will not be

What this API serves is a **cell-type grid** — the same data rAthena uses for
pathfinding and line-of-sight checks. That is the whole of it.

Map **textures, models, scenery and minimaps** live in the Ragnarok client's GRF
archives. They are Gravity's copyrighted assets. This API does not read them,
does not store them, and does not serve them.

So when someone asks for a picture of a map, **say the API does not serve one,
and say why**: it holds geometry, not artwork, and the artwork is Gravity's. Do
not offer to render one from the grid as though that were the same thing, and do
not go looking for the images somewhere else. What you *can* do from this data is
answer geometric questions — dimensions, walkable area, what is at a coordinate,
whether a point is on passable ground.

