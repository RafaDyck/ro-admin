"""Unit tests for the agent-facing CLI. No server needed."""
import pytest

from ro_admin.cli import build_url, parse_params, redact


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
