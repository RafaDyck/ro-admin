"""Unit tests for the overlay module. No database, no game server."""
from datetime import datetime, timedelta

import pytest

from ro_admin.overlay import (
    OVERLAY_VERSION, TIER1, TIER2_VERSION, Action, InvalidCommand, OverlayStatus,
    classify_heartbeat, consumer_tier, validate,
)
from ro_admin.overlay import (
    HEARTBEAT_TABLE, TIER2, TIER2_TABLE, enqueue, read_command, read_status,
    read_tier2_status,
)


def test_give_item_accepts_a_reasonable_request():
    assert validate(Action.GIVE_ITEM, {"item_id": 909, "amount": 7}) == (909, 7)


def test_adjust_zeny_maps_delta_to_the_first_argument():
    assert validate(Action.ADJUST_ZENY, {"delta": -500}) == (-500, 0)


def test_adjust_zeny_accepts_a_negative_delta():
    """Taking zeny away is an administrative action too, and it is the half
    an absolute 'set' would have to perform anyway."""
    assert validate(Action.ADJUST_ZENY, {"delta": -1})[0] == -1


@pytest.mark.parametrize("amount", [0, -1, 30_001])
def test_give_item_rejects_out_of_range_amounts(amount):
    with pytest.raises(InvalidCommand):
        validate(Action.GIVE_ITEM, {"item_id": 909, "amount": amount})


def test_give_item_rejects_a_non_positive_item_id():
    with pytest.raises(InvalidCommand):
        validate(Action.GIVE_ITEM, {"item_id": 0, "amount": 1})


def test_unknown_action_is_rejected_loudly():
    """Deliberately not defaulted. An unrecognised action must never fall
    through to 'do nothing and report success'."""
    with pytest.raises(InvalidCommand):
        validate("banish_player", {})


def test_missing_argument_is_rejected():
    with pytest.raises(InvalidCommand):
        validate(Action.GIVE_ITEM, {"item_id": 909})


def test_a_zero_zeny_delta_is_rejected():
    """@zeny 0 is refused by the game (src/map/atcommand.cpp:2897-2900), and
    the script's post-condition check cannot distinguish "changed by zero" from
    "refused" -- so a zero delta would be recorded as executed for work the
    game declined to do. Reject it here instead."""
    with pytest.raises(InvalidCommand):
        validate(Action.ADJUST_ZENY, {"delta": 0})


def test_overlay_version_is_a_plain_string():
    assert isinstance(OVERLAY_VERSION, str) and OVERLAY_VERSION


def test_tier2_version_is_a_plain_string():
    assert isinstance(TIER2_VERSION, str) and TIER2_VERSION


def test_sync_character_takes_no_arguments():
    """An empty arg spec must fall through validate() to the trailing-zero
    fill rather than being a special case. The script reads arg_int for other
    actions and ignores it here, so (0, 0) is the only honest row to write --
    and a spec that accidentally required an argument would make every
    sync_character request a 422."""
    assert validate(Action.SYNC_CHARACTER, {}) == (0, 0)


def test_sync_character_ignores_arguments_it_was_not_given_a_spec_for():
    """Not an invitation to pass them -- a statement that a stray key cannot
    smuggle a value onto the row."""
    assert validate(Action.SYNC_CHARACTER, {"delta": 500}) == (0, 0)


def _row(age_seconds: float = 0.0, version: str = OVERLAY_VERSION, poll_ms: int = 1000):
    now = datetime(2026, 8, 23, 12, 0, 0)
    return (
        {
            "instance_id": 1755950000,
            "version": version,
            "poll_ms": poll_ms,
            "last_seen": now - timedelta(seconds=age_seconds),
        },
        now,
    )


def test_missing_tables_report_not_installed():
    status = classify_heartbeat(tier=TIER1, tables_present=False, row=None, now=datetime(2026, 8, 23))
    assert status.installed is False
    assert status.responding is False
    assert "schema.sql" in status.reason


def test_tables_without_a_heartbeat_row_report_never_run():
    status = classify_heartbeat(tier=TIER1, tables_present=True, row=None, now=datetime(2026, 8, 23))
    assert status.installed is True
    assert status.responding is False
    assert "never" in status.reason.lower()


def test_a_fresh_heartbeat_is_responding():
    row, now = _row(age_seconds=1)
    status = classify_heartbeat(tier=TIER1, tables_present=True, row=row, now=now)
    assert status.responding is True
    assert status.instance_id == 1755950000


def test_a_stale_heartbeat_is_not_responding_and_says_how_stale():
    row, now = _row(age_seconds=47)
    status = classify_heartbeat(tier=TIER1, tables_present=True, row=row, now=now)
    assert status.responding is False
    assert "47" in status.reason


def test_staleness_threshold_scales_with_the_scripts_own_poll_interval():
    """A server configured to poll slowly must not be called stale for
    honouring its own configuration. The threshold is derived from the
    heartbeat itself, not hardcoded against one lab's timing."""
    row, now = _row(age_seconds=20, poll_ms=10_000)
    assert classify_heartbeat(tier=TIER1, tables_present=True, row=row, now=now).responding is True


def test_a_version_mismatch_is_reported_even_though_the_script_is_alive():
    """Responding but incompatible. Silently treating this as available is how
    an operator ends up debugging a contract change at 2am."""
    row, now = _row(age_seconds=1, version="0")
    status = classify_heartbeat(tier=TIER1, tables_present=True, row=row, now=now)
    assert status.responding is True
    assert status.compatible is False
    assert "0" in status.reason and OVERLAY_VERSION in status.reason


def test_a_future_heartbeat_does_not_produce_a_negative_age():
    """Clock skew between the API host and the database. Age clamps at zero
    rather than reporting '-4 seconds ago'."""
    row, now = _row(age_seconds=-4)
    status = classify_heartbeat(tier=TIER1, tables_present=True, row=row, now=now)
    assert status.age_seconds == 0.0
    assert status.responding is True


class FakeDb:
    """Captures SQL and parameters so the query SHAPE can be asserted without
    a database. What matters here is that values arrive as parameters."""

    def __init__(self, rows=None, new_id=1):
        self.rows = rows if rows is not None else []
        self.new_id = new_id
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        return self.rows.pop(0) if self.rows else []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self.new_id


def test_enqueue_passes_every_value_as_a_parameter():
    """No value is ever formatted into the statement. char_id is an int here,
    but requested_by is a caller-controlled string and must not be either."""
    db = FakeDb(new_id=77)
    new_id = enqueue(db, char_id=150002, action=Action.GIVE_ITEM,
                     args={"item_id": 909, "amount": 3},
                     requested_by="admin1234")
    assert new_id == 77
    sql, params = db.calls[0]
    assert "%s" in sql
    assert "150002" not in sql and "909" not in sql and "admin1234" not in sql
    assert 150002 in params and 909 in params and "admin1234" in params


def test_enqueue_validates_before_it_writes():
    db = FakeDb()
    with pytest.raises(InvalidCommand):
        enqueue(db, char_id=1, action=Action.GIVE_ITEM,
                args={"item_id": 909, "amount": 0}, requested_by="admin1234")
    assert db.calls == [], "a rejected command must not reach the database"


def test_enqueue_writes_a_pending_row_not_an_executed_one():
    db = FakeDb()
    enqueue(db, char_id=1, action=Action.ADJUST_ZENY, args={"delta": 5},
            requested_by="admin1234")
    sql, params = db.calls[0]
    assert "pending" in sql or "pending" in [p for p in params if isinstance(p, str)]


def test_read_command_returns_none_for_an_unknown_id():
    assert read_command(FakeDb(rows=[[]]), 999) is None


def test_read_status_reports_not_installed_when_the_tables_are_absent():
    db = FakeDb(rows=[[]])   # information_schema returns nothing
    status = read_status(db)
    assert status.installed is False


def test_read_tier2_status_reports_not_installed_when_the_tables_are_absent():
    db = FakeDb(rows=[[]])   # information_schema returns nothing
    status = read_tier2_status(db)
    assert status.installed is False
    assert "overlay/tier2/README.md" in status.reason


def test_the_two_tiers_read_two_different_heartbeat_tables():
    """A shared row would mean dropping one tier disturbed the other's
    detection, and that either tier's availability could be inferred from the
    other's age. They are separate tables on purpose, and both readers must
    actually go to their own."""
    tier1 = FakeDb(rows=[[]])
    read_status(tier1)
    tier2 = FakeDb(rows=[[]])
    read_tier2_status(tier2)
    assert TIER2_TABLE in str(tier2.calls[0][1])
    assert TIER2_TABLE not in str(tier1.calls[0][1])
    assert HEARTBEAT_TABLE in str(tier1.calls[0][1])


def test_tier2_install_instructions_both_name_the_readme():
    """Whichever half is missing, the reason has to name the step that fixes
    it. 'tier 2 unavailable' on its own is the message that sends an operator
    reading source code."""
    for reason in (TIER2.not_installed, TIER2.never_ran):
        assert "overlay/tier2/README.md" in reason


def test_each_action_names_the_tier_whose_script_consumes_it():
    """The split the two scripts' claim predicates implement: Tier 2 claims
    sync_character and nothing else, Tier 1 claims everything else. Both
    predicates are asserted in tests/test_overlay_artifact.py."""
    assert consumer_tier(Action.SYNC_CHARACTER) == "tier2"
    assert consumer_tier(Action.GIVE_ITEM) == "tier1"
    assert consumer_tier(Action.ADJUST_ZENY) == "tier1"
