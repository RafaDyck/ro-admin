---
name: ro-admin
description: Use when investigating or administering an rAthena Ragnarok Online server through a ro-admin API — answering questions about what happened to a character, auditing GM commands, tracing zeny or item history, looking up an account or character, reading a character's inventory, checking which install tiers a server has, or granting an item or adjusting zeny through the Tier 1 overlay. Triggers on questions like "what happened to this character", "who gave that item", "show me the GM commands", "trace this player's zeny", "look up this account", "which characters does this account have", "what is in their inventory", "how much zeny does this character have", "give this player an item", "refund their zeny", or any forensic question about an RO server.
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

An investigation over the logs needs `logs.read` and `system.read` and nothing more.
`accounts.read` and `characters.read` add the account and character reads described
below; ask for them only when the question is about who an account is or what a
character currently holds. `commands.read` lets you check the outcome of a queued
action; `commands.write` lets you queue one and is Admin-level (99). Do not ask for
`commands.write` on the chance you might need it.

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

## Accounts and characters

Six Tier 0 reads over rAthena's own `login` and `char` tables. All six are reads;
nothing here writes to either table.

| Endpoint | Scope | Filters |
|---|---|---|
| `accounts` | `accounts.read` | `userid` (exact), `min_group_id`, `limit`, `offset` |
| `accounts/{account_id}` | `accounts.read` | — |
| `accounts/{account_id}/characters` | **`characters.read`** | — |
| `characters` | `characters.read` | `name` (exact), `account_id`, `online`, `limit`, `offset` |
| `characters/{char_id}` | `characters.read` | — |
| `characters/{char_id}/inventory` | `characters.read` | — |

Both scopes require rAthena group 10 (Staff). Note the third row: the characters
*under* an account are gated by `characters.read`, not `accounts.read`, so a token
that can list accounts is not thereby able to list their characters. Ask for both
scopes when the question spans both.

Observed:

```
python -m ro_admin.cli get accounts min_group_id=10
```

```json
{"items": [{"account_id": 2, "userid": "admin", "sex": "S", "group_id": 99,
            "state": 0, "banned": false, "unban_time": 0, "expiration_time": 0,
            "logincount": 0, "lastlogin": null, "character_slots": 0,
            "vip_time": 0}],
 "limit": 50, "offset": 0}
```

```
python -m ro_admin.cli get characters name=Kami
```

```json
{"items": [{"char_id": 150000, "account_id": 2000005, "name": "Kami",
            "class": 4013, "base_level": 99, "job_level": 70, "base_exp": 0,
            "job_exp": 0, "zeny": 10249324, "status_point": 100391,
            "skill_point": 69, "party_id": 0, "guild_id": 0,
            "last_map": "geffen", "last_x": 52, "last_y": 134, "online": false,
            "last_login": "2026-08-23T21:30:51", "delete_date": 0,
            "unban_time": 0, "stale": false, "stale_fields": []}],
 "limit": 50, "offset": 0}
```

```
python -m ro_admin.cli get characters/150002/inventory
```

```json
{"char_id": 150002,
 "items": [{"item_id": 501, "item_name": "Red Potion", "amount": 15,
            "refine": 0, "identified": true, "equipped": false},
           {"item_id": 501, "item_name": "Red Potion", "amount": 100,
            "refine": 0, "identified": true, "equipped": false},
           {"item_id": 501, "item_name": "Red Potion", "amount": 3,
            "refine": 0, "identified": true, "equipped": false}],
 "stale": true}
```

Every list above is trimmed to fit; the fields are as returned. **Item names are
resolved server-side from the operator's own `item_db`**, so you need no id-to-name
table of your own here either. And note the repeated `item_id: 501`: inventory comes
back one entry per stored row, not aggregated, so sum the `amount`s yourself if
someone asks how many of an item a character holds.

### The staleness rule — what you are allowed to claim

Every character response carries `stale` and `stale_fields`, present whether true or
false. Values are always reported and never withheld, because a `null` meaning "we
chose not to tell you" is indistinguishable from a real zero.

**When `stale` is true, you may not present any field named in `stale_fields` as
current.** Say that the value may be up to five minutes old, every time you quote
one.

Observed with that character logged in, trimmed to the fields that matter here:

```json
{"char_id": 150002, "name": "acct", "zeny": 2131701, "base_level": 200,
 "last_map": "geffen", "online": true, "stale": true,
 "stale_fields": ["base_exp", "base_level", "job_exp", "job_level", "last_map",
                  "last_x", "last_y", "skill_point", "status_point", "zeny"]}
```

Where the five minutes comes from: `char` and `inventory` are **mirrors** of state
the map server holds in memory. It does not write through — it flushes on logout, or
every `autosave_time`, which is 300s by default. Measured: after an in-game +777 zeny
change, `char.zeny` was unchanged at t+0s, +2s, +5s, +10s and +20s, then read exactly
+777 after logout.

So, for the response above:

- Wrong: "acct has 2,131,701 zeny."
- Right: "the char table records 2,131,701 zeny as of the last save. The character is
  online, so that figure may be up to five minutes old."

If the question genuinely needs a live number there are two honest routes and no
third:

- **The character is offline.** Then `stale` is false, the map server has flushed,
  and the row is the game's own saved state.
- **Ask the logs instead.** `logs/zeny` and `logs/items` are written by rAthena when
  the event happens rather than on autosave, so "what did they gain today" is a log
  question, not a `char` question. Check first that this server logs the category you
  need — zeny logging ships off, see the Tier 1 section below.

`inventory` carries the same `stale` flag for the same reason: an item granted
seconds ago may not be in it yet.

### `online` here is not "has a live map session"

`online` is the **char server's** record, written at session start and end. Whether
the map server currently holds a session is a different question, answered inside the
game by `isloggedin()`. The two disagree after a crash — the char row can still read
`online: true` for a character the map server has no session for.

This is why a Tier 1 command can come back `failed: character is not online` for a
character this endpoint just showed as online. That is not a contradiction to
investigate and not a reason to retry in a loop. Report both readings, say the map
server is the authority for that question, and offer the remedy: have the player log
in again, then reissue.

### Account responses contain no password, pincode or token, structurally

The `login` table does hold `user_pass` — on a stock rAthena, the password itself.
This API serves an explicit **allowlist** of columns (`src/ro_admin/projections.py`),
so `user_pass`, `pincode`, `web_auth_token` and the personal-data columns are not
absent by convention but unreachable: `tests/test_no_credentials_leak.py` reads the
live table definition and fails the build if any withheld column ever reaches a
response.

So when asked to retrieve, reset or verify a password, a pincode or a session token:
**say that ro-admin does not expose one, and stop there.** Do not go looking in
another endpoint, another table, or the database directly. Nothing in this API edits
a `login` or `char` row either, so there is no reset path here to offer instead.

**`banned` is derived, and you should read it rather than reconstruct it.** rAthena
records a ban in two unrelated places — `state = 5` for a permanent ban, `unban_time`
in the future for a temporary one — and `banned` is true for either. Both raw columns
are still in the response, but do not interpret `state` yourself: other non-zero
states exist and mean other things.

### `class` is a bare id, and Tier 0 has no name for it

`"class": 4013` is an rAthena job id, and no job name appears in the response because
this API has none to give. Item names come from the operator's own `item_db`, which
is in the database; job data is not — rAthena keeps it in YAML on the server's
filesystem, which ro-admin never reads.

**Report the id.** Do not translate it from memory: job id tables vary by rAthena
revision and by whatever the operator has customised, and a confidently wrong job
name is worse than a number. A name is a Tier 1 capability in principle — the game
server's own `jobname()` resolves exactly this — but Tier 1 today offers only
`give_item` and `adjust_zeny`, so the honest answer is that no job name is available
through ro-admin.

### Filters and paging

Both list endpoints take `limit` (1..500, default 50) and `offset`. An out-of-range
`limit` is rejected with **422** before any query runs — `accounts?limit=501`
observed as 422.

**`userid` and `name` are exact matches, not prefix or substring.** Observed:
`accounts userid=admin` returns account 2 alone and not `admin1234`; `characters
name=Kam` returns an empty list rather than `Kami`. An empty result is therefore weak
evidence — consider that you were handed a partial name before reporting that no such
account or character exists.

`min_group_id` is a floor: `min_group_id=10` returns Staff and above. `account_id` on
`characters` and the `accounts/{id}/characters` sub-resource answer the same question
from two directions.

An unknown id is a 404 carrying the id — `{"detail":"no account with id 999"}` — and
`accounts/{id}/characters` 404s for an account that does not exist rather than
returning an empty list. An empty list would assert that the account exists and has
no characters, which is a different claim and, there, a false one.

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
- **Log data is player data, and so are account and character reads.** Chat logs,
  where enabled, contain private conversation; an account row identifies a person.
  Retrieve what the question needs, quote sparingly, and do not bulk-dump personal
  history or a whole account listing into a transcript.
- **A 403 is an answer, not an obstacle.** It means the token's scope excludes that
  endpoint. Report it and ask for a wider scope if the task genuinely needs one —
  never work around it.
