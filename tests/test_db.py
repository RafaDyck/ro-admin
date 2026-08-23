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
