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
