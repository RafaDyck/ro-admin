"""Tier 2: detection, the sync action, and freshness as evidence.

EVERY TEST HERE PASSES WITH OR WITHOUT TIER 2 INSTALLED, and that is a
requirement rather than a convenience. Tier 2 needs a recompiled map server, so
on most installs -- including this project's own lab until the hook is built --
it is absent. A test that only passes after a recompile is one nobody can run,
and a suite nobody can run is how the predecessor's twelve test files ended up
asserting nothing.

So the tests that depend on the tier branch on capabilities.tier2.available,
which is itself an observation: the Tier 2 script calls a COMPILED buildin, and
a script naming an unknown buildin is discarded at parse time
(src/map/npc.cpp:4421-4424), so its heartbeat row cannot exist unless the hook
is in the running binary.

The second half of this file needs no database at all. It is the proof that an
install with no Tier 2 TABLES answers every character endpoint exactly as it did
before this feature existed -- which cannot be shown by dropping the tables,
because the lab is shared and the rest of the suite reads them.
"""
from datetime import datetime

import pymysql
import pytest
from fastapi.testclient import TestClient

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, CHAR_WITH_ECONOMY, TEST_JWT_SECRET,
    apply_test_env,
)
from ro_admin.auth import issue_service_token
from ro_admin.overlay import SYNC_TABLE, Action
from ro_admin.permissions import Permission
from ro_admin.projections import CHARACTER_COLUMNS
from ro_admin.routers.characters import _to_character


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


@pytest.fixture()
def tier2(client, headers) -> dict:
    """What this install says about Tier 2. The branch point for this file."""
    r = client.get("/api/v1/system/capabilities", headers=headers)
    assert r.status_code == 200
    return r.json()["tier2"]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_capabilities_reports_tier2_as_an_observation(tier2):
    """Not a stub, and not a boolean on its own. `installed` and `responding`
    stay separate for Tier 2 as for Tier 1, because "you have not run
    overlay/tier2/schema.sql" and "the hook is not in your binary" need
    different fixes and one flag cannot say which."""
    assert isinstance(tier2["available"], bool)
    assert isinstance(tier2["installed"], bool)
    assert isinstance(tier2["responding"], bool)
    assert tier2["reason"] != "compiled hooks not implemented in this release", (
        "the Tier 2 stub is still being reported"
    )


@pytest.mark.integration
def test_an_unavailable_tier2_names_the_install_step(tier2):
    """'tier 2 unavailable' with no next step is the message that sends an
    operator reading source code. Whichever of the four states this install is
    in, the reason has to name the thing to go and do."""
    if tier2["available"]:
        pytest.skip("tier 2 is installed on this server; the refusal path is below")
    assert len(tier2["reason"]) > 20
    assert "overlay/tier2/" in tier2["reason"], tier2["reason"]
    if not tier2["installed"]:
        assert "overlay/tier2/schema.sql" in tier2["reason"]
    elif not tier2["responding"]:
        # The interesting case, and the lab's own: the tables are there and no
        # heartbeat has ever arrived, which means the hook was never compiled.
        assert "overlay/tier2/README.md" in tier2["reason"]


@pytest.mark.integration
def test_an_available_tier2_reports_the_version_it_is_running(tier2):
    """A responding script that does not say what contract it speaks is a
    script the API cannot refuse safely."""
    if not tier2["available"]:
        pytest.skip("tier 2 is not installed on this server")
    assert tier2["installed"] is True and tier2["responding"] is True
    assert tier2["version"]


def test_the_two_tiers_are_reported_independently(client, monkeypatch):
    """Tier 1 being up says nothing about Tier 2, and the separate heartbeat
    tables exist so that stays true. If these two ever moved together, one of
    them would be inferred rather than observed.

    Driven off a fake database rather than the lab, and that is the point of
    this test's history. It used to assert that the two `reason` strings
    differed, which passed only because the lab had no Tier 2 -- and the moment
    Tier 2 was actually installed it became FLAKY rather than red: both tiers
    are healthy, both poll at 1000ms, and `classify_heartbeat` renders one
    sentence from one template, so the strings differ only when the two ages
    happen to round to different seconds. It passed or failed on a race.

    Two tiers reporting the same sentence about the same situation is correct
    behaviour, so string inequality was never the property worth asserting.
    What matters is that each tier's answer comes from its OWN heartbeat table,
    which is checkable exactly: give Tier 1 a fresh row, give Tier 2 none, and
    the two answers must diverge. If tier2 were copied from tier1 -- or read
    from a shared row -- this cannot pass.
    """
    statements: list[str] = []

    def query(self, sql, params=None):
        statements.append(sql)
        if "information_schema" in sql:
            # Every ro_admin table exists. So a Tier 2 that reports unavailable
            # below is reporting a missing HOOK, not missing tables -- the
            # sharper of the two ways these tiers can disagree.
            names = params if params and "table_name IN" in sql else (
                "ro_admin_commands", "ro_admin_overlay",
                "ro_admin_tier2", "ro_admin_sync",
            )
            return [{"t": n} for n in names]
        now = datetime(2026, 1, 1, 12, 0, 0)
        if "FROM ro_admin_overlay" in sql:
            return [{"instance_id": 1, "version": "1", "poll_ms": 1000,
                     "last_seen": now, "db_now": now}]
        if "FROM ro_admin_tier2" in sql:
            return []  # the hook is not in this map server's binary
        return []

    monkeypatch.setattr("ro_admin.db.Database.query", query)
    token = issue_service_token(
        secret=TEST_JWT_SECRET, name="tier-independence",
        scopes=[Permission.SYSTEM_READ], ttl_seconds=60,
    )
    caps = client.get(
        "/api/v1/system/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert caps["tier1"]["available"] is True, caps["tier1"]
    assert caps["tier2"]["available"] is False, caps["tier2"]
    # And the divergence is not an accident of ordering: both heartbeat tables
    # were really read. Without this, a tier2 hard-coded to unavailable would
    # satisfy the assertions above.
    assert any("FROM ro_admin_overlay" in s for s in statements)
    assert any("FROM ro_admin_tier2" in s for s in statements)


# ---------------------------------------------------------------------------
# The action
# ---------------------------------------------------------------------------


def _sync(char_id=CHAR_WITH_ECONOMY) -> dict:
    return {"action": "sync_character", "char_id": char_id}


# A sync_character row can legitimately be terminal by the time the response is
# built, exactly as a Tier 1 row can -- and 'failed' is a legitimate FIRST
# answer here in a way it is not for Tier 1: the char server commits after
# chrif_save returns, so a read-back that has not landed yet fails the row with
# "flush queued but not yet persisted - retry". See SyncCharacter's docstring.
SYNC_STATUSES = {"pending", "processing", "executed", "failed"}


@pytest.mark.integration
def test_sync_character_is_refused_with_409_when_tier2_is_absent(
    client, headers, tier2
):
    """Not queued. The Tier 1 script filters `action <> 'sync_character'`, so
    a row enqueued here would have no consumer at all and would sit 'pending'
    forever -- the dead-queue outcome the guard exists to prevent, and the
    shape of the predecessor's seventy unconsumable rows."""
    if tier2["available"]:
        pytest.skip("tier 2 is installed on this server; see the 202 test below")
    r = client.post("/api/v1/commands", json=_sync(), headers=headers)
    assert r.status_code == 409, r.text


@pytest.mark.integration
def test_the_409_is_actionable_and_the_same_sentence_capabilities_reports(
    client, headers, tier2
):
    """Not "a similar message" -- the one string, from one definition. An agent
    is told to read /system/capabilities first, and being told two different
    things about one situation is how that distinction gets lost."""
    if tier2["available"]:
        pytest.skip("tier 2 is installed on this server")
    detail = client.post(
        "/api/v1/commands", json=_sync(), headers=headers
    ).json()["detail"]
    assert detail == tier2["reason"]
    assert "overlay/tier2/" in detail


@pytest.mark.integration
def test_tier1_actions_are_still_accepted_while_tier2_is_absent(
    client, headers, tier2
):
    """The guard is per-action, and this is the half that proves it. A missing
    Tier 2 must not close the queue for the actions Tier 1 consumes -- if this
    ever 409s alongside the sync above, the guard has gone back to asking one
    global question."""
    if tier2["available"]:
        pytest.skip("both tiers present, so this proves nothing about the split")
    caps = client.get("/api/v1/system/capabilities", headers=headers).json()
    if not caps["tier1"]["available"]:
        pytest.skip("tier 1 is not responding on this server either")
    r = client.post(
        "/api/v1/commands",
        json={"action": "give_item", "char_id": CHAR_WITH_ECONOMY,
              "item_id": 909, "amount": 1},
        headers=headers,
    )
    assert r.status_code == 202, r.text


@pytest.mark.integration
def test_sync_character_is_accepted_with_202_when_tier2_is_available(
    client, headers, tier2
):
    """202 -- accepted, not performed -- like every other queued action. The
    status may already be terminal, and 'failed' with "flush queued but not yet
    persisted - retry" is a legitimate first answer rather than an error."""
    if not tier2["available"]:
        pytest.skip("tier 2 is not installed on this server")
    r = client.post("/api/v1/commands", json=_sync(), headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["action"] == "sync_character"
    assert body["status"] in SYNC_STATUSES, body["status"]
    assert r.headers["Location"] == f"/api/v1/commands/{body['id']}"


@pytest.mark.integration
def test_a_queued_sync_reports_its_own_consumers_liveness(client, headers, tier2):
    """`overlay_responding` answers "is anything going to pick this up". For a
    sync_character row that is Tier 2's heartbeat, not Tier 1's -- reporting
    Tier 1's would tell a caller polling a stuck sync that its consumer was
    alive."""
    if not tier2["available"]:
        pytest.skip("tier 2 is not installed, so no such row can be created")
    new_id = client.post(
        "/api/v1/commands", json=_sync(), headers=headers
    ).json()["id"]
    body = client.get(f"/api/v1/commands/{new_id}", headers=headers).json()
    assert body["overlay_responding"] is tier2["responding"]


# ---------------------------------------------------------------------------
# The guard reads the tier the action actually needs.
#
# No database: _consumer_status only asks information_schema which tables
# exist, so a fake that records the parameters shows which tier was consulted.
# ---------------------------------------------------------------------------


class _RecordingDb:
    """Answers every query with no rows, and remembers what was asked."""

    def __init__(self):
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        return []


@pytest.mark.parametrize(
    "action, expected, forbidden",
    [
        (Action.SYNC_CHARACTER, "ro_admin_tier2", "ro_admin_overlay"),
        (Action.GIVE_ITEM, "ro_admin_overlay", "ro_admin_tier2"),
        (Action.ADJUST_ZENY, "ro_admin_overlay", "ro_admin_tier2"),
    ],
)
def test_the_guard_consults_the_tier_the_action_needs(action, expected, forbidden):
    """Every queueable action, so an action added later is covered by the
    parametrisation rather than by whoever remembers. The pairing is the queue
    split: Tier 2 claims sync_character and nothing else."""
    from ro_admin.routers.commands import _consumer_status

    db = _RecordingDb()
    _consumer_status(db, action)
    asked = str(db.calls[0][1])
    assert expected in asked, f"{action} did not consult {expected}: {asked}"
    assert forbidden not in asked, f"{action} consulted {forbidden}: {asked}"


def test_every_queueable_action_is_covered_by_the_test_above():
    """The parametrisation is a list, and a list goes stale. A new Action with
    no row there would be silently unchecked."""
    covered = {Action.SYNC_CHARACTER, Action.GIVE_ITEM, Action.ADJUST_ZENY}
    assert set(Action) == covered, (
        f"actions with no guard test: {sorted(set(Action) - covered)}"
    )


# ---------------------------------------------------------------------------
# An install with no Tier 2 tables.
#
# Faked at the database layer, the way tests/test_maps.py fakes an un-imported
# map table and for the same reason: the honest way to provoke this is to DROP
# ro_admin_sync, which would break the integration tests above, the capability
# tests, and the shared lab for whoever runs pytest next.
#
# pymysql raises ProgrammingError(1146) from Database.query when a table in the
# statement does not exist -- verified against MySQL 8.0, which answers
# "Table 'ragnarok.ro_admin_sync' doesn't exist" with that errno.
#
# The `char` rows are faked too, so these need no database and run in the
# no-database suite. A service token is pure JWT, so auth needs none either.
# ---------------------------------------------------------------------------

CHAR_ID = 150000

# One row with every allowlisted column present, as the plain query returns it.
# Online deliberately: offline is the uninteresting case, because stale is false
# either way and a regression in the freshness logic would not show.
_ROW = {name: 0 for name in CHARACTER_COLUMNS}
_ROW.update({
    "char_id": CHAR_ID, "account_id": 2000005, "name": "Kami",
    "last_map": "prontera", "last_login": None, "online": 1, "zeny": 592213,
})

ACCOUNT_ID = _ROW["account_id"]

# Every endpoint that serves a Character. The account listing is included
# because it serves the same model from its own query -- an endpoint that was
# forgotten here would keep answering `stale` the pre-Tier-2 way forever, and
# one character would give two answers depending on which URL was asked.
CHARACTER_ENDPOINTS = (
    "/api/v1/characters",
    f"/api/v1/characters/{CHAR_ID}",
    f"/api/v1/characters/{CHAR_ID}/inventory",
    f"/api/v1/accounts/{ACCOUNT_ID}/characters",
)


@pytest.fixture()
def without_tier2(client, monkeypatch) -> list[str]:
    """A database that has `char` and `inventory` but not ro_admin_sync.

    Patched as a plain function rather than a callable object, deliberately: a
    class instance assigned to Database.query is not a descriptor, so it would
    be called without the Database `self` and every argument would arrive one
    place to the left -- silently, as an empty result rather than an error.

    Returns the list of statements the service attempted, so a test can check
    that the joined query was really tried and not optimised away.
    """
    statements: list[str] = []

    def query(self, sql, params=None):
        statements.append(sql)
        if SYNC_TABLE in sql:
            raise pymysql.err.ProgrammingError(
                1146, f"Table 'ragnarok.{SYNC_TABLE}' doesn't exist"
            )
        if "FROM `char`" in sql:
            return [dict(_ROW)]
        if "FROM login" in sql:
            # The account endpoint 404s on an unknown account before it reads
            # any character, so the account has to exist for this to test
            # anything.
            return [{"account_id": ACCOUNT_ID}]
        return []

    monkeypatch.setattr("ro_admin.db.Database.query", query)
    return statements


@pytest.fixture()
def reader_headers():
    """A CHARACTERS_READ service token. Minted rather than logged in for:
    login reads the database, and this database is a fake."""
    token = issue_service_token(
        secret=TEST_JWT_SECRET, name="no-tier2-test",
        scopes=[Permission.CHARACTERS_READ], ttl_seconds=60,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", CHARACTER_ENDPOINTS)
def test_a_missing_sync_table_is_not_an_error(
    client, without_tier2, reader_headers, path
):
    """The join is not load-bearing. Most installs will never have this table,
    and on them every character endpoint has to keep working -- not 500 (which
    would say this service is broken), and not 503 either (the map endpoints'
    answer, and wrong here: nothing is missing that a caller asked for)."""
    r = client.get(path, headers=reader_headers)
    assert r.status_code == 200, f"{path} answered {r.status_code}: {r.text[:200]}"


def test_without_the_table_the_character_response_is_byte_for_byte_the_old_one(
    client, without_tier2, reader_headers
):
    """The strongest form of "unchanged", and the reason it is provable: the
    pre-Tier-2 answer is exactly what _to_character returns with no sync
    arguments, so the comparison is against the old behaviour itself rather
    than against a copy of its output that would need maintaining.

    Only `synced_at` may be added, and only as null -- a new field is a
    documented addition; a changed `stale` would be a silent behaviour change
    on every install that has no Tier 2.
    """
    body = client.get(
        f"/api/v1/characters/{CHAR_ID}", headers=reader_headers
    ).json()
    before = _to_character(dict(_ROW)).model_dump(mode="json", by_alias=True)
    assert body == before
    assert body["synced_at"] is None
    assert body["stale"] is True, "an online character with no evidence is stale"
    assert body["stale_fields"], "and it must still name the fields"


def test_without_the_table_the_fallback_query_actually_ran(
    client, without_tier2, reader_headers
):
    """Guards the test above from passing for the wrong reason. If the joined
    statement were never attempted, the fallback would be trivially identical
    and this file would be checking nothing about the join at all."""
    client.get(f"/api/v1/characters/{CHAR_ID}", headers=reader_headers)
    assert [s for s in without_tier2 if SYNC_TABLE in s], (
        "the joined query was never attempted"
    )
    assert any(
        "FROM `char`" in s and SYNC_TABLE not in s for s in without_tier2
    ), "the plain fallback query never ran"


def test_without_the_table_the_listing_answers_too(
    client, without_tier2, reader_headers
):
    """The list endpoint builds its suffix from query parameters, so it is a
    separate path through the same helper and a separate chance to leave a
    dangling join."""
    body = client.get(
        "/api/v1/characters?online=true&limit=5", headers=reader_headers
    ).json()
    assert body["items"], "the listing returned nothing"
    for item in body["items"]:
        assert item["synced_at"] is None
        assert item["stale"] is True


def test_every_character_serving_endpoint_agrees_about_freshness(
    client, without_tier2, reader_headers
):
    """One character, one answer. The account listing serves the same Character
    model from its own SELECT, so it needs the same join -- without it, that
    endpoint would report every character stale and synced_at null on a Tier 2
    install where /characters/{id} said otherwise."""
    one = client.get(
        f"/api/v1/characters/{CHAR_ID}", headers=reader_headers
    ).json()
    listed = client.get(
        f"/api/v1/accounts/{ACCOUNT_ID}/characters", headers=reader_headers
    ).json()["items"]
    assert listed, "the account listing returned no characters"
    for item in listed:
        assert item["synced_at"] == one["synced_at"]
        assert item["stale"] == one["stale"]
        assert item["stale_fields"] == one["stale_fields"]


def test_a_sql_error_that_is_not_a_missing_table_is_still_a_500(
    client, monkeypatch, reader_headers
):
    """The guard is on the errno, not the exception class. 1054 (unknown
    column) and 1064 (syntax) are ProgrammingError too, and those are bugs in
    characters.py -- answering one by quietly dropping the sync time would hide
    a broken projection behind a response that looks fine."""
    def unknown_column(self, sql, params=None):
        raise pymysql.err.ProgrammingError(
            1054, "Unknown column 'nope' in 'field list'"
        )

    monkeypatch.setattr("ro_admin.db.Database.query", unknown_column)
    with pytest.raises(pymysql.err.ProgrammingError):
        client.get(f"/api/v1/characters/{CHAR_ID}", headers=reader_headers)


def test_the_openapi_document_describes_what_synced_at_covers(client):
    """A consumer reading the document must not conclude that a fresh
    character means a fresh inventory. The distinction lives in one packet's
    contents and cannot be guessed from the field name."""
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    described = schema["Character"]["properties"]["synced_at"]["description"]
    assert "observed" in described.lower()
    assert "inventory" in described.lower()
    assert "synced_at" not in schema["Inventory"]["properties"], (
        "the inventory must not claim a sync time -- nothing observes an "
        "inventory landing"
    )
