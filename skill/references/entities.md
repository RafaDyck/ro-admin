# Accounts and characters, and what a stored value is worth

Read this when the question is about who and what — accounts, characters,
inventories, and why a value the server hands you may already be out of date.

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
  need — zeny logging ships off, see `references/tier1.md`.

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

