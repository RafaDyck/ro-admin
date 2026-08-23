"""Static checks on the shipped NPC script.

The predecessor's executor interpolated a character NAME from the web tier
into SQL in thirteen places. No test could have caught that, because there
was no test that read the script at all. These do.
"""
import re
from pathlib import Path

import pytest

OVERLAY = Path(__file__).resolve().parents[1] / "overlay" / "ro_admin_overlay.txt"


@pytest.fixture(scope="module")
def source() -> str:
    return OVERLAY.read_text(encoding="utf-8")


def _code_lines(source: str) -> list[str]:
    """Script lines with `//` comments stripped, so prose about SQL is not
    mistaken for SQL."""
    out = []
    for line in source.splitlines():
        stripped = line.split("//", 1)[0]
        if stripped.strip():
            out.append(stripped)
    return out


def test_overlay_artifact_exists(source):
    assert source, "overlay/ro_admin_overlay.txt is empty or missing"


def test_no_string_variable_is_concatenated_into_sql(source):
    """The injection invariant.

    rAthena string variables end in `$`. Any `$` variable joined to a SQL
    string with `+` is a potential injection. Two are allowed by name --
    `.@err$` and `.version$` -- because both hold only literals defined in
    this file. Everything else is a defect.
    """
    allowed = {".@err$", ".version$"}
    offenders = []
    for line in _code_lines(source):
        if "query_sql" not in line and not line.strip().startswith("+"):
            continue
        for var in re.findall(r"[.@]{1,2}[A-Za-z_][A-Za-z0-9_]*\$", line):
            if var not in allowed:
                offenders.append((var, line.strip()))
    assert not offenders, f"string variable concatenated into SQL: {offenders}"


def test_queue_is_never_read_by_character_name(source):
    """char_id is the only identifier the queue carries."""
    assert "character_name" not in source
    assert "WHERE name" not in source


def test_there_is_no_offline_fallback(source):
    """A direct write to inventory or char would produce no audit row.

    This is the specific defect that let the predecessor grant items with
    zero picklog rows and four duplicate inventory stacks.
    """
    code = "\n".join(_code_lines(source))
    assert "INSERT INTO inventory" not in code
    assert "UPDATE `char`" not in code
    assert "UPDATE char " not in code


def test_only_the_two_supported_actions_are_dispatched(source):
    """Keeps the script and the API's action registry from drifting apart."""
    dispatched = set(re.findall(r'\.@action\$ == "([a-z_]+)"', source))
    assert dispatched == {"give_item", "adjust_zeny"}


def test_rows_are_claimed_by_compare_and_swap(source):
    """The claim must be conditional on the row still being pending, and must
    stamp the claiming instance. Without both, a second consumer is invisible."""
    code = "\n".join(_code_lines(source))
    assert "AND status = 'pending'" in code
    assert "claimed_by = " in code


def test_heartbeat_is_written_before_any_early_return(source):
    """A tick that returns early on an empty queue must still have written the
    heartbeat, or an idle server would report Tier 1 as uninstalled."""
    code = "\n".join(_code_lines(source))
    heartbeat = code.index("ro_admin_overlay")
    first_goto = code.index("goto L_Reschedule")
    assert heartbeat < first_goto


def test_declared_version_matches_the_api(source):
    from ro_admin.overlay import OVERLAY_VERSION
    declared = re.search(r'\.version\$ = "([^"]+)"', source)
    assert declared, "script does not declare .version$"
    assert declared.group(1) == OVERLAY_VERSION


def test_poll_interval_matches_its_timer_label(source):
    """`.poll_ms` is reported to the API and used to judge heartbeat staleness.
    If it disagrees with the OnTimer label, the API's staleness threshold is
    calibrated against a lie."""
    poll = re.search(r"\.poll_ms = (\d+)", source)
    assert poll, "script does not declare .poll_ms"
    assert f"OnTimer{poll.group(1)}:" in source


def test_each_action_branch_verifies_its_own_postcondition(source):
    """The project's central rule: an outcome is only reported after it is
    observed. rAthena's script engine cannot report a failed getitem or
    atcommand back to the calling script (src/map/script.cpp:4136-4140), so
    a branch that skips the read-back has no other way to learn the action
    failed. Before this was added, a failed getitem (inventory full, bad
    item id) was recorded as 'executed' -- this guards against that
    regression reappearing in either branch.

    For each action this checks the concrete shape: a `.@before` snapshot
    taken BEFORE the mutating call (getitem / atcommand "@zeny"), and, after
    that call, a comparison against `.@before` that can set `.@err$`. A
    branch that dropped the snapshot, dropped the comparison, or moved the
    snapshot after the call would fail this.
    """
    code = "\n".join(_code_lines(source))
    mutating_call = {"give_item": "getitem", "adjust_zeny": 'atcommand "@zeny'}

    for action, call in mutating_call.items():
        marker = f'.@action$ == "{action}"'
        start = code.index(marker)
        end = code.index("} else", start)
        body = code[start:end]

        call_idx = body.index(call)
        before_idx = body.index(".@before")
        assert before_idx < call_idx, (
            f"{action}: .@before must be captured BEFORE the mutating call, "
            f"or the comparison is meaningless"
        )

        after = body[call_idx:]
        assert ".@before" in after and "!=" in after, (
            f"{action}: no post-condition comparison against .@before after "
            f"the mutating call"
        )
        assert ".@err$ =" in after, (
            f"{action}: the comparison exists but nothing records failure "
            f"in .@err$"
        )


def test_detachrid_covers_every_path_after_a_successful_attach(source):
    """attachrid without a matching detachrid on some exit pins that
    player's session to this script past the tick that attached it --
    their own commands would queue behind a script instance that already
    finished. The one exempt exit is attachrid's own failure branch:
    nothing was attached, so there is nothing to release.

    This is not full control-flow analysis; it checks the concrete shape
    this script currently has. The first `goto L_Finish` after the
    `attachrid(` call is the attach-failure exit (exempt). Every exit
    after that -- and the dispatch block's fallthrough into the
    `L_Finish:` label, which has no goto at all -- must have a `detachrid`
    within a few lines above it. If a new exit is added that does not fit
    this shape, this test should fail rather than pass by accident; if
    that happens, read the failure as "reconsider this test's shape",
    not "delete the assertion".
    """
    lines = _code_lines(source)

    attach_idx = next(
        i for i, l in enumerate(lines) if "if (!attachrid(" in l
    )
    exit_idxs = [
        i for i, l in enumerate(lines)
        if "goto L_Finish" in l or "goto L_Reschedule" in l
    ]
    after_attach_exits = [i for i in exit_idxs if i > attach_idx]
    assert after_attach_exits, "expected at least the attach-failure exit after attachrid("

    # The first exit after the attachrid( call is attachrid's own failure
    # branch -- nothing was attached yet, so it is exempt.
    later_exits = after_attach_exits[1:]
    assert later_exits, (
        "expected at least one exit from the attached region besides the "
        "attach-failure branch -- did the dispatch logic move?"
    )

    window = 3
    for idx in later_exits:
        preceding = lines[max(0, idx - window):idx]
        assert any(l.strip().startswith("detachrid") for l in preceding), (
            f"exit {lines[idx].strip()!r} has no detachrid within "
            f"{window} lines above it"
        )

    # The give_item / adjust_zeny branches have no goto at all -- they fall
    # through into the L_Finish label. That fallthrough must also detach.
    finish_idx = next(
        i for i, l in enumerate(lines) if l.strip().startswith("L_Finish:")
    )
    preceding = lines[max(0, finish_idx - window):finish_idx]
    assert any(l.strip().startswith("detachrid") for l in preceding), (
        "fallthrough into L_Finish has no detachrid within "
        f"{window} lines above it"
    )
