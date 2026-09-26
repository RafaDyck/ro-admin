# Tier 1: changing the game, on a server that has the overlay

Read this when the question is about changing the game — granting an item or
adjusting zeny.


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

### The two actions

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

