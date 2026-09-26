"""The skill's packaging, checked as data. No API needed.

Skill content loads into context when the skill triggers, so a 943-line entry
point charges every invocation -- including one that only reads a log -- for
the maps gat table and the Tier 1 polling protocol.
"""
import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "skill"
ENTRY = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"


def test_the_entry_point_is_small_enough_to_always_load():
    lines = ENTRY.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 200, (
        f"SKILL.md is {len(lines)} lines; it is loaded on every trigger"
    )


def test_every_reference_the_entry_point_names_exists():
    """A skill routing to a file that is not there sends an agent looking for
    guidance it will not find, and it has no way to tell that from guidance
    that says nothing."""
    text = ENTRY.read_text(encoding="utf-8")
    named = set(re.findall(r"references/([a-z0-9_-]+\.md)", text))
    assert named, "the entry point routes nowhere"
    missing = {n for n in named if not (REFERENCES / n).is_file()}
    assert not missing, f"named but absent: {missing}"


def test_every_reference_file_is_routed_to():
    """An orphan reference is content the agent will never be told to read."""
    text = ENTRY.read_text(encoding="utf-8")
    named = set(re.findall(r"references/([a-z0-9_-]+\.md)", text))
    on_disk = {p.name for p in REFERENCES.glob("*.md")}
    assert not (on_disk - named), f"unreachable: {on_disk - named}"


def test_the_frontmatter_survived_the_split():
    """The description is what makes the skill trigger at all."""
    text = ENTRY.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: ro-admin" in text
    assert "description:" in text


def test_the_rules_that_apply_everywhere_stay_in_the_entry_point():
    """Routing must not defer the things that are true regardless of which
    surface is in play -- an agent that reads only the entry point still has
    to know not to report an unobserved outcome."""
    text = ENTRY.read_text(encoding="utf-8").lower()
    assert "discover" in text
    assert "observed" in text or "never claim" in text


@pytest.mark.parametrize("name", ["forensics", "entities", "items", "maps", "tier1"])
def test_each_reference_is_substantial(name):
    """Guards against a split that routes to stubs."""
    path = REFERENCES / f"{name}.md"
    assert path.is_file()
    assert len(path.read_text(encoding="utf-8").splitlines()) > 40
