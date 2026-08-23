import pytest

from ro_admin.config import Settings
from ro_admin.db import Database

from conftest import (
    ADMIN_PASSWORD, ADMIN_USER, PLAYER_PASSWORD, PLAYER_USER,
    apply_test_env,
)


def _settings(monkeypatch):
    apply_test_env(monkeypatch)
    # 3307, not 3306: a native MariaDB service shadows 3306 on this host.
    apply_test_env(monkeypatch)
    return Settings(_env_file=None)


@pytest.mark.integration
def test_database_round_trip(monkeypatch):
    db = Database(_settings(monkeypatch))
    rows = db.query("SELECT 1 AS n")
    assert rows == [{"n": 1}]


@pytest.mark.integration
def test_query_is_parameterized(monkeypatch):
    db = Database(_settings(monkeypatch))
    rows = db.query("SELECT %s AS given", ("O'Brien; DROP TABLE `char`;--",))
    assert rows[0]["given"] == "O'Brien; DROP TABLE `char`;--"


def test_execute_returns_the_new_row_id(monkeypatch):
    """Callers need the id to poll the row afterwards. Returning it from the
    same connection avoids a SELECT that could race a concurrent insert."""
    import ro_admin.db as db_module

    captured = {}

    class FakeCursor:
        lastrowid = 4242

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db_module.Database, "_connect", lambda self: FakeConn())

    # _settings() is defined at the top of this file and passes
    # _env_file=None, so a stray .env on the developer's machine cannot
    # change what this test constructs.
    new_id = db_module.Database(_settings(monkeypatch)).execute(
        "INSERT INTO t (a) VALUES (%s)", (1,)
    )
    assert new_id == 4242
    assert captured["params"] == (1,)
