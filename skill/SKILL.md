---
name: ro-admin
description: Use when investigating or administering an rAthena Ragnarok Online server through a ro-admin API — answering questions about what happened to a character, auditing GM commands, tracing zeny or item history, checking which install tiers a server has, or granting an item or adjusting zeny through the Tier 1 overlay. Triggers on questions like "what happened to this character", "who gave that item", "show me the GM commands", "trace this player's zeny", "give this player an item", "refund their zeny", or any forensic question about an RO server.
---

# Administering an rAthena server through ro-admin

`ro-admin` is an administration API over a live rAthena server. Everything in Tier 0
reads. A server that also has the **Tier 1 overlay** installed accepts two write
actions, covered at the end of this file. **The server holds no AI credentials and
makes no model calls** — you are the intelligence, it is the interface.

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

An investigation needs `logs.read` and `system.read` and nothing more. `commands.read`
lets you check the outcome of a queued action; `commands.write` lets you queue one and
is Admin-level (99). Do not ask for `commands.write` on the chance you might need it.

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

## Tier 1: changing the game, on a server that has the overlay

Two actions, and only two: **grant an item** and **adjust zeny**. They are applied by
an NPC script running inside the game server, so the game's own rules apply, the
change is visible to the player immediately, and the server logs it to the extent it
is configured to. There is no direct-database path and you must not build one.

Tier 1 is optional. Most servers will not have it. Everything below starts with
finding out.

### Check capabilities first, and relay the reason if it is off

```
python -m ro_admin.cli get system/capabilities
```

The `tier1` object on a server that has it, observed:

```json
"tier1": {
  "available": true,
  "reason": "overlay responding, last seen 0s ago",
  "installed": true,
  "responding": true,
  "version": "1"
}
```

If `available` is false, **relay `reason` to the operator verbatim and stop.** It is
written to name their next step, and it is the only thing you know. Real examples:

```
"overlay not installed: run overlay/schema.sql against this database"
"overlay tables exist but the script has never run: copy overlay/ro_admin_overlay.txt into npc/custom/, enable it in npc/scripts_custom.conf, then @reloadscript"
"overlay script last responded 46s ago (stale after 10s); is the map server running?"
"installed overlay is version 0, this API expects 1: copy the current overlay/ro_admin_overlay.txt and @reloadscript"
```

Posting anyway just returns **409 with the same string**. You learn nothing and the
operator waits longer.

### Enqueue, then poll

The CLI has no write verb — it does `discover` and `get` only — so issue the POST
yourself:

```
curl -sS -X POST "$RO_ADMIN_URL/api/v1/commands" \
  -H "Authorization: Bearer $RO_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action":"give_item","char_id":200000,"item_id":501,"amount":3}'
```

An observed response — `202 Accepted`, with `Location: /api/v1/commands/135`:

```json
{"id":135,"char_id":200000,"action":"give_item","status":"pending","requested_by":"doc-verify",
 "created_at":"2026-08-23T18:28:12","claimed_by":null,"finished_at":null,
 "error_message":null,"overlay_responding":true}
```

Then poll the id:

```
python -m ro_admin.cli get commands/135
```

### The 202 is acceptance, not outcome — and its status may already be final

The body carries the row's **real** status at the instant it was read back. That is
normally `pending`, because normally nothing has happened yet. It is **not
guaranteed**: the insert and the read-back are separate round trips, the overlay polls
every 1000ms, and roughly 7% of measured requests came back already terminal.

**Branch on the status you were handed. Do not assume `pending`.** If it already says
`executed` or `failed`, that is the observed outcome and there is nothing to wait for
— polling further tells you nothing new. The API does not normalise the field to
`pending`, because reporting a state nobody observed is the exact defect this product
exists to remove.

### Report `executed` only when you have read `executed`

This is the one rule in this section that matters more than the others.

**Never tell anyone a change was applied because the POST returned 202.** 202 means
the row was written to a queue. Nothing has reached the game. If you stop there and
report success, you have made the same claim the predecessor tool made — and it was
wrong often enough to be why this project exists.

The four statuses:

| Status | What you may say |
|---|---|
| `pending` | Queued. Nothing has happened in the game yet. |
| `processing` | The overlay has claimed the row this tick. Still nothing you can report. |
| `executed` | The overlay performed the action **and read the game state back to confirm it landed**. Only this word licenses "the change was applied." |
| `failed` | Nothing was changed. `error_message` says why; relay it. |

`executed` is load-bearing precisely because it is verified rather than attempted:
rAthena's script engine cannot report a failed command back to a script, so the
overlay re-reads the player's inventory or zeny and compares. A grant that was
silently refused — full inventory, overweight, stack limit, nonexistent item id, a
partial delivery, a `MAX_ZENY` clamp, a partial debit — lands as `failed`, not as a
cheerful `executed`.

### `failed: character is not online` is a refusal, not a bug

Tier 1 has **no offline fallback** and that is deliberate. Writing to the database
directly would bypass the game's rules and skip its logging, so the overlay declines:

```json
{"id":135,"char_id":200000,"action":"give_item","status":"failed","requested_by":"doc-verify",
 "created_at":"2026-08-23T18:28:12","claimed_by":1787505087035,"finished_at":"2026-08-23T18:28:12",
 "error_message":"character is not online","overlay_responding":true}
```

Report it as a refusal, say that nothing was changed, and offer the remedy: ask the
player to log in, then reissue. **Do not look for another route to make the change.**

Two other refusals you may see. `could not attach - player is busy in a script or
offline` means the player is mid-conversation with an NPC and the overlay declined to
interrupt them — reissue in a moment. `no such character` means the `char_id` does not
exist; reissuing will not help, so check the id rather than retrying.

### A stuck `pending` is a diagnosis, not a reason to keep polling

Every command row carries `overlay_responding`. If a row stays `pending` **and
`overlay_responding` is false**, nothing is consuming the queue — the map server or
the script is down. Say that, re-read `system/capabilities` for the reason, and stop.
Do not poll forever.

If `overlay_responding` is true, expect at most a few seconds: the overlay drains
**at most one action per second**, and that is an upper bound rather than a rate. A
queue of thirty rows takes at least thirty seconds.

### The two actions

| Action | Body | Bounds |
|---|---|---|
| `give_item` | `{"action":"give_item","char_id":N,"item_id":N,"amount":N}` | `amount` 1..30000 (rAthena's `MAX_AMOUNT`) |
| `adjust_zeny` | `{"action":"adjust_zeny","char_id":N,"delta":N}` | `delta` -1000000000..1000000000, and **never 0** |

`adjust_zeny` is a **delta**, not an absolute value. Negative removes. There is no
"set zeny to X" action, because doing that through the game server means read,
subtract, apply — which races the player's own earning and spending. If an operator
asks you to set an absolute balance, say that only a delta is offered, and do not
compute one from a `char.zeny` you read yourself: that row is a stale mirror while the
player is online.

A `delta` of 0 is rejected with **422** before anything is queued. The game refuses
`@zeny 0` outright, and the overlay's verification cannot tell that refusal apart from
a real change of zero.

### Item names come from the server

```
python -m ro_admin.cli get items/501
```

```json
{"item_id": 501, "name": "Red Potion", "name_aegis": "Red_Potion", "type": "healing", "subtype": null}
```

Resolve an id this way before confirming a grant to an operator, and **never from a
lookup table you carry yourself** — `item_db` has tens of thousands of rows and yours
will be wrong for the one that matters. A 404 means no such item; say so rather than
queueing a grant that will come back `failed`.

### Answering "is this change logged?" — the honest answer differs by action

An item grant and a zeny adjustment do **not** get the same treatment, and you must
not imply they do.

- **`give_item` reaches `picklog` on a stock rAthena**, recording the receiving
  `char_id`. Item logging ships enabled (`enable_logs: 0xFFFFFFFF`).
- **`adjust_zeny` reaches `zenylog` only if that server set `log_zeny`, which ships
  at 0.** On a stock install the change is not in the game's logs at all. And where it
  *is* enabled, every row lands with **`src_id = 0`** — rAthena's `@zeny` never passes
  the actor through — so the log records the change and never who caused it.

You cannot read the server's `conf/log_athena.conf`, so **do not assert either way
from memory.** What you can do: after the row reads `executed`, check
`logs/zeny char_id=...` for a matching row. If one is there, logging is on for this
server; if not, say that this server does not log zeny and that the record of the
change is the command row itself. Presence of `zenylog` in `tier0.log_tables` is not
evidence — the table exists whether or not anything writes to it.

Either way, the durable record of **who asked** is `requested_by` on the command row.
That is true for both actions, and for zeny it is the only such record there is.

## Boundaries

- **Two write actions, and no more.** `give_item` and `adjust_zeny`, through Tier 1,
  when the server has it. If asked to do anything else that changes state — ban an
  account, edit stats, change a password, delete a character — say plainly that
  `ro-admin` cannot, and stop. Do not reach around it into the database or another
  tool, and do not fall back to a direct write when Tier 1 refuses.
- **Never print a token.** The CLI redacts anything JWT-shaped from its own output;
  do not defeat that by echoing `$RO_ADMIN_TOKEN` or pasting one into a summary.
- **Log data is player data.** Chat logs, where enabled, contain private
  conversation. Retrieve what the question needs, quote sparingly, and do not bulk-
  dump personal history into a transcript.
- **A 403 is an answer, not an obstacle.** It means the token's scope excludes that
  endpoint. Report it and ask for a wider scope if the task genuinely needs one —
  never work around it.
