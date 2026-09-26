"""The CLI's write verb.

The product's thesis is that an agent drives the public API. Until now the
shipped client could not perform the one action that changes anything, so the
skill told the agent to hand-assemble curl with a bearer token in it.
"""
import json

import pytest

from ro_admin import cli


def test_post_sends_a_json_body(monkeypatch):
    captured = {}

    def fake_request(url, token, method="GET", body=None):
        captured.update(url=url, token=token, method=method, body=body)
        return 202, b'{"id": 1, "status": "pending"}', "application/json"

    monkeypatch.setattr(cli, "_request", fake_request)
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "t")

    assert cli.main(["post", "commands", "char_id=1", "action=give_item",
                     "item_id=909", "amount=3"]) == 0
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {
        "char_id": 1, "action": "give_item", "item_id": 909, "amount": 3
    }


def test_numbers_are_sent_as_numbers_not_strings(monkeypatch):
    """`char_id=1` on a command line is the string "1". The API's models are
    typed, so sending a string where an int belongs is a 422 that looks like
    the caller's fault."""
    captured = {}
    monkeypatch.setattr(cli, "_request", lambda url, token, method="GET", body=None:
                        (captured.update(body=body), (202, b"{}", "application/json"))[1])
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "t")

    cli.main(["post", "commands", "char_id=150002", "delta=-500"])
    body = json.loads(captured["body"])
    assert body["char_id"] == 150002 and isinstance(body["char_id"], int)
    assert body["delta"] == -500 and isinstance(body["delta"], int)


def test_a_non_numeric_value_stays_a_string(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_request", lambda url, token, method="GET", body=None:
                        (captured.update(body=body), (202, b"{}", "application/json"))[1])
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "t")

    cli.main(["post", "commands", "action=give_item"])
    assert json.loads(captured["body"])["action"] == "give_item"


def test_202_is_success_not_failure(monkeypatch):
    """The hazard. POST /api/v1/commands answers 202, and the existing `get`
    verb treats anything but 200 as an error -- so a naive write verb would
    report every successful enqueue as a failure."""
    monkeypatch.setattr(cli, "_request", lambda *a, **k:
                        (202, b'{"id": 41, "status": "pending"}', "application/json"))
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "t")
    assert cli.main(["post", "commands", "char_id=1"]) == 0


def test_a_409_is_reported_as_failure_with_its_detail(monkeypatch, capsys):
    """409 is what the API answers when the Tier 1 overlay is not responding,
    and its detail says what to do about it."""
    monkeypatch.setattr(cli, "_request", lambda *a, **k:
                        (409, b'{"detail": "overlay not installed: run overlay/schema.sql"}',
                         "application/json"))
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "t")
    assert cli.main(["post", "commands", "char_id=1"]) == 1
    assert "schema.sql" in capsys.readouterr().out


def test_post_without_a_token_refuses(monkeypatch):
    """A write with no credentials is a mistake worth catching before it
    becomes a 401 the caller has to interpret."""
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.delenv("RO_ADMIN_TOKEN", raising=False)
    assert cli.main(["post", "commands", "char_id=1"]) == 2


def test_the_token_is_never_printed(monkeypatch, capsys):
    """cli.redact exists because a token pasted into a terminal is a token in
    somebody's scrollback."""
    monkeypatch.setattr(cli, "_request", lambda *a, **k:
                        (202, b'{"ok": true}', "application/json"))
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "supersecrettokenvalue")
    cli.main(["post", "commands", "char_id=1"])
    assert "supersecrettokenvalue" not in capsys.readouterr().out


def test_get_still_works_unchanged(monkeypatch):
    """The write verb changes _request's signature. The read path must not
    regress -- it is what every other skill example uses."""
    monkeypatch.setattr(cli, "_request", lambda url, token, method="GET", body=None:
                        (200, b'{"items": []}', "application/json"))
    monkeypatch.setenv("RO_ADMIN_URL", "http://example:8000")
    monkeypatch.setenv("RO_ADMIN_TOKEN", "t")
    assert cli.main(["get", "logs/zeny", "char_id=1"]) == 0
