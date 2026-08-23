---
name: ro-admin
description: Use when investigating or administering an rAthena Ragnarok Online server through a ro-admin API — answering questions about what happened to a character, auditing GM commands, tracing zeny or item history, or checking which install tiers a server has. Triggers on questions like "what happened to this character", "who gave that item", "show me the GM commands", "trace this player's zeny", or any forensic question about an RO server.
---

# Administering an rAthena server through ro-admin

`ro-admin` is a read-only administration API over a live rAthena server's database.
This skill drives it. **The server holds no AI credentials and makes no model calls** —
you are the intelligence, it is the interface.

## Setup

Two environment variables, both read from the environment so tokens never land in
shell history or a process list:

```
RO_ADMIN_URL     # default http://localhost:8000
RO_ADMIN_TOKEN   # a scoped service token
```

If `RO_ADMIN_TOKEN` is unset, ask the operator to mint one. Do not mint it yourself
unless they ask — it requires the server's signing secret:

```
python scripts/mint_token.py my-agent logs.read system.read --days 30
```

Request the **narrowest scopes that answer the question**. Scopes are a ceiling: a
token scoped to `logs.read` is refused `system.read` even if an administrator minted
it. Available scopes come from `src/ro_admin/permissions.py`; an invalid name fails at
mint time rather than producing a token that silently denies everything.

## Always discover before you query

**Never assume an endpoint exists.** The API changes; this file will not keep up.
Start every session with:

```
python -m ro_admin.cli discover
```

That reads the server's generated OpenAPI document and prints every endpoint it
actually offers, with summaries and parameters. It is authoritative in a way this
document is not. If an endpoint you expect is missing, the server does not have it —
say so rather than working around it.

Then query:

```
python -m ro_admin.cli get logs/timeline char_id=150002 limit=20
python -m ro_admin.cli get logs/zeny char_id=150002
python -m ro_admin.cli get logs/items item_id=501
python -m ro_admin.cli get logs/commands char_name=Kami
```

The `/api/v1` prefix is optional. **Omit the leading slash** — on Git Bash a leading
`/` gets rewritten into a Windows path before Python sees it, producing a confusing
`InvalidURL` about control characters.

## Answering the question people actually ask

"What happened to this character?" is one call:

```
python -m ro_admin.cli get logs/timeline char_id=150002
```

It merges GM commands, zeny changes, and item transactions into one chronological
stream, each entry carrying a readable `summary` plus a `detail` object:

```
2026-08-22T03:37:56  [item]  item 501 x3 via npc script on geffen
2026-08-21T22:29:54  [zeny]  zeny +777 via admin command on geffen
```

Prefer the timeline for open-ended investigation, and the per-source endpoints
(`logs/zeny`, `logs/items`, `logs/commands`) when you already know what you are
looking for or need filters they offer.

## Reading the results honestly

**`type_name` is decoded for you.** rAthena stores single-letter type codes; the API
translates all 31. Never guess at a raw code — the letters are mnemonic on rAthena's
internal enum *names*, not on their meanings, so `S` is "npc shop" while `N` is "npc
script". An unmapped code appears as `unknown (X)`; report it as unknown rather than
inferring.

**Absence of a log entry is not proof nothing happened.** Two reasons, both real:

- Logging is configurable per category. Check `system/capabilities` — its
  `tier0.log_tables` lists which log tables this server actually has. A server with
  chat logging off has no `chatlog`, and that is a configuration fact, not evidence.
- **Changes written straight to the database bypass the game's logging entirely.**
  An admin tool that updates a character row directly leaves no `picklog` or
  `zenylog` trace. So "no record" can mean "it was done out-of-band", not "it did not
  happen". Say which you mean.

**Timestamps are the server's local time**, and the log tables carry no timezone.

**Pagination is limit/offset, and `atcommandlog` has no surrogate key.** For that
table, rows sharing a timestamp have no stable order, so deep paging can repeat or
skip. Prefer narrowing with filters over paging far.

## Boundaries

- **This API is read-only.** There are no write endpoints. If asked to change
  something — ban an account, grant an item, edit stats — say plainly that `ro-admin`
  cannot, and stop. Do not reach around it into the database or another tool.
- **Never print a token.** The CLI redacts anything JWT-shaped from its own output;
  do not defeat that by echoing `$RO_ADMIN_TOKEN` or pasting one into a summary.
- **Log data is player data.** Chat logs, where enabled, contain private
  conversation. Retrieve what the question needs, quote sparingly, and do not bulk-
  dump personal history into a transcript.
- **A 403 is an answer, not an obstacle.** It means the token's scope excludes that
  endpoint. Report it and ask for a wider scope if the task genuinely needs one —
  never work around it.
