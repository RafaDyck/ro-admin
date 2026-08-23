"""The staleness contract, exercised on both branches.

Every character in the reference lab is offline, so the integration tests only
ever see stale=False. The online branch is the whole point of the feature --
it is what stops a consumer treating a five-minute-old zeny figure as live --
so it is tested here, against _to_character directly, with no database.
"""
from ro_admin.projections import CHARACTER_COLUMNS, CHARACTER_VOLATILE
from ro_admin.routers.characters import _to_character


def _row(online: int) -> dict:
    """A char row with every allowlisted column present."""
    row = {name: 0 for name in CHARACTER_COLUMNS}
    row.update({
        "char_id": 150000, "account_id": 2000005, "name": "Kami",
        "last_map": "prontera", "last_login": None, "online": online,
    })
    return row


def test_an_online_character_is_marked_stale():
    character = _to_character(_row(online=1))
    assert character.online is True
    assert character.stale is True


def test_an_online_character_names_exactly_the_volatile_fields():
    """The list must match the projection's own record of what the map server
    holds in memory -- not a second, drifting copy of that knowledge."""
    character = _to_character(_row(online=1))
    assert set(character.stale_fields) == set(CHARACTER_VOLATILE)


def test_stale_fields_are_sorted_so_the_response_is_stable():
    """An unordered set would make the JSON differ between identical requests,
    which breaks caching and makes diffs noisy."""
    character = _to_character(_row(online=1))
    assert character.stale_fields == sorted(character.stale_fields)


def test_an_online_character_still_reports_its_values():
    """Labelled, never withheld. A null meaning "we chose not to tell you" is
    indistinguishable from zero."""
    row = _row(online=1)
    row["zeny"] = 592213
    assert _to_character(row).zeny == 592213


def test_an_offline_character_is_not_stale_and_names_nothing():
    character = _to_character(_row(online=0))
    assert character.online is False
    assert character.stale is False
    assert character.stale_fields == []
