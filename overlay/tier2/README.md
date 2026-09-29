# Tier 2 hook

Tier 1 makes changes happen inside the running game. **Tier 2 makes the
database tell the truth about a character who is still logged in.**

## It costs a rebuild of your map server. Read this part first.

Tier 1 is a `.txt` file and a `.sql` file. Nothing is compiled, and no file
rAthena owns is edited.

Tier 2 is not that. It is a compiled script command, so installing it means
pasting two files into rAthena's `src/custom/` and **rebuilding the map
server**. On this project's lab that build takes roughly six minutes from a
warm Docker cache; from cold, or on a smaller machine, budget considerably
more. You will also have to redo it every time you pull rAthena.

**It is entirely reasonable to read this page and decide not to install it.**
Tiers 0 and 1 do not depend on Tier 2 in any way:

- every Tier 0 read keeps working, unchanged;
- every Tier 1 action keeps working, unchanged;
- `/system/capabilities` reports `tier2.available: false` with the install step
  in `reason`, rather than failing or guessing;
- `POST /api/v1/commands` with `action: sync_character` is refused **409**
  carrying that same sentence, so nothing is ever queued for a consumer that
  does not exist.

What you give up by not installing it is described next, precisely enough to
judge whether it matters on your server.

## What Tier 2 buys

The map server owns a logged-in character's state **in memory**. It writes that
state to MySQL on logout, or every `autosave_time` — 300 seconds on a stock
`conf/map_athena.conf`. Until then the `char` and `inventory` tables are a
mirror that can be five minutes out of date.

This is measured, not inferred. After an in-game **+777 zeny** change,
`char.zeny` was unchanged at t+0s, +2s, +5s, +10s and +20s, and read exactly
+777 **after logout**. `inventory` behaves the same way: empty while the
character is online, one row the moment the session ends.

So for a logged-in character, a stored zeny, level, experience total or map
position may simply be old. Nothing in SQL can fix this, and nothing in script
can either — `chrif_save()` has no script binding on a stock rAthena, and
`savepoint`/`save` set a respawn point rather than persisting anything.

| | Tier 0/1 only | With Tier 2 |
|---|---|---|
| Stored row for an **offline** character | Authoritative | Authoritative (unchanged) |
| Stored row for an **online** character | Up to `autosave_time` (300s) behind | Can be flushed on demand |
| What the API can say about it | `stale: true` — "this character is online, so these fields may be behind" | `synced_at`, and `stale: false` while that observation is fresh and no queued write has been attempted since |
| Basis for that answer | An assumption from `online` | An observation: the stored row was **compared** against live memory |

The API side of this is `synced_at` on every character response, and a `stale`
flag derived from it. `stale` goes false only while `synced_at` is within 60
seconds **and** the character is online **and** no `give_item`/`adjust_zeny`
has been claimed since (processing, executed or failed); an offline character
is never stale, because nothing is holding newer state.

The fields this covers are the ones the map server holds in memory:
`zeny`, `base_level`, `job_level`, `base_exp`, `job_exp`, `status_point`,
`skill_point`, `last_map`, `last_x`, `last_y`.

## Install

Five steps, and the fifth is the one people miss.

### 1. Paste the two files

Both go into rAthena's `src/custom/`, which upstream ships empty for exactly
this purpose — so neither paste conflicts on a `git pull`:

    cat script.inc     >> /path/to/rathena/src/custom/script.inc
    cat script_def.inc >> /path/to/rathena/src/custom/script_def.inc

`script.inc` is the body of one function, `ro_admin_sync()`. `script_def.inc`
is the one line that registers it. **Both are required.** With the body and no
definition the command is still unknown to the parser, which fails exactly as
if you had pasted neither.

Keep a copy of the originals. They are two comment-only stub files, and
restoring them is how you uninstall.

### 2. Rebuild the map server

    make server

Two traps here, both of which have cost this project time:

- **Do not use `make -j`.** rAthena's Makefile does not create the `obj/`
  subdirectories before compiling into them, so a parallel build races and dies
  with `Fatal error: can't create obj/ext/c4core/src/c4/memory_util.o`. Build
  serially.
- **If you build in Docker, `docker compose build` exits 0 on a failed build.**
  The exit status is not a result. Capture the log and grep it:

      docker compose build rathena 2>&1 | tee build.log
      grep -icE "error:|undefined reference|Error 1" build.log   # must be 0

  A build check that reads an always-successful exit status is the same class of
  defect as a healthcheck that cannot fail, and this project has shipped both.

"No errors" is the weaker half of the check, because it is an absence. The
positive half is that the symbol is really in the binary you are about to run:

    nm -C /path/to/map-server | grep ro_admin_sync
    # 000000000028db90 T buildin_ro_admin_sync(script_state*)

`T` is a defined symbol in the text section. That output is the compile *and*
the link, confirmed together. For reference, on this project's lab a full
`make clean && make server` from a warm Docker layer cache took **361 seconds**,
of which `script.cpp` is the single slowest translation unit.

### 3. Create the tables

    mysql -u <your_user> -p <your_database> < schema.sql

Idempotent; safe to re-run. Run `overlay/schema.sql` (Tier 1) first if you have
not — Tier 2 adds no queue of its own, it consumes `sync_character` rows from
`ro_admin_commands`.

### 4. Load the script

    cp ro_admin_tier2.txt /path/to/rathena/npc/custom/

and add to `npc/scripts_custom.conf`:

    npc: npc/custom/ro_admin_tier2.txt

### 5. Make sure your Tier 1 overlay is current, too

**This step is not optional, and skipping it is worse than not installing Tier
2 at all.**

Tier 2 owns the `sync_character` action. The two scripts' claim filters are
complements, and both halves are load-bearing:

| Script | Claim filter |
|---|---|
| `overlay/ro_admin_overlay.txt` | `action <> 'sync_character'` |
| `overlay/tier2/ro_admin_tier2.txt` | `action = 'sync_character'` |

A Tier 1 overlay from before Tier 2 existed **has no `<>` filter**. Load Tier 2
alongside it and both scripts poll for the same `sync_character` rows: whichever
claims one first wins, and which one that is, is a coin flip.

That is not a hypothetical. It is the defect that made this project necessary —
two consumers on one queue, where the impostor reported success for work the
game never did, and it went unnoticed for a year because the correct consumer
usually won. See `docs/ro-lessons-learned.md` in the private lab.

It also fails in a way that looks like something else. A stale Tier 1 instance
that claims a `sync_character` row does not ignore it; it runs its own
online-check *before* its action dispatch and stamps the row
`failed: character is not online`. So the symptom of "you forgot to update Tier
1" is a plausible-looking refusal about the character, not an error about the
action. Observed directly while installing this.

**So: copy the current `overlay/ro_admin_overlay.txt` as well, and reload it.**
`@reloadscript` is enough; the file on disk being current is not — the *running*
script is what claims rows.

Then reload both, with `@reloadscript` in game or by restarting the map server.

**Check it, rather than assume it.** Queue one `sync_character` row by hand and
look at who claimed it — `claimed_by` carries the consuming script's instance id,
and each script's instance id is in its own heartbeat table:

    SELECT c.id, c.action, c.status, c.claimed_by, c.error_message,
           (SELECT instance_id FROM ro_admin_overlay WHERE id=1) AS tier1,
           (SELECT instance_id FROM ro_admin_tier2   WHERE id=1) AS tier2
    FROM ro_admin_commands c ORDER BY c.id DESC LIMIT 1;

`claimed_by` must equal `tier2`. If it equals `tier1`, your Tier 1 overlay is the
old one — stop and update it, because every `sync_character` row from then on is
a coin flip. Two useful sanity results from the lab: with Tier 2 unloaded a
`sync_character` row sat `pending` with `claimed_by` NULL while Tier 1 went on
consuming an `adjust_zeny` row beside it (that is the `<>` filter working), and
it was picked up by Tier 2's instance within a second of Tier 2 being loaded
again.

## Verify

Ask the API, which reports what it observes:

    GET /api/v1/system/capabilities

A healthy Tier 2 install answers like this — from a live lab, not from the
source:

```json
{
  "available": true,
  "reason": "overlay responding, last seen 0s ago",
  "installed": true,
  "responding": true,
  "version": "1"
}
```

`installed` means the two tables exist. `responding` means the script wrote a
heartbeat recently. They stay separate because the fixes differ, and for Tier 2
the difference is large: one is `schema.sql`, the other is a recompile.

Unavailable states and what `reason` says:

| What is wrong | `reason` |
|---|---|
| Tables missing | `tier 2 not installed: run overlay/tier2/schema.sql against this database, then follow overlay/tier2/README.md to compile the hook` |
| Tables exist, no heartbeat ever | `tier 2 tables exist but the hook has never reported in: the compiled hook is missing or the script is not loaded -- follow overlay/tier2/README.md (src/custom/script.inc, rebuild map-server, then load overlay/tier2/ro_admin_tier2.txt and @reloadscript)` |
| Heartbeat stopped | `overlay script last responded Ns ago (stale after 10s); is the map server running, is overlay/tier2/ro_admin_tier2.txt still loaded, and does this build still have the compiled hook? see overlay/tier2/README.md` |
| Version mismatch | `installed overlay is version N, this API expects 1: copy the current overlay/tier2/ro_admin_tier2.txt and @reloadscript` |

Those two middle states are worth telling apart, because **unloading Tier 2
gives you the third row, not the second.** The heartbeat row is written with
`REPLACE`, so unloading the script leaves its last row behind and the tier
classifies as *stale* rather than *never run*. Only an install that has never
had a working hook has no row at all.

That distinction is the reason the stale message above names three causes
instead of asking "is the map server running?". Measured while installing this:
with the Tier 2 script unloaded, the API reported Tier 2 stale and Tier 1
`last seen 0s ago` **in the same response** — so the map server was visibly up,
and the only cause the old message offered was the one the reader could already
rule out.

**The heartbeat is the detection, and that is deliberate.** No configuration
flag says "Tier 2 is installed" anywhere. `ro_admin_tier2.txt` calls
`ro_admin_sync()`, and a script naming a buildin that was not compiled is
discarded at parse time (see below) — so the script cannot run at all unless the
hook is in the running binary. A row in `ro_admin_tier2` is therefore evidence
about the binary, not a setting somebody remembered to flip.

If you want to check it without the API:

    SELECT instance_id, version, TIMESTAMPDIFF(SECOND, last_seen, NOW()) AS age
    FROM ro_admin_tier2 WHERE id = 1;

Run it twice. `age` must stay small — a row that exists but does not advance is
a script that ran once and stopped, which is a different problem from a script
that never ran.

### What a working install looks like end to end

Measured on this project's lab against a live logged-in character, in one run,
and reproduced here because it is also the shortest way to convince yourself the
install is real rather than merely loaded:

| Step | Observed |
|---|---|
| Stored `char.zeny` | `10255540` |
| Tier 1 `adjust_zeny(+777)` | `executed` — the change is in the map server's memory |
| Stored `char.zeny`, re-read | **still `10255540`** — the staleness defect, live |
| `sync_character`, first attempt | `failed`, `flush queued but not yet persisted - retry` |
| `sync_character`, second attempt | `executed` |
| Stored `char.zeny`, re-read | `10256317` — exactly `+777` |
| `ro_admin_sync` | one row for that `char_id`, `synced_at` at that second |

Two things to take from it. The flush is real: a value that was only in memory
is in MySQL afterwards, with no logout. And **the first attempt failing is
normal** — see the limits below.

## A missing hook costs you a log line, not a server

This is the property that makes Tier 2 safe to attempt.

If you load `ro_admin_tier2.txt` without the compiled hook — you forgot the
paste, or the rebuild, or you pulled rAthena and lost `src/custom/` — the
script **does not load**, and that is all that happens:

- the parser hits the unknown command and raises a parse error,
  `disp_error_message2` longjmps, and `parse_script`'s `setjmp` handler returns
  `nullptr` (`src/map/script.cpp:603-608,2503-2518`);
- `npc_parse_script` guards its result with `if (script)`
  (`src/map/npc.cpp:4421-4424`), so no NPC is created;
- the map server logs the error and **keeps running**;
- the Tier 1 overlay is untouched and keeps consuming its own actions;
- no heartbeat appears, so `/system/capabilities` reports Tier 2 unavailable and
  names the install step;
- `sync_character` requests are refused 409 rather than queued.

So a wrong Tier 2 install degrades to "you have Tier 1", loudly, in the log.
It does not take the server down and it does not silently half-work.

**This was observed, not only read.** A scratch NPC naming a buildin that does
not exist was loaded into the lab's map server. The result: one `[Error] script
error on npc/custom/<file> line N` block quoting the offending line; no NPC
created; the statement after the bad call **never ran** (a sentinel `debugmes`
in the script body produced no `script debug` line, and the only occurrence of
its text anywhere in the log was the parser echoing the source); the container
stayed `running` with restart count 0; and both the Tier 1 and Tier 2 heartbeats
kept advancing beside it. The scratch script was then removed and the next boot
logged no script error at all.

One detail if you go looking for the message: the parser rejects the unknown
word itself, so what you see is a parse error quoting your line rather than a
tidy "unknown command" sentence. The wording varies; the outcome — script
discarded, server up — is the part to rely on.

## Honest limits

Five, and the first two matter most.

**`synced_at` means observed, not requested.** `chrif_save()` does not write
MySQL. It queues packet `0x2b01` to the char server and returns; the char server
commits afterwards. So the hook returning 1 means "handed over", never
"persisted". The script therefore re-reads the character's stored zeny and
compares it against live memory, and writes `synced_at` only when they match.
`synced_at` is the timestamp of a successful comparison.

**`synced_at` is evidence about the `char` row, not about inventory.** Inventory
travels in the same `chrif_save()` call, but it goes out through a separate
`intif_storage_save` *before* the `0x2b01` packet — a landed zeny therefore says
nothing about a landed inventory. Nothing observes an inventory landing, so
nothing claims one: there is no `synced_at` on the inventory response, and the
API's OpenAPI document says so rather than leaving a reader to assume.

**A first `sync_character` may legitimately fail.** Because the commit is
asynchronous, the read-back can run before the write lands, and the row is then
stamped `failed` with `flush queued but not yet persisted - retry`. That is not
an error; it is the honest answer to "confirm this is flushed", and retrying
costs one second. Treat `failed` with that exact message as "try again", not as
"the sync is broken".

**Storage and character variables are only written when already dirty.** The
hook passes `CSAVE_INVENTORY | CSAVE_CART` and nothing else. It does not mark
anything dirty that was not already dirty, so a `sync_character` is not a
general "write everything now" button.

**One field is not the whole row.** The read-back compares zeny, because it is
one integer, it is the field the staleness defect was measured on, and the
script already holds it. The rest of `mmo_charstatus` rides the same packet and
is covered by the same commit, but is not individually re-read.

## What it does

The script polls `ro_admin_commands` once a second, claims one pending
`sync_character` row by compare-and-swap, and then:

1. resolves `account_id` from `char_id`; refuses `no such character` if absent;
2. refuses `character is not online` if `isloggedin()` says the map server is
   not holding the character — nothing to flush, and the stored row is already
   authoritative;
3. `attachrid(account_id, false)` — the `false` is the force argument and is
   load-bearing: the default is true, which would swap the script state out from
   under a player mid-conversation with another NPC. A busy player fails the row
   cleanly with `could not attach - player is busy in a script or offline`;
4. confirms `getcharid(0)` really is the requested character, because `attachrid`
   can resolve a session that has already gone away;
5. records live `Zeny`, calls `ro_admin_sync()`, and detaches;
6. re-reads stored zeny and compares; writes `ro_admin_sync.synced_at` only on a
   match.

It writes its heartbeat **first and unconditionally**, so one failing row never
looks like an uninstalled tier.

### Safety of flushing a connected player

`chrif_save`'s logout machinery is gated on `CSAVE_QUITTING`, which is
`CSAVE_QUIT|CSAVE_CHANGE_MAPSERV|CSAVE_AUTOTRADE` = `0x07`. The hook passes
`CSAVE_INVENTORY|CSAVE_CART` = `0x18`, and `0x18 & 0x07 == 0` — so neither the
`chrif_auth_logout` branch nor a non-zero "quitting" byte is reached. What is
left is exactly the persistence path. **Do not add a bit to that call without
redoing that arithmetic.**

The hook also uses `map_id2sd` rather than `script_rid2sd`. On failure
`script_rid2sd` sets `st->state = END`, which stops the calling script dead —
the queue consumer would die silently the first time it synced a character who
had just logged out. `map_id2sd` is the non-fatal lookup; rAthena's own
`buildin_playerattached` uses it for the same reason.

The line references in both `.inc` files were read against **rAthena commit
`ad04a42` (2025-08-04)**. They will drift; rAthena is a live project and line
numbers are not stable addresses. Re-derive rather than assume.

## Throughput

One `sync_character` per second, upper bound, for the same reason as Tier 1: the
script handles one row per tick and restarts its timer at the *end* of the body.
Tier 2 is for confirming a character before you read or act on them, not for
sweeping a population.

## Uninstall

Any of these is enough, and they degrade in this order:

**Stop consuming syncs** — remove the line from `npc/scripts_custom.conf` and
`@reloadscript`. The heartbeat stops, the API reports Tier 2 unavailable and
names the install step, and `sync_character` goes back to 409. Tier 1 is
unaffected.

**Remove the compiled hook** — restore the two `src/custom/*.inc` stubs and
rebuild. Do this *after* unloading the script, or the script will fail to parse
on the next reload (harmless, and logged, but there is no reason to see it).

**Drop the tables** — only if you want the history gone:

    DROP TABLE ro_admin_sync;
    DROP TABLE ro_admin_tier2;

Nothing else in your database was touched. `ro_admin_commands` belongs to Tier 1
and stays. The API drops back to Tier 1 and reports it.

Dropping the tables while the script is still loaded makes it log a `query_sql`
failure every tick — noisy, harmless, and it recovers by itself when the tables
come back.

## Upgrading

The script declares a version and the API refuses to report Tier 2 available
against a version it does not expect, so copying a new release and forgetting to
`@reloadscript` tells you rather than failing strangely later.

Two things to redo on **every** rAthena pull, because they live in files
upstream owns or that a rebuild replaces:

1. the two `src/custom/*.inc` pastes, and the rebuild;
2. the `npc/scripts_custom.conf` line.

And on every ro-admin upgrade, copy **both** overlay artifacts, not just this
one. The claim filters are a matched pair.
