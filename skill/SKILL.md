---
name: ro-admin
description: Use when investigating or administering an rAthena Ragnarok Online server through a ro-admin API — answering questions about what happened to a character, auditing GM commands, tracing zeny or item history, looking up an account or character, reading a character's inventory, searching the server's item database or reading what an item does, listing the server's maps or reading a map's dimensions and walkable geometry, checking which install tiers a server has, or granting an item or adjusting zeny through the Tier 1 overlay. Triggers on questions like "what happened to this character", "who gave that item", "show me the GM commands", "trace this player's zeny", "look up this account", "which characters does this account have", "what is in their inventory", "how much zeny does this character have", "find the item called X", "what is item 501", "what does this item do", "which items are cards", "which maps does this server have", "how big is prontera", "is this coordinate walkable", "find the map called X", "give this player an item", "refund their zeny", or any forensic question about an RO server.
---

# Administering an rAthena server through ro-admin

`ro-admin` is an administration API over a live rAthena server. Everything in Tier 0
reads. A server that also has the **Tier 1 overlay** installed accepts two write
actions, covered in `references/tier1.md`. **The server holds no AI credentials and
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
`logs.read` also covers the three item endpoints — search, types and detail — so
resolving an item takes no extra scope. `system.read` likewise covers the four map
endpoints as well as `system/capabilities`, so a question about map geometry needs
nothing you were not already asking for. `accounts.read` and `characters.read` add the
account and character reads described in `references/entities.md`; ask for them
only when the question is about who an account is or what a character currently
holds. `commands.read` lets you check the outcome of a queued action;
`commands.write` lets you queue one and is Admin-level (99). Do not ask for
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

## Never report an outcome you have not observed

This is the rule that holds on every surface, and it is why this project exists.
Report the field the server sent, in the words the response supports. Do not
re-derive a value the server already gave you, do not translate an id from
memory, and do not report a state nobody observed — the predecessor tool reported
outcomes it had not seen, and it was wrong often enough to be the reason for this
one.

It takes three shapes, each worked through in the reference for its surface:

- **A queued write is not an applied write.** A `202` means a row reached a
  queue. Only a command row that reads `executed` licenses "the change was
  applied" — see `references/tier1.md`.
- **A stored value is not necessarily a current one.** Character and inventory
  rows are mirrors the map server flushes on a timer, and they carry a `stale`
  flag you are obliged to relay — see `references/entities.md`.
- **Absence of a record is not proof of absence.** An empty search, a log table
  this server does not have, a 404 — each means something narrower than "it did
  not happen", and the reference for the surface says what.

Where a response hands you a `detail` or a `reason` naming the operator's next
step — a 503 from the map endpoints, a 409 from Tier 1, `system/capabilities`
reporting a tier as unavailable — **relay it verbatim and stop.** It is the only
thing you know, and it is written to be relayed.

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
- **Never carry game data of your own.** Not item ids, not map names, not
  dimensions. Every one of them is the operator's, it is on their server, and
  there is an endpoint that returns it. A remembered value is wrong for exactly
  the server that has customised it. And map *artwork* is not the operator's to
  give either — it is Gravity's, it lives in the client's GRF archives, and no
  part of this API touches it.

## Which reference to read

Read the entry above on every task. Then read only what the question needs.

| Read | When the question is about |
|---|---|
| `references/forensics.md` | what happened — GM commands, zeny movement, item history, a character's timeline |
| `references/entities.md` | who and what — accounts, characters, inventories, and why a value may be stale |
| `references/items.md` | finding an item, or reading what one does |
| `references/maps.md` | the map list, dimensions, or whether a coordinate is walkable |
| `references/tier1.md` | changing the game — granting an item or adjusting zeny |

**Those paths are relative to this file, not to your working directory.** This
file is `<skill>/SKILL.md` and the references sit in `<skill>/references/`; open
them alongside it rather than resolving `references/` against wherever the shell
happens to be.

If a question spans two, read both. If none of them fits, `discover` first: the
API may have grown a surface this skill does not describe yet, and inventing
one is worse than saying so.
