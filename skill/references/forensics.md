# Forensics: what happened, and how to read a log honestly

Read this when the question is about what happened — GM commands, zeny
movement, item history, or a character's timeline. The rules in `SKILL.md`
apply here unchanged; these are the ones specific to the log surface.

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
