"""Character reads, and the staleness contract.

The second half of this file is the important half. char.zeny is a stale
mirror while a character is online -- measured during the Tier 1 work, where
an in-game +777 change did not appear in the table at t+20s but read exactly
+777 after logout. An API that hands that number over unlabelled is inviting
the reader to treat a five-minute-old value as live.
"""
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    CHAR_WITH_ECONOMY, apply_test_env,
)
from ro_admin.config import Settings
from ro_admin.db import Database


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


def _token(client, userid, password):
    r = client.post("/api/v1/auth/login", json={"userid": userid, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.fixture()
def headers(client):
    return {"Authorization": f"Bearer {_token(client, ADMIN_USER, ADMIN_PASSWORD)}"}


@pytest.mark.integration
def test_listing_rejects_anonymous(client):
    assert client.get("/api/v1/characters").status_code == 401


@pytest.mark.integration
def test_listing_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get("/api/v1/characters", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_listing_returns_characters(client, headers):
    items = client.get("/api/v1/characters", headers=headers).json()["items"]
    assert items
    assert {"char_id", "account_id", "name", "base_level", "zeny", "online"} <= set(items[0])


@pytest.mark.integration
def test_name_filter_narrows_results(client, headers):
    listed = client.get("/api/v1/characters", params={"limit": 1}, headers=headers).json()
    name = listed["items"][0]["name"]
    filtered = client.get(
        "/api/v1/characters", params={"name": name}, headers=headers
    ).json()["items"]
    assert filtered
    assert {e["name"] for e in filtered} == {name}


@pytest.mark.integration
def test_online_filter_narrows_results(client, headers):
    items = client.get(
        "/api/v1/characters", params={"online": False}, headers=headers
    ).json()["items"]
    assert items
    assert all(e["online"] is False for e in items)


@pytest.mark.integration
def test_single_character_is_returned(client, headers):
    r = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers)
    assert r.status_code == 200
    assert r.json()["char_id"] == CHAR_WITH_ECONOMY


@pytest.mark.integration
def test_unknown_character_is_404(client, headers):
    """Asserts on the detail message, not just the status: a route that does
    not exist ALSO returns 404, so a bare status check here would pass whether
    or not the endpoint was ever built."""
    r = client.get("/api/v1/characters/999999999", headers=headers)
    assert r.status_code == 404
    assert "999999999" in r.json()["detail"], (
        "got FastAPI's generic 404 -- the route is missing, not the character"
    )


@pytest.mark.integration
def test_class_is_a_bare_id_and_no_name_is_invented(client, headers):
    """Checked, not assumed: rAthena has NO job table in SQL. It keeps job data
    in YAML on the server's filesystem, which this API deliberately never
    reads. So Tier 0 cannot resolve a job name, and the honest thing is to
    return the id and say so -- not to ship a lookup table (which
    scripts/check_no_game_data.py exists to prevent) and not to invent a label.

    Contrast items, where item_db IS a SQL table and the API therefore owes the
    caller the name. The rule is the same; only the server's answer differs.
    """
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers).json()
    assert isinstance(body["class"], int)
    assert "job_name" not in body, (
        "a job name here could only have been fabricated or bundled"
    )


# --- the staleness contract --------------------------------------------------


@pytest.mark.integration
def test_an_offline_character_is_not_marked_stale(client, headers):
    """char is authoritative once the map server has flushed on logout."""
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers).json()
    assert body["online"] is False
    assert body["stale"] is False
    assert body["stale_fields"] == []


@pytest.mark.integration
def test_the_response_always_carries_the_staleness_fields(client, headers):
    """Present on every response, not only when true. A field that appears only
    sometimes trains callers to ignore it."""
    for entry in client.get("/api/v1/characters", headers=headers).json()["items"]:
        assert "stale" in entry and "stale_fields" in entry


@pytest.mark.integration
def test_a_stale_character_still_reports_its_values(client, headers):
    """Labelled, never withheld. A null would be indistinguishable from zero,
    and an operator asking a character's zeny deserves the best answer
    available even when it is a few minutes old."""
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers).json()
    assert body["zeny"] is not None


@pytest.mark.integration
def test_select_characters_reports_the_latest_executed_write(client, headers):
    """Fix A's evidence, proven against the real SQL rather than a mock: the
    correlated subquery _select_characters adds must actually find a write.

    _to_character's own rule (synced_at must be strictly newer than
    written_at to stay fresh) is unit-tested against a fake row in
    test_staleness.py with no database; this is the other half -- that the
    query really produces a written_at a caller can hand it.

    Pinned to the exact row inserted, not just "is not None": CHAR_WITH_ECONOMY
    already carries well over a thousand old executed/failed rows from earlier
    test runs (see overlay/README.md's note on the lab's accumulated traffic),
    so a bare not-None check would pass even if the subquery returned some
    OLDER write and silently ignored this one. Read back by id, off MySQL's
    own clock rather than trusting the NOW() this test's INSERT used, so the
    comparison is honest about which clock produced the value.

    An obviously synthetic requested_by, deleted in `finally`. Never a
    'pending' row: the live overlay would execute it in the game.
    """
    from ro_admin.overlay import COMMAND_TABLE
    from ro_admin.routers.characters import _select_characters

    db = Database(Settings())
    command_id = db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, "
        "created_at, finished_at) "
        "VALUES (%s, 'adjust_zeny', -1, 0, 'executed', 'test-written-at', "
        "NOW(), NOW())",
        (CHAR_WITH_ECONOMY,),
    )
    try:
        expected = db.query(
            f"SELECT finished_at FROM {COMMAND_TABLE} WHERE id = %s",
            (command_id,),
        )[0]["finished_at"]

        rows = _select_characters(db, "WHERE char_id = %s", (CHAR_WITH_ECONOMY,))
        assert rows, "the synthetic character did not come back"
        row = rows[0]
        assert row.get("written_at") == expected, (
            f"expected the newly inserted row's finished_at {expected!r}, "
            f"got {row.get('written_at')!r} -- the subquery is not finding "
            f"the LATEST write for this character"
        )
    finally:
        db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (command_id,))


@pytest.mark.integration
def test_sync_rows_are_excluded_but_a_still_processing_write_counts(client, headers):
    """The two load-bearing exclusions in written_at_sql, proven together
    against the real query:

    1. sync_character rows must NEVER move written_at, at any status -- a
       sync is the evidence itself, not a write to check the evidence
       against. Without this exclusion, every successful sync would
       invalidate its own freshness the instant it landed (synced_at and
       written_at would tie, and the comparison in _to_character is a strict
       `>` precisely because a tie is ambiguous) -- Tier 2 would never be
       able to report a character fresh at all.
    2. A 'processing' row -- claimed, but not yet finished -- must count.
       Both overlay scripts stamp claimed_at strictly BEFORE touching the
       game (see written_at_sql's docstring), so an in-flight write is
       exactly the case this field exists to catch; waiting for it to reach
       a terminal status would be too late.

    Measured as a delta against a captured baseline, not an absolute value,
    because CHAR_WITH_ECONOMY already carries a long non-sync write history
    from earlier test runs -- the baseline stands in for "whatever the
    correct answer already was", and only the effect of these specific new
    rows is asserted.
    """
    from ro_admin.overlay import COMMAND_TABLE
    from ro_admin.routers.characters import _select_characters

    db = Database(Settings())

    def written_at():
        rows = _select_characters(db, "WHERE char_id = %s", (CHAR_WITH_ECONOMY,))
        return rows[0]["written_at"]

    baseline = written_at()

    inserted_ids = []
    try:
        for status in ("executed", "failed"):
            inserted_ids.append(db.execute(
                f"INSERT INTO {COMMAND_TABLE} "
                "(char_id, action, arg_int, arg_int2, status, requested_by, "
                "created_at, finished_at) "
                f"VALUES (%s, 'sync_character', 0, 0, '{status}', "
                "'test-sync-exclusion', NOW(), NOW())",
                (CHAR_WITH_ECONOMY,),
            ))
        assert written_at() == baseline, (
            "a sync_character row moved written_at -- the action <> "
            "'sync_character' exclusion in written_at_sql is broken"
        )

        processing_id = db.execute(
            f"INSERT INTO {COMMAND_TABLE} "
            "(char_id, action, arg_int, arg_int2, status, requested_by, "
            "created_at, claimed_by, claimed_at) "
            "VALUES (%s, 'adjust_zeny', -1, 0, 'processing', "
            "'test-processing-write', NOW(), 999999999, NOW())",
            (CHAR_WITH_ECONOMY,),
        )
        inserted_ids.append(processing_id)

        expected = db.query(
            f"SELECT claimed_at FROM {COMMAND_TABLE} WHERE id = %s",
            (processing_id,),
        )[0]["claimed_at"]
        assert written_at() == expected, (
            "a still-'processing' row did not move written_at -- either "
            "COALESCE(finished_at, claimed_at) is not applied, or the "
            "status IN list dropped 'processing'"
        )
    finally:
        for row_id in inserted_ids:
            db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (row_id,))


@pytest.mark.integration
def test_every_character_endpoint_passes_written_at_to__to_character(
    client, headers, monkeypatch
):
    """Endpoint wiring, checked directly rather than inferred from `stale`:
    every lab character is offline, and offline is never stale regardless of
    written_at, so no endpoint's JSON response can distinguish "written_at
    was wired up" from "it was silently dropped". This patches
    _to_character itself and inspects the keyword actually arriving.

    Two import sites, both patched: accounts.py does
    `from ro_admin.routers.characters import ..., _to_character`, which binds
    its OWN name in accounts.py's namespace -- patching
    characters._to_character alone would leave account_characters still
    calling the original function and this test would pass for the wrong
    endpoints even if accounts.py dropped the keyword.
    """
    import ro_admin.routers.accounts as accounts_module
    import ro_admin.routers.characters as characters_module

    calls: list[dict] = []
    real = characters_module._to_character

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(characters_module, "_to_character", spy)
    monkeypatch.setattr(accounts_module, "_to_character", spy)

    one = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}", headers=headers)
    assert one.status_code == 200, one.text
    account_id = one.json()["account_id"]

    list_resp = client.get("/api/v1/characters", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    account_resp = client.get(
        f"/api/v1/accounts/{account_id}/characters", headers=headers
    )
    assert account_resp.status_code == 200, account_resp.text

    assert calls, "no _to_character calls were captured -- did every endpoint 404?"
    for kwargs in calls:
        assert "written_at" in kwargs, (
            f"a call to _to_character dropped the written_at keyword: {kwargs}"
        )


# --- inventory ---------------------------------------------------------------


@pytest.mark.integration
def test_inventory_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}/inventory",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.integration
def test_inventory_of_unknown_character_is_404(client, headers):
    """Not an empty item list. "This character owns nothing" and "there is no
    such character" are different answers and only one of them is true.

    Checks the detail message for the same reason as above -- a missing route
    would otherwise satisfy a bare 404 assertion.
    """
    r = client.get("/api/v1/characters/999999999/inventory", headers=headers)
    assert r.status_code == 404
    assert "999999999" in r.json()["detail"], (
        "got FastAPI's generic 404 -- the route is missing, not the character"
    )


@pytest.mark.integration
def test_inventory_carries_item_names(client, headers):
    """The rule that produced the items endpoint, applied here. Returning bare
    nameids would push every consumer toward keeping its own id-to-name map --
    exactly the defect scripts/check_no_game_data.py exists to prevent.

    Character 200000 is seeded with items in the reference lab, and the Tier 1
    work granted it Jellopy (909) -- the item the predecessor's panel rendered
    as "Unknown Item" because it was outside that component's hardcoded list.
    """
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}/inventory",
                      headers=headers).json()
    assert body["items"], "the seeded demo character holds items"
    for entry in body["items"]:
        assert entry["item_name"], f"item {entry['item_id']} resolved to an empty name"
        assert entry["item_name"] != str(entry["item_id"])


@pytest.mark.integration
def test_inventory_reports_its_own_staleness(client, headers):
    """`inventory` is flushed on the same schedule as `char` -- measured during
    the Tier 1 work, where a grant to a logged-in character was absent from the
    table at +5s and present after logout. A caller checking whether a grant
    landed must be told the table may not know yet."""
    body = client.get(f"/api/v1/characters/{CHAR_WITH_ECONOMY}/inventory",
                      headers=headers).json()
    assert "stale" in body
    assert body["stale"] is False, "character 200000 is expected offline"


# --- prefix search -----------------------------------------------------------
#
# Prefix and not substring, because only a prefix can be served by the B-tree
# index on `char`.`name`. Measured on 1,000,000 characters: a prefix is a
# range scan returning in 0.1 ms, a substring is a full index scan taking
# 1,452 ms. See the web UI spec. These tests derive their inputs from the
# lab's own characters, so they hold on any install with a few characters in
# it.


def _everyone(client, headers):
    return client.get(
        "/api/v1/characters", params={"limit": 500}, headers=headers
    ).json()["items"]


def _names(client, headers, **params):
    r = client.get("/api/v1/characters", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [c["name"] for c in r.json()["items"]]


@pytest.mark.integration
def test_name_prefix_matches_from_the_start(client, headers):
    target = next(c["name"] for c in _everyone(client, headers) if len(c["name"]) >= 3)
    # limit=500: on a larger install the target might not land on the
    # default first page of 50, and this test is about matching, not paging.
    found = _names(client, headers, name_prefix=target[:2], limit=500)
    assert target in found
    assert all(n.casefold().startswith(target[:2].casefold()) for n in found)


@pytest.mark.integration
def test_name_prefix_is_not_a_substring_search(client, headers):
    target = next(
        c["name"] for c in _everyone(client, headers)
        if len(c["name"]) >= 3
        and not c["name"].casefold().startswith(c["name"][1:].casefold())
    )
    assert target not in _names(client, headers, name_prefix=target[1:])


@pytest.mark.integration
def test_name_prefix_treats_underscore_literally(client, headers):
    """`_` is LIKE's any-one-character wildcard, bound parameter or not.
    Unescaped, `K_` would match `Kami`."""
    target = next(
        c["name"] for c in _everyone(client, headers)
        if len(c["name"]) >= 2 and c["name"][1] != "_"
    )
    assert target not in _names(client, headers, name_prefix=target[0] + "_")


@pytest.mark.integration
def test_prefix_results_are_ordered_by_name(client, headers):
    """Ordered by the searched column, so the index returns rows already sorted
    and LIMIT stops early. Ordering by char_id would force a sort of every
    match."""
    everyone = _everyone(client, headers)
    letter = Counter(c["name"][0].casefold() for c in everyone).most_common(1)[0][0]
    found = _names(client, headers, name_prefix=letter)
    # Compared only among names that are ASCII letters and digits: under
    # utf8mb4_0900_ai_ci, ASCII digits sort before letters exactly as they do
    # in Python, so they're safe to include. Punctuation, symbols and
    # accented letters are the ones MySQL's collation and str.casefold() are
    # not guaranteed to agree on, so a name outside this set could
    # legitimately sort differently under the two orderings without the
    # endpoint being wrong. Filtering doesn't reorder anything, so a
    # subsequence of a correctly-sorted response is still sorted.
    comparable = [n for n in found if n.isascii() and n.isalnum()]
    if len(comparable) < 2:
        pytest.skip("the lab needs two ASCII alphanumeric names sharing a first letter")
    assert comparable == sorted(comparable, key=str.casefold)


@pytest.mark.integration
def test_has_more_reports_a_further_page_without_a_count(client, headers):
    first = client.get("/api/v1/characters", params={"limit": 1}, headers=headers).json()
    assert len(first["items"]) == 1
    assert first["has_more"] is True
    everyone = client.get("/api/v1/characters", params={"limit": 500}, headers=headers).json()
    if everyone["has_more"]:
        pytest.skip("the lab has 500 or more characters; this assumes it fits one page")
    last = client.get(
        "/api/v1/characters",
        params={"limit": 1, "offset": len(everyone["items"]) - 1},
        headers=headers,
    ).json()
    assert len(last["items"]) == 1
    assert last["has_more"] is False
    assert "total" not in first


@pytest.mark.integration
def test_name_prefix_combines_with_online(client, headers):
    target = next(c for c in _everyone(client, headers) if not c["online"])
    r = client.get(
        "/api/v1/characters",
        # limit=500: the target may not be on the default first page.
        params={"name_prefix": target["name"][:1], "online": False, "limit": 500},
        headers=headers,
    )
    items = r.json()["items"]
    assert target["char_id"] in {c["char_id"] for c in items}
    assert all(c["online"] is False for c in items)


@pytest.mark.integration
@pytest.mark.parametrize("value", ["", "x" * 31])
def test_name_prefix_bounds_are_enforced(client, headers, value):
    """30 is `char`.`name`'s width; a longer prefix cannot match anything."""
    r = client.get("/api/v1/characters", params={"name_prefix": value}, headers=headers)
    assert r.status_code == 422


@pytest.mark.integration
def test_name_prefix_is_served_by_the_name_index(client, headers, monkeypatch):
    """The guarantee the spec makes, pinned against the query this endpoint
    ACTUALLY sends: the statement is captured on its way to MySQL, then
    EXPLAINed.

    Only successful statements are recorded, so on an install without Tier 2
    the failed LEFT JOIN attempt is skipped and the fallback is checked.

    The prefix is the rarest leading letter among the lab's own names, not a
    hardcoded one. On a table this small, MySQL's cost model abandons the
    index for a common letter -- the account equivalent of this test found
    that "d" matches 15 of the lab's 21 accounts and plans as a full scan
    with a filesort -- while a rare letter still ranges over the index.
    """
    everyone = _everyone(client, headers)
    counts = Counter(c["name"][0].casefold() for c in everyone)
    letter = min(counts, key=counts.get)

    seen = []
    real_query = Database.query

    def spy(self, sql, params=None):
        rows = real_query(self, sql, params)
        seen.append((sql, params))
        return rows

    monkeypatch.setattr(Database, "query", spy)
    r = client.get("/api/v1/characters", params={"name_prefix": letter}, headers=headers)
    assert r.status_code == 200
    monkeypatch.setattr(Database, "query", real_query)

    sql, params = next((s, p) for s, p in reversed(seen) if "FROM `char`" in s)
    plan = Database(Settings()).query("EXPLAIN " + sql, params)
    row = next(p for p in plan if p["table"] == "char")
    assert row["key"] == "name_key", plan
    assert row["type"] == "range", plan
    # Pins ORDER BY-by-the-searched-column, not just the index use: ordering
    # by char_id instead would still find name_key/range for the WHERE
    # clause, but would need a filesort (or a temp table) to satisfy an
    # ORDER BY on a different column afterwards. This is the assertion that
    # actually catches that regression -- the key/type pair alone does not.
    extra = row["Extra"] or ""
    assert "Using filesort" not in extra, plan
    assert "Using temporary" not in extra, plan
    if "LEFT JOIN" in sql:
        # A JOIN does not preserve the derived table's row order, so
        # _select_characters repeats the ORDER BY outside it -- pinned here
        # since only the joined form (Tier 2 present) has that outer query.
        assert " ".join(sql.split()).endswith("ORDER BY name"), sql


@pytest.mark.integration
def test_default_listing_pins_the_outer_order_by_char_id(client, headers, monkeypatch):
    """The same outer-ORDER-BY guarantee as
    test_name_prefix_is_served_by_the_name_index above, for the OTHER order
    _select_characters can be asked to repeat: the default, unfiltered
    listing orders by char_id rather than name. A regression scoped only to
    the char_id path -- as opposed to name -- would slip past that test
    alone, since it captures a name_prefix query."""
    seen = []
    real_query = Database.query

    def spy(self, sql, params=None):
        rows = real_query(self, sql, params)
        seen.append((sql, params))
        return rows

    monkeypatch.setattr(Database, "query", spy)
    r = client.get("/api/v1/characters", headers=headers)
    assert r.status_code == 200
    monkeypatch.setattr(Database, "query", real_query)

    sql, _ = next((s, p) for s, p in reversed(seen) if "FROM `char`" in s)
    if "LEFT JOIN" in sql:
        # As above: only the joined form (Tier 2 present) has an outer query
        # to repeat the ORDER BY in.
        assert " ".join(sql.split()).endswith("ORDER BY char_id"), sql
