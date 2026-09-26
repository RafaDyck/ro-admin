# Changing the game, on a server that has the overlay

Read this when the question is about changing the game — granting an item or
adjusting zeny — or about making the stored row for a logged-in character true,
which is `sync_character`, at the end of this file.


Two Tier 1 actions, and only two: **grant an item** and **adjust zeny**. They are
applied by an NPC script running inside the game server, so the game's own rules
apply, the change is visible to the player immediately, and the server logs it to the
extent it is configured to. There is no direct-database path and you must not build
one.

A third action, **`sync_character`**, needs **Tier 2** — a compiled hook, so a
recompiled map server rather than a copied file. It changes nothing in the game: it
flushes what the map server holds in memory into the database, so a stored value stops
being a guess. It has its own section at the end and **its own capability check**, and
a server with Tier 1 refuses it.

Every tier here is optional, and a server may have neither. Everything below starts
with finding out.

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

The CLI has a write verb. `post` takes an endpoint and `key=value` body fields,
and reads the token from the environment as `get` does — so no bearer token is
assembled by hand or left in a shell history:

```
python -m ro_admin.cli post commands action=give_item char_id=200000 item_id=501 amount=3
```

It exits **0** on any 2xx — a 202 is an accepted enqueue, which is the whole of
what an enqueue can promise — and **1** on a refusal, printing the response body
either way. A non-zero exit here is the server declining, not the tool failing,
so read the body before reporting anything.

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

### The two Tier 1 actions

| Action | Body | Bounds |
|---|---|---|
| `give_item` | `{"action":"give_item","char_id":N,"item_id":N,"amount":N}` | `amount` 1..30000 (rAthena's `MAX_AMOUNT`) |
| `adjust_zeny` | `{"action":"adjust_zeny","char_id":N,"delta":N}`, and `"confirm":true` as well whenever `delta` is negative | `delta` -1000000000..1000000000, and **never 0** |

`adjust_zeny` is a **delta**, not an absolute value. Negative removes. There is no
"set zeny to X" action, because doing that through the game server means read,
subtract, apply — which races the player's own earning and spending. If an operator
asks you to set an absolute balance, say that only a delta is offered, and do not
compute one from a `char.zeny` you read yourself: that row is a stale mirror while the
player is online.

A `delta` of 0 is rejected with **422** before anything is queued. The game refuses
`@zeny 0` outright, and the overlay's verification cannot tell that refusal apart from
a real change of zero.

#### A negative `delta` needs `confirm`, and the API is what enforces it

Taking zeny away destroys value, so `adjust_zeny` with a negative `delta` requires
`"confirm": true` in the body. The check runs **in the API, regardless of caller**
— an instruction to a client is not enforcement, and this file is an instruction
to a client. Without the flag the request is a **422** and nothing is queued:

```
python -m ro_admin.cli post commands char_id=200000 action=adjust_zeny delta=-500
```
```json
{"detail": [{"type": "value_error", "loc": ["body", "adjust_zeny"],
  "msg": "Value error, removing 500 zeny is destructive; resend with confirm=true to proceed"}]}
```

**`detail` here is a list of dicts, not a string.** The other refusals in this file
— the 409 when Tier 1 is unavailable, a 404 on an unknown id — return
`{"detail": "..."}`, one plain sentence. A validation failure returns a different
shape: one entry per rejected field, and the sentence you want is
`detail[0]["msg"]`. Read it from there rather than pasting the whole structure at
an operator, and do not report a 422 as though the server had said nothing.

Resend with the flag once whoever asked for the deduction has confirmed it:

```
python -m ro_admin.cli post commands char_id=200000 action=adjust_zeny delta=-500 confirm=true
```

A positive `delta` does not need `confirm`, and neither does `give_item`. The gate
is narrow on purpose: a flag that every caller learns to always send is the same as
having no gate at all.

### Item names come from the server

```
python -m ro_admin.cli get items/501
```

```json
{"item_id": 501, "name": "Red Potion", "id": 501, "name_english": "Red Potion",
 "name_aegis": "Red_Potion", "alias_name": null, "type": "healing",
 "subtype": null, "script": "itemheal rand(45,65),0;\n"}
```

Trimmed — the full response is in `references/items.md`, under "Items: finding
one, and reading what it does". Resolve an id this way before confirming a grant to an operator, and
**never from a lookup table you carry yourself** — `item_db` has tens of thousands of
rows and yours will be wrong for the one that matters. A 404 means no such item; say
so rather than queueing a grant that will come back `failed`.

If the operator named an item instead of giving you an id, **search for it** —
`items q=<name>` — and confirm the id and name back to them before you queue
anything. Read `script` too when the request is about what the item does: a grant
of the wrong "potion" is not recoverable through this API.

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

## `sync_character` — a Tier 2 action, and a different capability check

`sync_character` makes the **stored row for a logged-in character true**. It changes
nothing in the game. Use it when the question is "what does this character have *right
now*" and the character is online, because the `char` table is otherwise a mirror up to
five minutes behind — see `references/entities.md` for what you may and may not claim
about a stored value.

### Check `tier2`, not `tier1`

```
python -m ro_admin.cli get system/capabilities
```

**The tiers are reported separately and a Tier 1 server does not have this action.**
Reading `tier1.available` and posting a `sync_character` on the strength of it is the
mistake this paragraph exists to prevent. The `tier2` object on a server that has it,
observed:

```json
"tier2": {
  "available": true,
  "reason": "overlay responding, last seen 1s ago",
  "installed": true,
  "responding": true,
  "version": "1"
}
```

If `tier2.available` is false, **relay `reason` verbatim and stop**, exactly as for
Tier 1. Posting anyway returns **409 with the same string**. Tier 2 needs a recompiled
map server, so the remedy is longer than a file copy and it is not yours to perform:

```
"tier 2 not installed: run overlay/tier2/schema.sql against this database, then follow overlay/tier2/README.md to compile the hook"
"tier 2 tables exist but the hook has never reported in: the compiled hook is missing or the script is not loaded -- follow overlay/tier2/README.md (src/custom/script.inc, rebuild map-server, then load overlay/tier2/ro_admin_tier2.txt and @reloadscript)"
```

A server with Tier 1 and no Tier 2 is a **normal, supported install**. When someone
asks for a live figure there and cannot have one, say so and offer the two honest
routes in `references/entities.md` — wait for the character to log out, or ask the
logs — rather than presenting a stale number as current.

### Enqueue it like any other action

Two fields, and there is nothing else to choose: the hook flushes the whole character,
not a field.

| Action | Body | Needs |
|---|---|---|
| `sync_character` | `{"action":"sync_character","char_id":N}` | Tier 2, and the character online |

```
python -m ro_admin.cli post commands char_id=150002 action=sync_character
```

Observed — `202 Accepted`:

```json
{"id":645,"char_id":150002,"action":"sync_character","status":"pending","requested_by":"admin1234",
 "created_at":"2026-09-26T18:51:14","claimed_by":null,"finished_at":null,
 "error_message":null,"overlay_responding":true}
```

Then poll the id, and read `executed` before claiming anything — the rule above applies
here unchanged.

### `failed: flush queued but not yet persisted - retry` means RETRY

**This is the one thing in this section that will otherwise be reported wrong.** An
agent that sees this and tells the operator "the sync failed" is technically accurate
and practically useless: the answer was "not yet", and one more attempt normally gets
it.

Observed, both rows from one sequence four seconds apart:

```json
{"id":640,"char_id":150000,"action":"sync_character","status":"failed","requested_by":"admin1234",
 "created_at":"2026-09-26T18:49:48","claimed_by":1790447748711,"finished_at":"2026-09-26T18:49:48",
 "error_message":"flush queued but not yet persisted - retry","overlay_responding":true}
```
```json
{"id":641,"char_id":150000,"action":"sync_character","status":"executed","requested_by":"admin1234",
 "created_at":"2026-09-26T18:49:52","claimed_by":1790447748711,"finished_at":"2026-09-26T18:49:53",
 "error_message":null,"overlay_responding":true}
```

Why it happens: the hook hands the save to the char server and returns — it does not
write MySQL itself — and the char server commits a moment later. The overlay will not
record a sync it has not seen land, so it reads the stored value back, finds the write
still in flight, and fails the row rather than claiming a freshness nobody observed.
That refusal is the same discipline as `executed` meaning verified.

**So: reissue, up to a small number of times.** Each attempt costs about a second. The
repeatable measurement in the private lab's `harness/test_tier2.py` needed **two**
attempts to land a +777; treat three or four consecutive `not yet persisted` rows as a
real problem and report it, rather than looping.

**Only that exact message means the write is in flight.** Every other `error_message`
is an answer about this request, and each one says something different about what to do
next — do not treat them as one "it failed":

| `error_message` | What it means |
|---|---|
| `character is not online` | Nothing to flush. The map server is not holding this character, so **the stored row is already authoritative** — which is the answer the caller wanted, not a failure to work around. Read the character and report the row as current. |
| `no such character` | The `char_id` does not exist. Check the id. |
| `could not attach - player is busy in a script or offline` | The player is mid-conversation with an NPC and the overlay declined to interrupt them. Reissue in a moment. |
| `attached session is a different character` | The session the overlay reached is not the character that was asked for — seen when a player reconnects quickly. Reissue once; if it repeats, report it. |
| `sync hook could not flush - char server down or no session` | The char server is not reachable from the map server. An operator problem; relay it. |

Observed, for the first of those:

```json
{"id":643,"char_id":200000,"action":"sync_character","status":"failed","requested_by":"admin1234",
 "created_at":"2026-09-26T18:50:04","claimed_by":1790447748711,"finished_at":"2026-09-26T18:50:04",
 "error_message":"character is not online","overlay_responding":true}
```

### What a successful sync licenses you to say, and what it does not

After `executed`, re-read the character. `synced_at` carries the moment the stored row
was **observed** to match the game's memory, and `stale` is false while that
observation is under a minute old.

**It is evidence about the `char` row and not about the inventory.**
`GET /characters/{char_id}/inventory` still reports `stale: true` for an online
character after a successful sync — observed, in the same second — because nothing
watched the inventory land. Do not tell anyone an inventory is current because a
character sync succeeded. `references/entities.md` has the full rule and the observed
pair.
