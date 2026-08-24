"""The allowlists, checked as data. No database needed."""
import pytest

from ro_admin.projections import (
    ACCOUNT_COLUMNS, CHARACTER_COLUMNS, CHARACTER_VOLATILE,
    ITEM_DETAIL_COLUMNS, ITEM_LIST_COLUMNS, select_clause,
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


def test_item_list_projection_is_small_enough_to_page():
    """A page of 200 rows carrying seventy columns each is a response nobody
    wants. The list answers "which items match"; the detail answers "what is
    this item"."""
    assert len(ITEM_LIST_COLUMNS) <= 12


def test_item_list_projection_excludes_the_script_columns():
    """rAthena script source runs to hundreds of characters per item."""
    for heavy in ("script", "equip_script", "unequip_script"):
        assert heavy not in ITEM_LIST_COLUMNS


def test_item_list_projection_carries_enough_to_identify_an_item():
    for needed in ("id", "name_english", "type", "slots"):
        assert needed in ITEM_LIST_COLUMNS


def test_item_detail_is_a_superset_of_the_list():
    """Otherwise fetching the detail of a row you just listed could lose a
    field, which is a genuinely confusing API to use."""
    assert set(ITEM_LIST_COLUMNS) <= set(ITEM_DETAIL_COLUMNS)


def test_item_detail_carries_the_script_so_an_operator_can_see_behaviour():
    """"What does this item actually do" is the question the script answers,
    and it is server-side knowledge the caller cannot reconstruct."""
    assert "script" in ITEM_DETAIL_COLUMNS


def test_item_projections_render_through_select_clause():
    """`range` is a MySQL reserved word and a real item_db column."""
    assert "`range`" in select_clause(ITEM_DETAIL_COLUMNS)


def test_every_item_list_column_has_a_field_on_the_summary_model():
    """The projection and the model are two halves of one contract.

    pydantic defaults to extra='ignore', so a column added to the projection
    without a matching model field is SELECTed, silently discarded, and never
    reaches the response -- no error, no failing test. That same behaviour once
    defeated an attempt to prove the credential-leak test could fail at all.
    """
    from ro_admin.routers.items import ItemSummary
    missing = set(ITEM_LIST_COLUMNS) - set(ItemSummary.model_fields)
    assert not missing, f"selected but never served: {missing}"


def test_every_item_detail_column_has_a_field_on_the_item_model():
    from ro_admin.routers.items import Item
    missing = set(ITEM_DETAIL_COLUMNS) - set(Item.model_fields)
    assert not missing, f"selected but never served: {missing}"
