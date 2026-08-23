import pytest

from ro_admin.config import Settings


def test_settings_reject_missing_jwt_secret(monkeypatch):
    monkeypatch.delenv("RO_ADMIN_JWT_SECRET", raising=False)
    monkeypatch.setenv("RO_ADMIN_DB_USER", "u")
    monkeypatch.setenv("RO_ADMIN_DB_PASSWORD", "p")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_settings_reject_empty_jwt_secret(monkeypatch):
    monkeypatch.setenv("RO_ADMIN_JWT_SECRET", "")
    monkeypatch.setenv("RO_ADMIN_DB_USER", "u")
    monkeypatch.setenv("RO_ADMIN_DB_PASSWORD", "p")
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_settings_load_when_all_present(monkeypatch):
    monkeypatch.setenv("RO_ADMIN_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("RO_ADMIN_DB_USER", "ragnarok")
    monkeypatch.setenv("RO_ADMIN_DB_PASSWORD", "ragnarok")
    settings = Settings(_env_file=None)
    assert settings.db_name == "ragnarok"
    assert settings.md5_passwords is False
