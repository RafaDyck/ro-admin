"""Unit tests for the agent-facing CLI. No server needed."""
import pytest

from ro_admin import cli
from ro_admin.cli import build_url, describe_body, is_json, main, parse_params, redact


def test_build_url_joins_base_and_path():
    assert build_url("http://localhost:8000", "/api/v1/logs/zeny") == \
        "http://localhost:8000/api/v1/logs/zeny"


def test_build_url_tolerates_trailing_and_missing_slashes():
    assert build_url("http://localhost:8000/", "api/v1/auth/me") == \
        "http://localhost:8000/api/v1/auth/me"


def test_build_url_adds_the_api_prefix_when_omitted():
    """Agents shorten paths. Accept /logs/zeny for /api/v1/logs/zeny."""
    assert build_url("http://x", "/logs/zeny") == "http://x/api/v1/logs/zeny"


def test_build_url_encodes_query_params():
    url = build_url("http://x", "/logs/timeline", {"char_id": 150002, "limit": 5})
    assert url.startswith("http://x/api/v1/logs/timeline?")
    assert "char_id=150002" in url and "limit=5" in url


def test_parse_params_splits_key_equals_value():
    assert parse_params(["char_id=1", "limit=5"]) == {"char_id": "1", "limit": "5"}


def test_parse_params_rejects_malformed_pairs():
    with pytest.raises(ValueError):
        parse_params(["char_id"])


def test_parse_params_keeps_equals_signs_in_the_value():
    assert parse_params(["q=a=b"]) == {"q": "a=b"}


def test_redact_hides_tokens_so_they_never_reach_a_transcript():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
    out = redact(text)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out
    assert "REDACTED" in out


def test_redact_leaves_ordinary_text_alone():
    assert redact("item 501 x3 via npc script") == "item 501 x3 via npc script"


# --------------------------------------------------------------------------
# A response that is not JSON.
#
# `roadmin get maps/prontera/cells` printed 122,304 bytes of gat cell types
# and exited 0. Types 0-6 are unprintable control characters, so the visible
# result was a blank line and a success -- which an agent following SKILL.md
# reads as "this map has no cells". A false negative shaped like a successful
# call is the failure this whole project exists to remove.
# --------------------------------------------------------------------------

# One byte per cell, all gat type 1 -- exactly what /cells sends, and every
# byte of it invisible in a terminal.
GAT_BYTES = bytes([1]) * 122304


@pytest.mark.parametrize("header", [
    "application/json",
    "application/json; charset=utf-8",
    "application/problem+json",
])
def test_is_json_accepts_the_json_content_types(header):
    assert is_json(header) is True


@pytest.mark.parametrize("header", ["application/octet-stream", "text/html", "", "text/plain"])
def test_is_json_rejects_everything_else(header):
    assert is_json(header) is False


def test_describe_body_says_how_many_bytes_arrived():
    """The count is the fix. "cannot display this" would still leave a reader
    unable to tell a full response from an empty one."""
    out = describe_body("http://x/cells", 200, GAT_BYTES, "application/octet-stream")
    assert "122304" in out
    assert "application/octet-stream" in out


def test_describe_body_shows_that_bytes_really_arrived():
    out = describe_body("http://x/cells", 200, GAT_BYTES, "application/octet-stream")
    assert "01 01 01" in out


def test_describe_body_says_so_when_the_body_is_actually_empty():
    """Zero bytes and 122,304 invisible bytes must not read the same. That
    they did is the entire defect."""
    empty = describe_body("http://x/y", 200, b"", "application/octet-stream")
    full = describe_body("http://x/y", 200, GAT_BYTES, "application/octet-stream")
    assert "0 bytes" in empty
    assert empty != full


def test_describe_body_names_curl_and_the_url_that_was_requested():
    out = describe_body("http://h/api/v1/maps/prontera/cells", 200, GAT_BYTES,
                        "application/octet-stream")
    assert "curl" in out
    assert "http://h/api/v1/maps/prontera/cells" in out


@pytest.fixture()
def cli_env(monkeypatch):
    monkeypatch.setenv("RO_ADMIN_TOKEN", "not-a-real-token")
    monkeypatch.setenv("RO_ADMIN_URL", "http://server")


def _serves(status, body, content_type):
    def _fake(url, token):
        return status, body, content_type
    return _fake


def test_a_binary_response_does_not_exit_zero(cli_env, monkeypatch):
    """The whole defect in one assertion. Exit 0 told a caller the bytes had
    been handed over when nothing had been."""
    monkeypatch.setattr(cli, "_request",
                        _serves(200, GAT_BYTES, "application/octet-stream"))
    assert main(["get", "maps/prontera/cells"]) != 0


def test_a_binary_response_prints_something(cli_env, monkeypatch, capsys):
    """It printed nothing at all. Anything honest beats that."""
    monkeypatch.setattr(cli, "_request",
                        _serves(200, GAT_BYTES, "application/octet-stream"))
    main(["get", "maps/prontera/cells"])
    out = capsys.readouterr().out
    assert out.strip(), "printed nothing -- the original failure"
    assert "122304" in out


def test_a_binary_response_is_never_written_to_stdout(cli_env, monkeypatch, capsys):
    """Summarised, not dumped. 122,304 control characters in a transcript are
    unreadable at best and terminal-corrupting at worst."""
    monkeypatch.setattr(cli, "_request",
                        _serves(200, GAT_BYTES, "application/octet-stream"))
    main(["get", "maps/prontera/cells"])
    out = capsys.readouterr().out
    assert chr(1) not in out, "the raw gat bytes reached stdout"
    assert len(out) < 1000


def test_json_responses_are_still_printed_and_still_exit_zero(cli_env, monkeypatch, capsys):
    """The fix must not cost the normal path."""
    monkeypatch.setattr(cli, "_request",
                        _serves(200, b'{"name":"prontera","width":312}', "application/json"))
    assert main(["get", "maps/prontera"]) == 0
    out = capsys.readouterr().out
    assert '"name": "prontera"' in out


def test_a_json_error_body_is_still_printed_and_still_exits_one(cli_env, monkeypatch, capsys):
    """A 503 from a map endpoint carries the reason an operator needs, so it
    has to reach them."""
    monkeypatch.setattr(cli, "_request", _serves(
        503,
        b'{"detail":"maps not imported: run importers/maps_schema.sql, '
        b'then importers/import_maps.py"}',
        "application/json",
    ))
    assert main(["get", "maps"]) == 1
    assert "importers/import_maps.py" in capsys.readouterr().out


def test_a_token_in_a_summarised_url_is_still_redacted(cli_env, monkeypatch, capsys):
    """redact() applies to everything this tool prints, and the new path is
    not an exception."""
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
    monkeypatch.setattr(cli, "_request",
                        _serves(200, GAT_BYTES, "application/octet-stream"))
    main(["get", "maps/x/cells", f"t={jwt}"])
    assert jwt not in capsys.readouterr().out
