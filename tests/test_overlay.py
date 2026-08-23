"""Unit tests for the overlay module. No database, no game server."""
from datetime import datetime, timedelta

import pytest

from ro_admin.overlay import (
    OVERLAY_VERSION, Action, InvalidCommand, OverlayStatus, classify_heartbeat, validate,
)
from ro_admin.overlay import enqueue, read_command, read_status


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
    status = classify_heartbeat(tables_present=False, row=None, now=datetime(2026, 8, 23))
    assert status.installed is False
    assert status.responding is False
    assert "schema.sql" in status.reason


def test_tables_without_a_heartbeat_row_report_never_run():
    status = classify_heartbeat(tables_present=True, row=None, now=datetime(2026, 8, 23))
    assert status.installed is True
    assert status.responding is False
    assert "never" in status.reason.lower()


def test_a_fresh_heartbeat_is_responding():
    row, now = _row(age_seconds=1)
    status = classify_heartbeat(tables_present=True, row=row, now=now)
    assert status.responding is True
    assert status.instance_id == 1755950000


def test_a_stale_heartbeat_is_not_responding_and_says_how_stale():
    row, now = _row(age_seconds=47)
    status = classify_heartbeat(tables_present=True, row=row, now=now)
    assert status.responding is False
    assert "47" in status.reason


def test_staleness_threshold_scales_with_the_scripts_own_poll_interval():
    """A server configured to poll slowly must not be called stale for
    honouring its own configuration. The threshold is derived from the
    heartbeat itself, not hardcoded against one lab's timing."""
    row, now = _row(age_seconds=20, poll_ms=10_000)
    assert classify_heartbeat(tables_present=True, row=row, now=now).responding is True


def test_a_version_mismatch_is_reported_even_though_the_script_is_alive():
    """Responding but incompatible. Silently treating this as available is how
    an operator ends up debugging a contract change at 2am."""
    row, now = _row(age_seconds=1, version="0")
    status = classify_heartbeat(tables_present=True, row=row, now=now)
    assert status.responding is True
    assert status.compatible is False
    assert "0" in status.reason and OVERLAY_VERSION in status.reason


def test_a_future_heartbeat_does_not_produce_a_negative_age():
    """Clock skew between the API host and the database. Age clamps at zero
    rather than reporting '-4 seconds ago'."""
    row, now = _row(age_seconds=-4)
    status = classify_heartbeat(tables_present=True, row=row, now=now)
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
