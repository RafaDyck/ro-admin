# ro-admin

A modern administration API for [rAthena](https://github.com/rathena/rathena) servers.

## What this is, and is not

**Is:** an administration interface — accounts, characters, forensics, live GM operations.

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
| **0 — Database** | Point it at your MySQL. Nothing installed. | Accounts, characters, items, maps, **forensics/logs**, monitoring |
| **1 — Script overlay** | Drop one NPC file in `npc/custom/`, uncomment one line. No recompile. | Live GM operations with no player relog |
| **2 — Compiled hooks** | Add an `.inc` to `src/custom/`, rebuild. | Custom atcommands and script functions |

Tier 0 is fully useful alone. Tier 2 is never required.

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

rAthena itself is GPLv3 and is not included or modified by this project.

## License

MIT — see [LICENSE](LICENSE).
