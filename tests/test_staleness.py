"""The staleness contract, exercised on both branches.

Every character in the reference lab is offline, so the integration tests only
ever see stale=False. The online branch is the whole point of the feature --
it is what stops a consumer treating a five-minute-old zeny figure as live --
so it is tested here, against _to_character directly, with no database.
"""
from datetime import datetime, timedelta

from ro_admin.projections import CHARACTER_COLUMNS, CHARACTER_VOLATILE
from ro_admin.routers.characters import FRESH_WITHIN_SECONDS, _to_character


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


# ---------------------------------------------------------------------------
# Tier 2. `stale` stops being a synonym for `online` and starts being a
# statement about evidence -- ro_admin_sync.synced_at, written only after the
# overlay read the stored row back and found it matching live memory.
# ---------------------------------------------------------------------------


def test_an_online_character_synced_just_now_is_not_stale():
    """The whole point of Tier 2. `stale` stops being a synonym for `online`
    and becomes a statement about evidence: the stored row was observed to
    match live memory two seconds ago."""
    now = datetime(2026, 9, 26, 12, 0, 0)
    character = _to_character(_row(online=1), synced_at=now - timedelta(seconds=2), now=now)
    assert character.online is True
    assert character.stale is False
    assert character.stale_fields == []
    assert character.synced_at is not None


def test_an_online_character_synced_long_ago_is_stale_again():
    """A sync is evidence with a shelf life. The character kept playing."""
    now = datetime(2026, 9, 26, 12, 0, 0)
    old = now - timedelta(seconds=FRESH_WITHIN_SECONDS + 1)
    character = _to_character(_row(online=1), synced_at=old, now=now)
    assert character.stale is True
    assert character.synced_at is not None, "still report WHEN, even when stale"


def test_an_online_character_never_synced_is_stale():
    """No Tier 2, or Tier 2 present and never asked. Same answer, and it is
    the pre-Tier-2 behaviour unchanged."""
    character = _to_character(_row(online=1), synced_at=None, now=datetime(2026, 9, 26))
    assert character.stale is True
    assert character.synced_at is None


def test_an_offline_character_is_never_stale_regardless_of_sync():
    """Nothing is holding newer state, so the row is authoritative. A sync
    time is irrelevant and must not make an offline character look stale."""
    character = _to_character(_row(online=0), synced_at=None, now=datetime(2026, 9, 26))
    assert character.stale is False
    assert character.stale_fields == []


def test_the_existing_callers_keep_working_without_the_new_arguments():
    """synced_at and now are optional. Every pre-Tier-2 call site, and every
    existing test in this file, must be unaffected."""
    character = _to_character(_row(online=1))
    assert character.stale is True
    assert character.synced_at is None
