# ro-admin

A modern administration API for [rAthena](https://github.com/rathena/rathena) servers.

## What this is, and is not

**Is:** an operator's administration API — **forensics** over rAthena's own logs and **live GM
operations** applied inside the running game server. Five routers ship today: `auth`, `logs`
(GM commands, zeny changes, item transactions, and a per-character timeline across all three),
`system` (capability reporting), `items` (id-to-name lookup), and `commands` (the Tier 1 queue:
item grants and zeny adjustments).

**Does not yet include:** account or character management. There is no `/accounts` or
`/characters` endpoint, and nothing here edits a `login` or `char` row — reads go through the
log tables, and the only writes go through the game server itself.

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
| **0 — Database** | Point it at your MySQL. Nothing installed. | **Forensics/logs** — GM commands, zeny, item transactions, per-character timeline — plus item lookup and a health/capability report |
| **1 — Script overlay** *(shipping)* | Run `overlay/schema.sql`, drop one NPC file in `npc/custom/`, add one line to `scripts_custom.conf`. No recompile. | Item grants and zeny adjustments applied **inside the running game**: the game's own stacking, weight and cap rules; whatever logging the server has enabled; visible without a relog; and an outcome recorded only after the change was read back and confirmed |
| **2 — Compiled hooks** | Add an `.inc` to `src/custom/`, rebuild. | Custom atcommands and script functions |

Tier 0 is fully useful alone. Tier 2 is never required.

**Tier 1 install, verification and limits: [`overlay/README.md`](overlay/README.md).**
Read it before installing — in particular, item grants land in `picklog` on a stock
rAthena, and zeny changes do not land in `zenylog` unless you have turned `log_zeny`
on. `GET /api/v1/system/capabilities` reports whether Tier 1 is actually responding,
from the script's own heartbeat rather than from a config file.

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
over a normal MySQL connection and reads what is already there. Item and job
names come from *your* `item_db` at runtime, never bundled here.

Tier 1 adds two tables of its own, both prefixed `ro_admin_`, and one NPC
script you copy into `npc/custom/` — rAthena's own extension seam. It modifies
no rAthena file and needs no rebuild. Two `DROP TABLE`s and one line removed
from `scripts_custom.conf` put everything back.

rAthena itself is GPLv3 and is not included or modified by this project.

## License

MIT — see [LICENSE](LICENSE).
