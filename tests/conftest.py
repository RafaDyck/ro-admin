"""Shared test configuration.

Credentials come from the environment rather than from literals scattered
through the suite. Two reasons, and the second matters more:

  * A public repository should not teach people to paste credentials into test
    files, however harmless the particular values are.
  * These tests should be runnable against *your* server. Hardcoding one lab's
    accounts makes the integration suite useless to everyone else -- the same
    "works only on my machine" coupling this product exists to avoid.

Defaults target the reference lab so `pytest` works out of the box there.
Override with RO_ADMIN_TEST_* to point the suite at your own install.
"""
import os
import time

import pytest

# Accounts the integration tests authenticate as. The admin account needs
# group_id >= 10; the player account must be group_id 0, because several tests
# assert that it is REFUSED -- pointing both at admins would turn those into
# false passes.
ADMIN_USER = os.environ.get("RO_ADMIN_TEST_ADMIN_USER", "admin1234")
ADMIN_PASSWORD = os.environ.get("RO_ADMIN_TEST_ADMIN_PASSWORD", "password1234")
PLAYER_USER = os.environ.get("RO_ADMIN_TEST_PLAYER_USER", "test1234")
PLAYER_PASSWORD = os.environ.get("RO_ADMIN_TEST_PLAYER_PASSWORD", "test1234")

# Database the service under test connects to.
DB_USER = os.environ.get("RO_ADMIN_TEST_DB_USER", "ragnarok")
DB_PASSWORD = os.environ.get("RO_ADMIN_TEST_DB_PASSWORD", "ragnarok")
DB_PORT = os.environ.get("RO_ADMIN_TEST_DB_PORT", "3307")

# Any value long enough to satisfy the min_length validator. Never a real
# secret: tests must not depend on one, and a "realistic looking" constant here
# is how placeholder secrets end up copied into deployments.
TEST_JWT_SECRET = "test-only-not-a-real-secret-value"

# Characters the integration tests read. Character 150000 has GM command
# history; 200000 is a seeded demo character with zeny and item history.
CHAR_WITH_COMMANDS = int(os.environ.get("RO_ADMIN_TEST_CHAR_COMMANDS", "150000"))
CHAR_WITH_ECONOMY = int(os.environ.get("RO_ADMIN_TEST_CHAR_ECONOMY", "200000"))


def apply_test_env(monkeypatch) -> None:
    """Point the app at the test database. Used by every client fixture."""
    monkeypatch.setenv("RO_ADMIN_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("RO_ADMIN_DB_USER", DB_USER)
    monkeypatch.setenv("RO_ADMIN_DB_PASSWORD", DB_PASSWORD)
    monkeypatch.setenv("RO_ADMIN_DB_PORT", DB_PORT)


# --- queue rows the suite leaves behind ---------------------------------------
#
# Several integration tests queue real commands, mostly against a character
# who is offline, and the overlay refuses them as `failed: character is not
# online`. Nothing in the game changes, but every run used to leave its rows
# behind: the lab reached 1,517 of them, and they crowded real events out of
# the character's History and out of the timeline tests' windows.
#
# The rows can't be avoided -- the tests assert that the queue records the
# signed-in user, so they must go through the real enqueue -- so they are
# recorded as they are created and deleted when the session ends.
#
# Deleted: only rows that provably changed nothing. That means still
# `pending` (never claimed, so never run), or `failed` with "character is not
# online". A row that executed, or failed any other way, may have touched
# the game and is left alone as the record of that. Each DELETE is one atomic
# statement, so a row the overlay claims in between is `processing` by then
# and is skipped rather than deleted mid-run.

_queued_by_tests: list[int] = []

# How long to let the overlay finish rows still processing when the session
# ends. It polls every second, and a refusal is immediate once claimed.
_SETTLE_SECONDS = 5.0


@pytest.fixture(autouse=True)
def _record_queued_commands(monkeypatch):
    """Note the id of every row a test enqueues through the router."""
    from ro_admin.routers import commands

    real_enqueue = commands.enqueue

    def recording_enqueue(*args, **kwargs):
        new_id = real_enqueue(*args, **kwargs)
        _queued_by_tests.append(new_id)
        return new_id

    monkeypatch.setattr(commands, "enqueue", recording_enqueue)


def pytest_sessionfinish(session, exitstatus):
    if not _queued_by_tests:
        return
    import pymysql

    try:
        _delete_rows_that_changed_nothing(sorted(set(_queued_by_tests)))
    except pymysql.err.OperationalError as exc:
        # No database to clean. A test that enqueued against a faked one
        # left nothing real behind, and cleanup must never fail the run.
        print(f"\nqueue cleanup skipped: {exc.args[-1] if exc.args else exc}")


def _delete_rows_that_changed_nothing(ids: list[int]) -> None:
    from ro_admin.config import Settings
    from ro_admin.db import Database

    db = Database(Settings(
        jwt_secret=TEST_JWT_SECRET, db_user=DB_USER,
        db_password=DB_PASSWORD, db_port=int(DB_PORT),
    ))
    marks = ",".join(["%s"] * len(ids))

    deadline = time.monotonic() + _SETTLE_SECONDS
    while time.monotonic() < deadline:
        busy = db.query(
            f"SELECT COUNT(*) AS n FROM ro_admin_commands "
            f"WHERE id IN ({marks}) AND status = 'processing'",
            ids,
        )[0]["n"]
        if not busy:
            break
        time.sleep(0.5)

    # `requested_by` as well as the id, so that an id a faked enqueue made up
    # can only ever match a row this suite's own account queued.
    db.execute(
        f"DELETE FROM ro_admin_commands WHERE id IN ({marks}) "
        f"AND requested_by = %s AND (status = 'pending' OR "
        f"(status = 'failed' AND error_message = 'character is not online'))",
        [*ids, ADMIN_USER],
    )
