"""One chronological view of everything that happened to a character.

FluxCP has a page per log table, which means answering "what happened to this
character?" is a manual cross-reference across several screens. This merges
them, which is the question operators actually ask.
"""
from datetime import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, CHAR_WITH_COMMANDS, CHAR_WITH_ECONOMY,
    PLAYER_PASSWORD, PLAYER_USER, TEST_JWT_SECRET, apply_test_env,
)
from ro_admin.auth import issue_service_token
from ro_admin.config import Settings
from ro_admin.db import Database
from ro_admin.overlay import COMMAND_TABLE
from ro_admin.permissions import Permission


@pytest.fixture()
def client(monkeypatch):
    apply_test_env(monkeypatch)
    from ro_admin.main import app
    return TestClient(app)


def _token(client, userid, password):
    r = client.post("/api/v1/auth/login", json={"userid": userid, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.mark.integration
def test_timeline_merges_multiple_log_sources(client):
    """Character 150002 has a zeny entry and an item entry -- both must appear.

    limit=500, not the 100 default: this character's ro_admin_commands rows
    (Fix B's new source) now compete for the same merged-and-truncated window,
    and 150002 has exactly one picklog row. Specifically 62 rows accumulated
    by this project's own integration-test harness -- 60 adjust_zeny plus 2
    sync_character, all requested_by admin1234 -- not the ~1,200 that belong
    to CHAR_WITH_ECONOMY (200000); confirmed with a GROUP BY against the lab.
    The default limit is a real product decision worth its own test
    elsewhere; this one is about merging sources, so it asks for enough of
    each that none is truncated away by data volume.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150002, "limit": 500},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 2
    assert {"zeny", "item"} <= {e["kind"] for e in items}


@pytest.mark.integration
def test_timeline_entries_carry_a_readable_summary(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150002},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"]
    for entry in items:
        assert {"date", "kind", "summary", "char_id", "detail"} <= set(entry)
        assert entry["summary"].strip(), "every entry needs a human-readable summary"


@pytest.mark.integration
def test_timeline_is_newest_first(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150000, "limit": 20},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"]
    assert len(items) > 1
    dates = [e["date"] for e in items]
    assert dates == sorted(dates, reverse=True), "merged sources must be re-sorted, not concatenated"


@pytest.mark.integration
def test_timeline_includes_command_source(client):
    """Character 150000 has GM command history, so 'command' must appear.

    Asserts membership, not equality. An earlier version asserted the kind set
    was exactly {"command"} -- true when written, and false the moment anything
    touched that character's zeny. A test pinned to a snapshot of mutable world
    data decays into a false alarm and trains you to ignore it.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    items = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150000},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"]
    assert items
    assert "command" in {e["kind"] for e in items}


@pytest.mark.integration
def test_timeline_requires_staff(client):
    token = _token(client, PLAYER_USER, PLAYER_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 150002},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_timeline_for_unknown_character_is_empty_not_an_error(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": 99999999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


# ---------------------------------------------------------------------------
# Fix B: the command queue belongs in History too.
#
# On a stock install log_zeny is 0, so adjust_zeny leaves no zenylog row --
# the very write an operator asks "check History before sending this again"
# about would otherwise be invisible here. See routers/logs.py.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_timeline_includes_a_queued_command(client):
    """An obviously synthetic requested_by, deleted in `finally`. Never a
    'pending' row: the live overlay would execute it in the game."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    db = Database(Settings())
    command_id = db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, "
        "created_at, finished_at, error_message) "
        "VALUES (%s, 'adjust_zeny', -5000, 0, 'failed', 'test-timeline-queue', "
        "NOW(), NOW(), 'character is not online')",
        (CHAR_WITH_ECONOMY,),
    )
    try:
        items = client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_ECONOMY, "limit": 500},
            headers=headers,
        ).json()["items"]
        matches = [e for e in items if e["detail"].get("id") == command_id]
        assert matches, "the synthetic queued row did not appear in the timeline"
        entry = matches[0]
        assert entry["kind"] == "queued"
        assert "adjust_zeny" in entry["summary"]
        assert "-5000" in entry["summary"]
        assert "failed" in entry["summary"]
        assert entry["detail"]["action"] == "adjust_zeny"
        assert entry["detail"]["status"] == "failed"
        assert entry["detail"]["requested_by"] == "test-timeline-queue"
        assert entry["detail"]["delta"] == -5000
    finally:
        db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (command_id,))


@pytest.mark.integration
def test_timeline_sources_lists_the_command_queue(client):
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": CHAR_WITH_ECONOMY},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "ro_admin_commands" in r.json()["sources"]


@pytest.mark.integration
def test_a_queued_give_item_carries_the_resolved_item_name_in_detail(client):
    """The summary has always resolved the name (`give_item Jellopy x3 ...`);
    `detail` did not, which meant a consumer reading the structured field
    instead of parsing prose got only a bare item_id -- the exact habit
    scripts/check_no_game_data.py exists to keep this project from causing in
    its callers. picklog's ItemLogEntry.item_name is the precedent this
    matches."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    db = Database(Settings())
    command_id = db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, "
        "created_at, finished_at) "
        "VALUES (%s, 'give_item', 909, 3, 'executed', 'test-detail-item-name', "
        "NOW(), NOW())",
        (CHAR_WITH_ECONOMY,),
    )
    try:
        items = client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_ECONOMY, "limit": 500},
            headers=headers,
        ).json()["items"]
        matches = [e for e in items if e["detail"].get("id") == command_id]
        assert matches, "the synthetic queued row did not appear in the timeline"
        detail = matches[0]["detail"]
        assert detail["item_id"] == 909
        assert detail.get("item_name"), "detail must carry the resolved name, not just item_id"
        assert detail["item_name"] != "909", "a bare id restated as a string is not a name"
    finally:
        db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (command_id,))


@pytest.mark.integration
def test_a_queued_row_that_finished_later_says_when(client):
    """A row queued at T and executed hours later must not read as if it ran
    at T -- the summary is what an operator skims, and 'requested by X:
    executed' alone implies it happened right then.

    CHAR_WITH_COMMANDS (150000), not CHAR_WITH_ECONOMY: that character's
    >1,200 accumulated ro_admin_commands rows would crowd a deliberately
    old-dated synthetic row out of even a limit=500 response, for the same
    reason test_merged_truncation_ranks_the_newest_row_first_across_sources
    picks it -- see that test's docstring.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    db = Database(Settings())
    created_at = datetime(2020, 1, 1, 0, 0, 0)
    finished_at = datetime(2020, 1, 1, 3, 30, 0)
    command_id = db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, "
        "created_at, finished_at) "
        "VALUES (%s, 'adjust_zeny', 1, 0, 'executed', 'test-finish-time', "
        "%s, %s)",
        (CHAR_WITH_COMMANDS, created_at, finished_at),
    )
    try:
        items = client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_COMMANDS, "limit": 500},
            headers=headers,
        ).json()["items"]
        matches = [e for e in items if e["detail"].get("id") == command_id]
        assert matches, "the synthetic queued row did not appear in the timeline"
        summary = matches[0]["summary"]
        assert "2020-01-01 03:30:00" in summary, (
            f"expected the finish time in the summary, got {summary!r}"
        )
    finally:
        db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (command_id,))


@pytest.mark.integration
def test_a_queued_row_finished_the_same_second_it_was_created_says_nothing_extra(client):
    """The counterpart to the test above: when finished_at equals created_at
    -- the common case, since the overlay usually finishes within the same
    second -- nothing is appended. Silence is correct here, not a missed
    case."""
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    db = Database(Settings())
    command_id = db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, "
        "created_at, finished_at) "
        "VALUES (%s, 'adjust_zeny', 1, 0, 'executed', 'test-same-second', "
        "NOW(), NOW())",
        (CHAR_WITH_ECONOMY,),
    )
    try:
        items = client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_ECONOMY, "limit": 500},
            headers=headers,
        ).json()["items"]
        matches = [e for e in items if e["detail"].get("id") == command_id]
        assert matches, "the synthetic queued row did not appear in the timeline"
        assert matches[0]["summary"].endswith(": executed"), matches[0]["summary"]
    finally:
        db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (command_id,))


@pytest.mark.integration
def test_merged_truncation_ranks_the_newest_row_first_across_sources(client):
    """entries.sort() + entries[:limit] is a full merge-sort-truncate over
    every source's fetched rows, not a per-source slice or a naive
    concatenation. Proven by planting one command-queue row created right
    now, which must outrank this character's entire zeny/item/GM-command
    history (all dated well before today in the lab), and confirming other
    kinds still surface further down once the limit is wide enough -- so the
    top-1 result is really "newest by date across sources", not just "the
    queue always wins" or "results are returned in source order".

    Deliberately does NOT force created_at to an arbitrary past or future
    value the way an earlier version of this test did: each source's own
    query pre-limits by ITS natural recency column (id DESC here, matching
    insertion order), and a created_at that contradicts insertion order would
    make that per-source pre-limit -- not the final merge this test is
    actually about -- drop the row before the merge ever saw it. NOW() keeps
    id order and date order in agreement, exactly as every real row's does.

    Uses CHAR_WITH_COMMANDS (150000), not CHAR_WITH_ECONOMY: the latter's
    ro_admin_commands rows alone (>1,200, accumulated by this project's own
    test runs -- see test_timeline_merges_multiple_log_sources's comment)
    outnumber the 500-row cap this endpoint allows, which would crowd its
    zeny/item history out of the response regardless of this test and prove
    nothing about the merge. 150000 has all four sources and well under 500
    rows total.
    """
    token = _token(client, ADMIN_USER, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    db = Database(Settings())
    command_id = db.execute(
        f"INSERT INTO {COMMAND_TABLE} "
        "(char_id, action, arg_int, arg_int2, status, requested_by, "
        "created_at, finished_at) "
        "VALUES (%s, 'sync_character', 0, 0, 'executed', "
        "'test-truncation-newest', NOW(), NOW())",
        (CHAR_WITH_COMMANDS,),
    )
    try:
        top_one = client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_COMMANDS, "limit": 1},
            headers=headers,
        ).json()["items"]
        assert len(top_one) == 1
        assert top_one[0]["detail"].get("id") == command_id, (
            "limit=1 did not return the globally newest entry across all "
            "sources -- truncation is not ranking by date across sources"
        )

        everything = client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_COMMANDS, "limit": 500},
            headers=headers,
        ).json()["items"]
        assert everything[0]["detail"].get("id") == command_id, (
            "the synthetic row must still rank first with a wider limit"
        )
        kinds = {e["kind"] for e in everything}
        assert len(kinds) > 1, (
            f"expected other sources' entries to still surface once the "
            f"limit is wide enough -- got only {kinds}, also consistent "
            f"with a merge that silently dropped every other source"
        )
    finally:
        db.execute(f"DELETE FROM {COMMAND_TABLE} WHERE id = %s", (command_id,))


# ---------------------------------------------------------------------------
# The narrow errno-1146 guard around the ro_admin_commands read, proven
# directly rather than relying on the lab happening to lack the table (it
# has it, and dropping it for real would break every other test in this
# file). Faked at the database layer, the same pattern as test_tier2.py's
# without_tier2/without_commands_table fixtures, and for the same reason.
#
# A minted service token, not a login: login reads the `login` table, and
# this database is a fake that only knows how to answer (or refuse) queries
# naming ro_admin_commands.
# ---------------------------------------------------------------------------


@pytest.fixture()
def logs_read_headers():
    token = issue_service_token(
        secret=TEST_JWT_SECRET, name="timeline-missing-table-test",
        scopes=[Permission.LOGS_READ], ttl_seconds=60,
    )
    return {"Authorization": f"Bearer {token}"}


def test_timeline_sources_excludes_the_command_queue_when_the_table_is_missing(
    client, monkeypatch, logs_read_headers
):
    """sources must name only the tables actually read -- a Tier 0 install,
    with no ro_admin_commands at all, must not list a table it never
    consulted."""
    def query(self, sql, params=None):
        if COMMAND_TABLE in sql:
            raise pymysql.err.ProgrammingError(
                1146, f"Table 'ragnarok.{COMMAND_TABLE}' doesn't exist"
            )
        return []

    monkeypatch.setattr("ro_admin.db.Database.query", query)
    r = client.get(
        "/api/v1/logs/timeline",
        params={"char_id": CHAR_WITH_ECONOMY},
        headers=logs_read_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert COMMAND_TABLE not in body["sources"], (
        "sources must not name a table that was never successfully read"
    )
    assert body["sources"] == ["atcommandlog", "zenylog", "picklog"], body["sources"]


def test_timeline_a_non_missing_table_error_on_the_commands_query_still_raises(
    client, monkeypatch, logs_read_headers
):
    """The guard is on the errno, not the exception class. 1054 (unknown
    column) and 1064 (syntax) are ProgrammingError too, and those are bugs in
    logs.py that must keep reaching the 500 handler rather than being
    quietly answered as 'no queue here'."""
    def query(self, sql, params=None):
        if COMMAND_TABLE in sql:
            raise pymysql.err.ProgrammingError(
                1054, "Unknown column 'nope' in 'field list'"
            )
        return []

    monkeypatch.setattr("ro_admin.db.Database.query", query)
    with pytest.raises(pymysql.err.ProgrammingError):
        client.get(
            "/api/v1/logs/timeline",
            params={"char_id": CHAR_WITH_ECONOMY},
            headers=logs_read_headers,
        )
