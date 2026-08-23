"""The allowlists, checked as data. No database needed."""
import pytest

from ro_admin.projections import (
    ACCOUNT_COLUMNS, CHARACTER_COLUMNS, CHARACTER_VOLATILE, select_clause,
)


def test_account_projection_excludes_every_credential():
    """The whole reason this module exists. login.user_pass is plaintext on a
    stock rAthena -- verified in the reference lab, where it reads 'admin'."""
    for forbidden in ("user_pass", "pincode", "web_auth_token", "web_auth_token_enabled"):
        assert forbidden not in ACCOUNT_COLUMNS


def test_account_projection_excludes_personal_data():
    for forbidden in ("email", "birthdate", "last_ip"):
        assert forbidden not in ACCOUNT_COLUMNS


def test_account_projection_keeps_what_an_operator_needs():
    for needed in ("account_id", "userid", "group_id", "state", "lastlogin"):
        assert needed in ACCOUNT_COLUMNS


def test_character_projection_keeps_identity_and_progress():
    for needed in ("char_id", "account_id", "name", "class", "base_level", "zeny", "online"):
        assert needed in CHARACTER_COLUMNS


def test_volatile_columns_are_a_subset_of_the_projection():
    """Every column we label stale must actually be returned, or the label
    describes a field the caller cannot see."""
    assert CHARACTER_VOLATILE <= set(CHARACTER_COLUMNS)


def test_zeny_is_volatile():
    """The specific field the founding incident was about."""
    assert "zeny" in CHARACTER_VOLATILE


def test_online_is_not_volatile():
    """char.online is written by the CHAR server at session start and end, not
    held in map-server memory, so it is not subject to the autosave lag that
    makes zeny stale."""
    assert "online" not in CHARACTER_VOLATILE


def test_select_clause_backticks_every_column():
    """`class` and `int` are reserved words and real rAthena column names."""
    clause = select_clause(("char_id", "class", "int"))
    assert clause == "`char_id`, `class`, `int`"


def test_select_clause_rejects_anything_not_an_identifier():
    """These names are constants in this file, never caller input. The guard is
    here so that stays true if someone later passes a name in from a request."""
    for bad in ("zeny; DROP TABLE login", "*", "a b", ""):
        with pytest.raises(ValueError):
            select_clause(("char_id", bad))
