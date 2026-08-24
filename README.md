# ro-admin

A modern administration API for [rAthena](https://github.com/rathena/rathena) servers.

## What this is, and is not

**Is:** an operator's administration API — **forensics** over rAthena's own logs and **live GM
operations** applied inside the running game server. Eight routers ship today: `auth`, `logs`
(GM commands, zeny changes, item transactions, and a per-character timeline across all three),
`system` (capability reporting), `items` (search, type facets and full item detail
including the rAthena script), `accounts` and `characters`
(reads over the `login` and `char` tables, including a character's inventory), `maps`
(the server's real map list and its walkable geometry, once imported), and `commands`
(the Tier 1 queue: item grants and zeny adjustments).

| Endpoint | Notes |
|---|---|
| `POST /api/v1/auth/login`, `GET /api/v1/auth/me` | Sign in as an rAthena account; report the principal |
| `GET /api/v1/logs/commands` | GM commands (`atcommandlog`) |
| `GET /api/v1/logs/zeny` | Zeny changes (`zenylog`) |
| `GET /api/v1/logs/items` | Item transactions (`picklog`) |
| `GET /api/v1/logs/timeline` | All three merged per character, chronologically |
| `GET /api/v1/system/capabilities` | Which tiers and log tables this install actually has |
| `GET /api/v1/items` | Search the operator's own `item_db`: `q` (substring of `name_english`, `name_aegis` or `alias_name`), `type`, `subtype`, `slots`; `limit`/`offset` |
| `GET /api/v1/items/types` | The item types this server actually has, with counts |
| `GET /api/v1/items/{item_id}` | One item in full — names, stats, prices and its rAthena `script` |
| `GET /api/v1/accounts` | Filters: `userid` (exact), `min_group_id`; `limit`/`offset` |
| `GET /api/v1/accounts/{account_id}` | One account |
| `GET /api/v1/accounts/{account_id}/characters` | That account's characters |
| `GET /api/v1/characters` | Filters: `name` (exact), `account_id`, `online`; `limit`/`offset` |
| `GET /api/v1/characters/{char_id}` | One character |
| `GET /api/v1/characters/{char_id}/inventory` | Inventory, item names resolved server-side |
| `GET /api/v1/maps` | Search the imported map list: `q` (substring of the name); `limit`/`offset` |
| `GET /api/v1/maps/{name}` | One map's `width`, `height` and `walkable_cells` |
| `GET /api/v1/maps/{name}/cell` | What is at one coordinate: the raw rAthena `gat` type plus `walkable`/`shootable`/`water` |
| `GET /api/v1/maps/{name}/cells` | The whole grid — `width*height` raw bytes, one gat type per cell |
| `POST /api/v1/commands`, `GET /api/v1/commands/{id}` | Tier 1 only: enqueue an action, poll its outcome |
| `GET /healthz` | Unauthenticated liveness, and a real database round trip |

**Reads accounts and characters; does not manage them.** The account and character
endpoints are reads only — nothing in this API edits a `login` or `char` row. There is
no ban, no password reset, no stat edit and no character deletion, and the only writes
of any kind go through the game server itself via Tier 1. Account responses serve an
explicit column allowlist, so no password, pincode or session token is ever in one.
Because the `char` table is a mirror the map server flushes on logout or every
`autosave_time`, every character response carries `stale` and `stale_fields` saying so.

**Is not:** a FluxCP replacement. FluxCP is two products: of its 135 actions, only 49 require
admin. The other 64% is a player-facing control panel — registration, rankings, donations,
purchase carts, a support desk. `ro-admin` does not do that half, and if you need it, keep
running FluxCP alongside.

## Requires no fork of rAthena

rAthena is consumed unmodified. Everything this tool adds rides rAthena's official extension
seams. You keep pulling upstream.

## Install tiers

| Tier | You do | You get |
|---|---|---|
| **0 — Database** | Point it at your MySQL. Nothing installed. | **Forensics/logs** — GM commands, zeny, item transactions, per-character timeline — plus **account and character reads** (accounts, characters, inventories), item search and detail, and a health/capability report. The map endpoints are Tier 0 too, but need a one-off import first — see below |
| **1 — Script overlay** *(shipping)* | Run `overlay/schema.sql`, drop one NPC file in `npc/custom/`, add one line to `scripts_custom.conf`. No recompile. | Item grants and zeny adjustments applied **inside the running game**: the game's own stacking, weight and cap rules; whatever logging the server has enabled; visible without a relog; and an outcome recorded only after the change was read back and confirmed |
| **2 — Compiled hooks** | Add an `.inc` to `src/custom/`, rebuild. | Custom atcommands and script functions |

Tier 0 is fully useful alone. Tier 2 is never required.

**Tier 1 install, verification and limits: [`overlay/README.md`](overlay/README.md).**
Read it before installing — in particular, item grants land in `picklog` on a stock
rAthena, and zeny changes do not land in `zenylog` unless you have turned `log_zeny`
on. `GET /api/v1/system/capabilities` reports whether Tier 1 is actually responding,
from the script's own heartbeat rather than from a config file.

## Map import

The map endpoints need one setup step, and it is independent of the tiers above —
Tier 0 is enough to serve them, but not enough to fill them.

**rAthena has no map list in SQL.** Map names reach the database only as values on
log and character rows, so a database-only view can name just the maps something
has already happened on: ten, on the reference lab, against the 1,241 the map
server had loaded. The real list is a file on the game server's disk.

So the operator imports it once, from a checkout, on the machine that has the
files:

    mysql -u <user> -p <database> < importers/maps_schema.sql
    python -m importers.import_maps --rathena-db /path/to/rathena/db

`GET /api/v1/system/capabilities` reports `maps.imported`, `count` and
`imported_at` from the table itself, so you can tell an import that ran from one
that did not. `DROP TABLE ro_admin_maps;` reverses it completely.

**Read [`importers/README.md`](importers/README.md) before running it.** Two
things there will cost you if you skip them: the importer takes your `db/`
*directory* because rAthena layers three map caches and reads the first that has
a given map — point it at one file and you get 1,263 maps and no `prontera` — and
`--mode renewal|pre-renewal` decides which of `db/re/` and `db/pre-re/` is
layered, a compile-time setting nothing on disk records, where a wrong choice
imports the right map names with the wrong geometry.

That file also carries the **gat cell-type table**, which anything consuming the
grid needs in order to read it: the bytes are rAthena cell types, not walkable
flags, and walkable means `gat not in {1, 5}` rather than `gat == 0`.

## AI, deliberately absent

This service makes no model calls and holds no AI credentials. Instead it ships an agent skill
in `skill/` that drives this API. You bring your own agent and your own inference budget; the
server stays a plain API.

    python scripts/mint_token.py my-agent logs.read system.read --days 30
    export RO_ADMIN_TOKEN=...
    python -m ro_admin.cli discover
    python -m ro_admin.cli get logs/timeline char_id=150002

The skill discovers endpoints from this server's generated OpenAPI document rather than
carrying a hardcoded list, so it does not go stale when the API changes. Service tokens are
scoped, and scopes are a ceiling: a token limited to `logs.read` is refused everything else
regardless of who minted it.

## Quick start

    cp .env.example .env    # then fill in every value
    pip install -e ".[dev]"
    uvicorn ro_admin.main:app --reload

API docs at http://localhost:8000/docs

## Relationship to rAthena

`ro-admin` is an independent service. It contains no rAthena source, links no
rAthena code, and redistributes no game data — it connects to your database
over a normal MySQL connection and reads what is already there. Item names come
from *your* `item_db` at runtime, never bundled here. Job names are not served at
all: rAthena keeps job data in YAML on the server's filesystem rather than in the
database, so a character's `class` is returned as the bare rAthena job id.

Tier 1 adds two tables of its own, both prefixed `ro_admin_`, and one NPC
script you copy into `npc/custom/` — rAthena's own extension seam. It modifies
no rAthena file and needs no rebuild. Two `DROP TABLE`s and one line removed
from `scripts_custom.conf` put everything back.

The map import adds a third such table, `ro_admin_maps`, and is the one place
this project reads a file from the game server. **The service still does not.**
`importers/` is run by the operator, from a checkout, on their own machine; it
writes the table and exits, and the API only ever sees the table — which is what
keeps the service able to run on a different host from the game server. The
scripts are deliberately not part of the installed package. `DROP TABLE
ro_admin_maps;` reverses it.

What it imports is **map geometry** — the cell-type grid rAthena generates for
pathfinding and line of sight — and nothing else. Map textures, models, scenery
and minimaps live in the client's GRF archives and are Gravity's copyrighted
assets. No part of this project reads a GRF, and no endpoint serves a map image.

rAthena itself is GPLv3 and is not included or modified by this project.

## License

MIT — see [LICENSE](LICENSE).
