# Tier 1 overlay

Tier 0 reads your database. **Tier 1 makes changes happen inside the running
game**, which is a different thing and worth understanding before you install
it.

## What Tier 1 actually buys you

The usual pitch is "no relog required". That is true and it is the smaller
half. The real difference is correctness and — with one important asymmetry —
auditability:

| | Direct database write | Through this overlay |
|---|---|---|
| Item grant appears in `picklog` | **No** | **Yes**, on a stock rAthena |
| Zeny change appears in `zenylog` | **No** | **Only if you enabled `log_zeny`** — off by default — and the row never names who did it |
| Who requested it is recorded | Nowhere | Always, in `ro_admin_commands.requested_by` |
| Inventory stacking, weight and caps | Whatever you INSERT | The game's own rules |
| Visible to a logged-in player | After relog | Immediately |
| A change that did not take effect | Looks like success | Recorded as `failed`, with a reason |

An item written straight into the `inventory` table leaves no trace in the
game's logs at all, because the game server was never involved. On a server
with real players and real disputes that is the difference between "we can
show what happened" and "we cannot".

This overlay therefore has **no offline fallback**. If the target character is
not logged in, the command fails and says so — `status: failed`,
`error_message: "character is not online"`, and nothing is written anywhere.
Silently degrading to a direct write would trade auditability for availability
without telling you.

### The zeny asymmetry, in full

Do not skip this. If you believe zeny adjustments are audited when they are
not, you are exactly the person this project exists to protect.

**Item grants are audited by default.** rAthena gates pick-logging on
`enable_logs & LOG_TYPE_SCRIPT` (`src/map/log.cpp:210-213`) and ships
`enable_logs: 0xFFFFFFFF` (`conf/log_athena.conf:44`). So on a stock install a
grant through this overlay writes a `picklog` row naming the receiving
`char_id`, at the second it happened. Verified against a live server.

**Zeny changes are not.** Zeny logging is gated separately on `log_zeny`
(`src/map/log.cpp:281-282`), and rAthena ships it at `0`
(`conf/log_athena.conf:93`); the compiled default is 0 as well
(`src/map/log.cpp:578`). On a stock install `@zeny` writes **no `zenylog` row
at all**.

And turning it on does not get you all the way. rAthena's `ACMD_FUNC(zeny)`
calls `pc_getzeny` / `pc_payzeny` with three arguments
(`src/map/atcommand.cpp:2904,2909`), so the optional `log_charid` parameter
takes its default of 0 (`src/map/pc.hpp:1448,1450`). **Every `zenylog` row
produced this way lands with `src_id = 0`** — confirmed against live data. The
log records the change. It never records the actor.

To get zeny into the game's own logs, set `log_zeny` and restart the map
server. Put it in `conf/import/log_conf.txt` rather than editing
`conf/log_athena.conf`, which is a tracked upstream file — the import seam is
what lets you keep pulling rAthena:

    log_zeny: 1

`0` is off, `1` logs any change, and `2` upward is a minimum absolute value
worth recording.

Even then, the only durable record of *who asked* for a zeny adjustment is
`ro_admin_commands.requested_by`, written by this API before the row is queued.
That column is populated on every row, for both actions, from the first row
onward — an audit trail added later is an audit trail with a hole in it.

Two more things to check while you are in that file. Imports load **after** the
main config, so a value in `conf/import/log_conf.txt` wins — a stray
`enable_logs: 0` there silently disables all pick-logging server-wide no matter
what `log_athena.conf:44` says. And `sql_logs: yes` is what sends logs to MySQL
rather than flat files; this API can only read the MySQL ones.

## Install

Three steps. No recompile, no changes to any rAthena file that upstream owns.

**1. Create the tables**

    mysql -u <your_user> -p <your_database> < schema.sql

Idempotent; safe to re-run.

**2. Copy the script into rAthena's custom directory**

    cp ro_admin_overlay.txt /path/to/rathena/npc/custom/

**3. Enable it**

Add this line to `npc/scripts_custom.conf` -- the file rAthena ships for
exactly this purpose:

    npc: npc/custom/ro_admin_overlay.txt

Then reload, either with `@reloadscript` in game or by restarting the map
server.

## Verify

Ask the API, which reports what it observes rather than what it assumes:

    GET /api/v1/system/capabilities

It needs a token with the `system.read` scope, like every other endpoint:

    export RO_ADMIN_TOKEN=$(python scripts/mint_token.py verify system.read --days 1)
    python -m ro_admin.cli get system/capabilities

A healthy install answers like this (the `tier1` object, from a live lab):

```json
{
  "available": true,
  "reason": "overlay responding, last seen 0s ago",
  "installed": true,
  "responding": true,
  "version": "1"
}
```

`installed` means the tables exist. `responding` means the script wrote a
heartbeat recently — within ten polls, or five seconds, whichever is longer.
They are reported separately because the fixes differ: run `schema.sql`, versus
check that your map server is up and the script loaded.

If `available` is false, `reason` names the next step. There are four
unavailable states, and each one's message is distinct enough to match against
what you see:

| What is wrong | `reason` |
|---|---|
| Tables missing | `overlay not installed: run overlay/schema.sql against this database` |
| Tables exist, script never ran | `overlay tables exist but the script has never run: copy overlay/ro_admin_overlay.txt into npc/custom/, enable it in npc/scripts_custom.conf, then @reloadscript` |
| Script stopped writing | `overlay script last responded 46s ago (stale after 10s); is the map server running?` |
| Script older or newer than the API | `installed overlay is version 0, this API expects 1: copy the current overlay/ro_admin_overlay.txt and @reloadscript` |

The numbers in the last two vary; the wording does not. All four were produced
against a live server rather than read off the source.

`POST /api/v1/commands` returns **409** carrying the same `reason` string when
Tier 1 is unavailable, rather than queueing work that nothing will consume.

## What it does

The script polls `ro_admin_commands` once a second, claims one pending row,
`attachrid`s to the target character, performs the action, verifies it, and
records the outcome on the row. It writes a heartbeat to `ro_admin_overlay` on
every poll — first, and unconditionally, so that one failing command never
looks like an uninstalled overlay.

### The two actions

| Action | Arguments | Notes |
|---|---|---|
| `give_item` | `item_id` (1..2147483647), `amount` (1..30000) | 30000 is rAthena's `MAX_AMOUNT`; a larger request is refused here rather than by the game |
| `adjust_zeny` | `delta` (-1000000000..1000000000, **not 0**) | Relative. Negative removes. |

**`adjust_zeny` is a delta, and there is no absolute "set zeny".** Setting an
absolute value through the game server means read-current, compute-difference,
apply — which races the player's own earning and spending in between. Only the
operation that maps atomically onto `@zeny` is offered.

**A zero delta is rejected with 422**, before anything is queued. rAthena's
`@zeny` returns early on a zero argument without touching the player
(`src/map/atcommand.cpp:2897-2900`), and the post-condition check below cannot
tell that refusal apart from a real change of zero — so a zero delta would be
recorded as `executed` for work the game never did.

### `executed` means it was observed, not attempted

rAthena's script engine cannot report a failed command back to the script: a
`SCRIPT_CMD_FAILURE` becomes a console warning and execution continues
(`src/map/script.cpp:4136-4140`). So `getitem` and `@zeny` have no way to tell
the overlay they did nothing. Left there, every row would be stamped
`executed`.

Instead, each action re-reads the player state it just changed and compares it
against what was asked. If the change did not happen, the row is stamped
`failed`. That is what makes `executed` worth reading. It catches:

- a nonexistent item id
- a full inventory, or a player already overweight
- the per-stack limit
- **partial delivery** of non-stackables, where `getitem` adds one unit per
  iteration and stops at the first rejection
- the `MAX_ZENY` clamp on a gain
- a **partial debit**, where the player has less zeny than you asked to remove
  and rAthena silently deducts only what is there

**Do not check your work by reading the `inventory` or `char` tables.** While a
character is online, those tables are stale mirrors: the map server holds the
real state in memory and flushes it on logout or every `autosave_time`
(300 seconds by default). A grant that has genuinely landed will not appear
there for minutes. `picklog` and `zenylog` are written immediately, and so is
the command row — read those instead.

**Known limit.** For a pet-egg item id, `getitem` hands off to an asynchronous
call to the char server (`src/map/pet.cpp:684`) and the egg arrives after the
tick has ended. The check therefore stamps such a row `failed` even though the
egg may still land. Tier 1 does not expose pet eggs. This is the safe direction
to be wrong in — the overlay under-claims rather than over-claims — but it is
wrong, and worth knowing if you extend it.

### Throughput: at most one action per second

The script processes one row per tick, and the timer restarts at the *end* of
the body (`src/map/script.cpp:11744-11746`), not the start. The real period is
1000ms plus however long the body took, across several MySQL round trips on the
map-server thread. So one action per second is an **upper bound**, not a rate.
Tier 1 is for administrative actions, not for bulk distribution.

One consequence worth planning for: because the queue can drain that fast, a
`POST` can come back with a row that is *already* `executed` or `failed`. The
insert and the read-back are separate round trips. The API reports the row's
real status rather than synthesising `pending`, so branch on what you are
handed.

## A note on the citations in the script

`ro_admin_overlay.txt` is unusually heavily commented, and many comments carry
`src/map/...:NNN` references into rAthena's own source. Those line numbers were
read against **rAthena commit `ad04a42` (2025-08-04)**.

They will drift. rAthena is a live project and line numbers are not stable
addresses, so treat a reference that no longer matches as stale rather than as
evidence the comment is wrong -- and re-derive rather than assume. The
behaviours described were verified at that revision against a running server,
not inferred.

This matters more than usual here because rAthena's behaviour itself differs
across versions in at least one place the overlay depends on: at `ad04a42` a
rejected item inside `getitem`'s loop returns failure, while on older trees it
is dropped to the ground and the loop carries on regardless. The overlay does
not rely on either -- it reads the inventory back and compares. That is why.

## Security notes

- The queue carries `char_id` and integers. It never carries a character name
  or a command string, so nothing from your web tier is concatenated into SQL.
- The script does not grant anyone GM rights. `attachrid` rebinds which
  character the script is acting *as*; it is identity, not privilege.
- The script will not interrupt a player who is mid-conversation with another
  NPC. Such a row fails cleanly with `could not attach - player is busy in a
  script or offline`, and can simply be reissued.
- Enqueueing requires `commands.write`, which is level 99 (Admin). Reading a
  command's outcome requires `commands.read`, level 10 (Staff).

## Uninstall

Remove the line from `npc/scripts_custom.conf`, `@reloadscript`, and if you
want the tables gone:

    DROP TABLE ro_admin_commands;
    DROP TABLE ro_admin_overlay;

Nothing else in your database was touched. The API drops back to Tier 0 and
reports it.

Dropping the tables while the script is still loaded makes it log a
`query_sql` failure on every tick — noisy, but harmless, and it recovers by
itself the moment the tables come back. That was measured: after re-running
`schema.sql`, the same script instance was reporting healthy again on its next
poll, with no reload.

## Upgrading

The script declares a version, and the API refuses to report Tier 1 available
against a version it does not expect -- so copying a new release and forgetting
to `@reloadscript` tells you, rather than failing strangely later.
