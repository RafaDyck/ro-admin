# Forensics: what happened, and how to read a log honestly

Read this when the question is about what happened — GM commands, zeny
movement, item history, or a character's timeline. The rules in `SKILL.md`
apply here unchanged; these are the ones specific to the log surface.

## Answering the question people actually ask

"What happened to this character?" is one call:

```
python -m ro_admin.cli get logs/timeline char_id=150002
```

It merges GM commands, zeny changes, item transactions, and — where Tier 1 or Tier 2
is installed — this character's own rows from the action queue into one chronological
stream, each entry carrying a readable `summary` plus a `detail` object.

Observed on the reference lab's character 150002, `limit=3` (its zeny and queue history
crowd out the item entry below at this limit; both are real, separate observations, not
one screenshot):

```
2026-09-26T20:14:52  [zeny]    zeny +1289 via admin command on geffen
2026-09-26T20:14:52  [queued]  adjust_zeny +1289 requested by admin1234: executed
2026-09-26T20:14:47  [zeny]    zeny +511 via admin command on geffen
```

And, further back for the same character:

```
2026-08-22T03:37:56  [item]    Red Potion x3 via npc script on geffen
```

**The `queued` entries matter more than they look.** On a stock rAthena, `adjust_zeny`
leaves no `zenylog` row at all — `log_zeny` ships off (see `references/tier1.md`) — so
the queue is the only durable record that request ever happened. If you are asked
whether a write was already sent before sending it again, this is what you check, and
`kind: "zeny"` alone is not enough to rule it out. `sync_character` rows appear here too,
same as any other queued action.

Check `sources` on the response before concluding the queue was consulted: it lists only
the tables actually read, so `"ro_admin_commands"` is present only when Tier 1 or Tier 2
is installed. Absent from `sources`, the honest reading is "this server has no queue to
check", not "nothing was queued".

Prefer the timeline for open-ended investigation, and the per-source endpoints
(`logs/zeny`, `logs/items`, `logs/commands`, `commands/{id}`) when you already know what
you are looking for or need filters they offer.

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
