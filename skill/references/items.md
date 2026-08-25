# Items: finding one, and reading what it does

Read this when the question is about finding an item or reading what one does.


Three Tier 0 reads over the operator's own `item_db`. All three are gated by
`logs.read` and need no other scope — observed: a token scoped to `logs.read`
alone answers all three with 200, and a token without it is refused
`403 {"detail":"not permitted: logs.read"}`.

| Endpoint | Filters |
|---|---|
| `items` | `q` (substring), `type`, `subtype`, `slots`, `limit`, `offset` |
| `items/types` | — |
| `items/{item_id}` | — |

### Never guess an item id, and never carry a list of them

This is the rule already stated for log responses in `references/forensics.md`,
extended to the surface that can now answer it.

- If someone names an item, **search for it**. Do not recall its id from memory.
- If a response hands you an id you cannot name — a `logs/items` row, an
  inventory entry, a queued command — **call `items/{item_id}`**.
- Do not keep an id-to-name table of your own. `item_db` on this server has
  28,525 rows, it belongs to the operator, and yours will be wrong for exactly
  the id that matters.

A wrong id here is not a cosmetic error. `give_item` with the wrong id puts the
wrong item into a live game.

### Searching

```
python -m ro_admin.cli get items q=potion limit=2
```

```json
{"items": [{"id": 501, "name_english": "Red Potion", "name_aegis": "Red_Potion",
            "type": "healing", "subtype": null, "slots": null, "weight": 70,
            "price_buy": 10, "price_sell": null, "equip_level_min": null},
           {"id": 502, "name_english": "Orange Potion",
            "name_aegis": "Orange_Potion", "type": "healing", "subtype": null,
            "slots": null, "weight": 100, "price_buy": 50, "price_sell": null,
            "equip_level_min": null}],
 "total": 332, "limit": 2, "offset": 0}
```

Filters combine with AND. `type`, `subtype` and `slots` are exact matches; only
`q` is a substring. Three bounds are enforced with a **422** before any query runs,
all observed: `q` must be 1–100 characters (`q=` empty is 422), `slots` must be 0–4
(`slots=5` is 422), and `limit` must be 1–200. A 422 is the server telling you the
request was malformed — read `detail`, fix the parameter, and do not retry it
unchanged.

#### `total` is the match count, not the length of this page

332 items matched `potion`; two came back because `limit=2` asked for two. Quote
`total` when someone asks how many there are, and say separately how many you
actually looked at.

**A page is capped at 200.** `items?limit=201` is rejected with **422** before
any query runs. So `total: 332` alongside `limit: 200` means a second page
exists at `offset=200` — not that you have seen everything.

#### A result that does not contain your search term is still a real match

Search covers **three** columns: `name_english`, `name_aegis` and `alias_name`.
A match can come from any of them, so a result need not visibly contain what you
typed. Observed, inside that same `q=potion` result set:

```json
{"id": 11621, "name_english": "Red Syrup", "name_aegis": "High_RedPotion",
 "type": "healing", "subtype": null, "slots": null, "weight": 70,
 "price_buy": 800, "price_sell": null, "equip_level_min": 60}
```

"Red Syrup" matched `potion` on its **aegis name**, `High_RedPotion`. So did
1088 "Morocc Solution" (`Morocc_Potion`), 1089 "Payon Solution"
(`Payon_Potion`) and 7308 "Witch's Tonic" (`Witch's_Potion`).

**These are correct results. Do not discard them, do not apologise for them, and
do not "correct" the list down to the rows whose English name contains the
term.** The server matched on a name it holds and you do not. If someone asks
why a row is in the list, the answer is already in the response: say which name
it matched on — `name_aegis` is in every summary, `alias_name` is on the detail.

#### Prefer one narrow query to several broad ones

There is no index on `name_english`, so a search is a **full scan of all 28,525
rows**, and each request runs two of them — one `COUNT(*)` for `total`, one for
the page. Put your filters in a single call instead of issuing several broad
ones and intersecting the results yourself:

```
python -m ro_admin.cli get items q=potion type=healing limit=3
```

That returns `"total": 54`, against 332 for `q=potion` alone.

#### `%` and `_` are literal characters, not wildcards

LIKE wildcards are escaped before the query runs, so `q` means exactly what it
says. Measured on this server:

| `q` | `total` |
|---|---|
| *(omitted)* | 28525 |
| `%` | 57 |
| `_` | 27291 |
| `50%` | 3 |

`q=%` finds the 57 items with a percent sign in a name — 4760 "MATK+1%", 4766
"ATK+2%" and so on — not the whole table. `q=50%` finds "Fixed Cast Time - 50%",
"Ignored Def 50%" and "Ignored Mdef 50%", not every item whose name begins "50".
You cannot glob here, and you do not need to.

### `items/types` — take the type list from the server, not from memory

```
python -m ro_admin.cli get items/types
```

```json
{"types": [{"type": "armor", "count": 8700}, {"type": "card", "count": 5504},
           {"type": "etc", "count": 4041}, {"type": "weapon", "count": 2744},
           {"type": "usable", "count": 2708}, {"type": "cash", "count": 2371},
           {"type": "shadowgear", "count": 980},
           {"type": "delayconsume", "count": 704},
           {"type": "healing", "count": 461}, {"type": "petegg", "count": 144},
           {"type": "ammo", "count": 123}, {"type": "petarmor", "count": 45}]}
```

Those are the types **this** server has, counted by a `GROUP BY` over its own
table. Another operator's list will differ, and a server with custom types gets
its custom types. Call this before filtering by `type`, rather than guessing a
name, getting zero rows, and reading that as "there are none".

### `items/{item_id}` — the whole row, including the script

```
python -m ro_admin.cli get items/501
```

```json
{"item_id": 501, "name": "Red Potion", "id": 501, "name_english": "Red Potion",
 "name_aegis": "Red_Potion", "alias_name": null, "type": "healing",
 "subtype": null, "slots": null, "weight": 70, "price_buy": 10,
 "price_sell": null, "attack": null, "defense": null, "range": null,
 "weapon_level": null, "armor_level": null, "equip_level_min": null,
 "equip_level_max": null, "refineable": 0, "view": null,
 "script": "itemheal rand(45,65),0;\n", "equip_script": null,
 "unequip_script": null}
```

**`item_id`/`name` and `id`/`name_english` are the same two values twice.** The
first pair is the shape this endpoint originally published, kept so callers
written against it keep working; the second is what the search endpoint returns,
so one shape serves both. It is one item, not two.

An unknown id is a **404** carrying the id — `{"detail":"no item with id
999999"}` — never a placeholder name. Report "no such item" and stop.

#### `script` is rAthena source, and it answers "what does this item do"

`"itemheal rand(45,65),0;\n"` is item 501's actual behaviour: heal a random 45
to 65 HP and 0 SP. Nothing else in the response says that — not the name, not
the type, not the price.

**Read it before telling anyone what an item is for.** "Red Potion is a healing
item" only repeats the `type` column; "it heals 45–65 HP" is what the server
actually knows. Quote the script, say plainly what it does, and where it uses a
function or constant you are not certain of, say so rather than guessing — a
confidently wrong reading of a script is worse than "the script says this, and I
cannot tell you what that call does".

**`null` means the item has no script at all, and is not the same as `""`.**
Item 909 "Jellopy" returns `"script": null` — a plain trade good with no
behaviour. Do not describe a `null` script as empty or blank, and do not invent
behaviour for such an item from its name.

`equip_script` and `unequip_script` run when equipment is put on and taken off.
Both are `null` on the two items above because neither is equipment.

