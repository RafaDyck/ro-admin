"""Static checks on the shipped NPC script.

The predecessor's executor interpolated a character NAME from the web tier
into SQL in thirteen places. No test could have caught that, because there
was no test that read the script at all. These do.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
OVERLAY = _ROOT / "overlay" / "ro_admin_overlay.txt"
TIER2 = _ROOT / "overlay" / "tier2" / "ro_admin_tier2.txt"

# The one action Tier 2 owns. Both scripts' claim predicates are written
# against this name, in opposite senses, and the tests below check that the
# two senses stay complements.
TIER2_ACTION = "sync_character"


@pytest.fixture(scope="module")
def source() -> str:
    return OVERLAY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tier2_source() -> str:
    return TIER2.read_text(encoding="utf-8")


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


def _sql_string_offenders(source: str) -> list[tuple[str, str]]:
    """Every `$` variable concatenated into SQL that is not allowed by name.

    rAthena string variables end in `$`. Any `$` variable joined to a SQL
    string with `+` is a potential injection. Two are allowed by name --
    `.@err$` and `.version$` -- because in both overlays they hold only
    literals defined in that same file. Everything else is a defect.
    """
    allowed = {".@err$", ".version$"}
    offenders = []
    for line in _code_lines(source):
        if "query_sql" not in line and not line.strip().startswith("+"):
            continue
        for var in re.findall(r"[.@]{1,2}[A-Za-z_][A-Za-z0-9_]*\$", line):
            if var not in allowed:
                offenders.append((var, line.strip()))
    return offenders


def _claim_select(source: str) -> str:
    """The claim SELECT with its `" + "` concatenation seams closed up.

    Both overlays build that statement across two source lines, so neither
    predicate can be found by searching a single line. Returns one string in
    which the whole WHERE clause is contiguous.
    """
    code = "\n".join(_code_lines(source))
    start = code.index("SELECT id FROM ro_admin_commands")
    end = code.index(".@row;", start)
    return re.sub(r'"\s*\+\s*"', "", code[start:end])


def test_no_string_variable_is_concatenated_into_sql(source):
    """The injection invariant, on the Tier 1 artifact."""
    offenders = _sql_string_offenders(source)
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


# ---------------------------------------------------------------------------
# The queue split between the two overlays.
#
# Tier 1 and Tier 2 poll the SAME table. Two consumers on one queue is the
# defect this project was built to remove -- the predecessor ran two of them,
# the impostor won most of the races, and rows came back 'executed' while
# nothing had reached the game. The split is therefore written as two
# complementary predicates, and both halves are checked here: an invariant
# asserted in a header comment and checked nowhere is what this file exists
# to prevent.
# ---------------------------------------------------------------------------


def test_tier1_refuses_the_action_tier2_owns(source):
    claim = _claim_select(source)
    assert f"action <> '{TIER2_ACTION}'" in claim, (
        "the Tier 1 claim SELECT must exclude the action Tier 2 owns, or both "
        "scripts race for those rows and Tier 1 fails them as 'unknown action'"
    )
    assert f"action = '{TIER2_ACTION}'" not in claim


def test_tier2_claims_only_the_action_it_owns(tier2_source):
    claim = _claim_select(tier2_source)
    assert f"action = '{TIER2_ACTION}'" in claim, (
        "the Tier 2 claim SELECT must restrict itself to the action it owns"
    )
    assert "<>" not in claim, (
        "Tier 2's predicate must be the complement of Tier 1's, not another "
        "exclusion -- an exclusion on both sides leaves rows with no consumer"
    )


def test_tier2_never_reads_the_action_column_back(tier2_source):
    """Tier 2 has one action and no dispatch chain, so the claim predicate is
    the ONLY thing standing between it and flushing a character for a
    `give_item` row. If a `.@action$` branch ever appears here, this test
    should be replaced by one that checks the branch -- not deleted."""
    code = "\n".join(_code_lines(tier2_source))
    assert ".@action$" not in code


def test_every_api_action_has_exactly_one_consumer(source):
    """Neither script may be the consumer of an action, and neither may two.

    Tier 1 dispatches on `.@action$`; Tier 2 owns `TIER2_ACTION` and nothing
    else. Any action the API can queue must fall on exactly one side. An
    action added to the enum with no consumer would sit 'pending' forever;
    one claimed by both would be the two-consumer defect again.
    """
    from ro_admin.overlay import Action

    tier1 = set(re.findall(r'\.@action\$ == "([a-z_]+)"', source))
    assert TIER2_ACTION not in tier1, (
        f"{TIER2_ACTION} is dispatched by Tier 1 as well as claimed by Tier 2"
    )
    consumed = tier1 | {TIER2_ACTION}
    queueable = {a.value for a in Action}
    assert queueable <= consumed, (
        f"actions the API can queue that no overlay consumes: "
        f"{sorted(queueable - consumed)}"
    )


# ---------------------------------------------------------------------------
# The Tier 2 artifact. Its header claims the same invariants as Tier 1's, so
# they are checked the same way.
# ---------------------------------------------------------------------------


def test_tier2_artifact_exists(tier2_source):
    assert tier2_source, "overlay/tier2/ro_admin_tier2.txt is empty or missing"


def test_ui_error_matching_depends_on_this_exact_wording(source, tier2_source):
    """The web UI's actions.js has no view of these scripts at all -- it only
    ever sees `error_message` off a command row, and classifies a failed
    write by matching that string against two regexes in its
    `describeOutcome` (src/ro_admin/web/js/actions.js): `/not online/` reads
    a failure as a refusal ("Not applied: ..."), and, for a `sync_character`
    row specifically, `/not yet persisted/` reads it as an expected
    first-sync retry that offers a Retry button.

    Those patterns are meaningless unless the overlay scripts actually write
    text that matches them. Both scripts write "character is not online"
    (Tier 1's own .@err$, and Tier 2's -- a sync can fail the same way a
    write can); only Tier 2 writes "flush queued but not yet persisted -
    retry", since only sync_character has a first-write-not-committed-yet
    case. This pins the exact wording on the script side, so a rename here
    fails this test loudly instead of silently breaking the UI's
    classification.
    """
    assert "character is not online" in source, (
        "overlay/ro_admin_overlay.txt no longer writes the exact wording "
        "actions.js's describeOutcome matches with /not online/"
    )
    assert "character is not online" in tier2_source, (
        "overlay/tier2/ro_admin_tier2.txt no longer writes the exact wording "
        "actions.js's describeOutcome matches with /not online/"
    )
    assert "flush queued but not yet persisted - retry" in tier2_source, (
        "overlay/tier2/ro_admin_tier2.txt no longer writes the exact wording "
        "actions.js's describeOutcome matches with /not yet persisted/ to "
        "offer a Retry on a sync_character row"
    )


def test_tier2_concatenates_no_string_variable_into_sql(tier2_source):
    offenders = _sql_string_offenders(tier2_source)
    assert not offenders, f"string variable concatenated into SQL: {offenders}"


def test_tier2_rows_are_claimed_by_compare_and_swap(tier2_source):
    code = "\n".join(_code_lines(tier2_source))
    assert "AND status = 'pending'" in code
    assert "claimed_by = " in code


def test_tier2_heartbeat_is_written_before_any_early_return(tier2_source):
    """For Tier 2 the heartbeat is the tier detection itself: reaching that
    line proves the script parsed, which proves `ro_admin_sync` was a known
    buildin, which proves the hook is compiled. A tick that returned before
    writing it would look exactly like an uncompiled server."""
    code = "\n".join(_code_lines(tier2_source))
    assert code.index("ro_admin_tier2 (id") < code.index("goto L_Reschedule")


def test_tier2_poll_interval_matches_its_timer_label(tier2_source):
    poll = re.search(r"\.poll_ms = (\d+)", tier2_source)
    assert poll, "script does not declare .poll_ms"
    assert f"OnTimer{poll.group(1)}:" in tier2_source


def test_tier2_has_no_offline_fallback_and_writes_no_game_table(tier2_source):
    """Tier 2 reads `char` back to verify a flush. It must never write it --
    the whole point of the hook is that the GAME performs the write."""
    code = "\n".join(_code_lines(tier2_source))
    assert "INSERT INTO inventory" not in code
    assert "UPDATE `char`" not in code
    assert "UPDATE char " not in code


def test_tier2_verifies_the_flush_instead_of_trusting_the_hook(tier2_source):
    """The hook's return value is not a post-condition.

    `ro_admin_sync()` returning 1 means chrif_save handed packet 0x2b01 to the
    char server; the char server commits afterwards. Recording a sync time on
    that basis would let the API report a row as fresh before it was written.
    So the script must snapshot live memory BEFORE the call, read the stored
    row back AFTER it, compare, and write ro_admin_sync only if they match.

    This checks that concrete order. A version that dropped the snapshot,
    dropped the comparison, or wrote ro_admin_sync before comparing would
    fail here.
    """
    code = "\n".join(_code_lines(tier2_source))

    snapshot = code.index(".@live_zeny = Zeny")
    hook = code.index("ro_admin_sync()")
    read_back = code.index("SELECT zeny FROM `char`")
    compare = code.index(".@stored != .@live_zeny")
    record = code.index("REPLACE INTO ro_admin_sync")

    assert snapshot < hook, (
        "the live value must be snapshotted before the flush, or the "
        "comparison is against a value the flush itself produced"
    )
    assert hook < read_back < compare < record, (
        "the stored row must be read back and compared AFTER the hook and "
        "BEFORE ro_admin_sync is written"
    )
    err_after_compare = code[compare:record]
    assert ".@err$ =" in err_after_compare, (
        "the comparison exists but nothing records failure in .@err$"
    )


def test_tier2_detachrid_runs_exactly_once_on_every_attached_path(tier2_source):
    """attachrid without a matching detachrid pins that player's session to a
    script instance that has already finished; detaching twice on one path is
    the other half of the same bug.

    Tier 2's shape differs from Tier 1's deliberately: the hook is the last
    thing that needs a session, so the release is a single unconditional
    `detachrid` at the top level of the body, and every exit after it is
    already detached. This checks that shape:

      * exactly one top-level (single-tab) detachrid -- the release;
      * every exit between the attachrid call and the release, other than
        attachrid's own failure branch, detaches inside its own block;
      * no detachrid at or after the release except the release itself, so no
        path can detach twice;
      * the release precedes both the remaining exit and the fallthrough into
        L_Finish, which therefore need nothing of their own.

    This is not full control-flow analysis. If a new exit is added that does
    not fit this shape, read a failure here as "re-derive this test for the
    new shape", not "delete the assertion".
    """
    lines = _code_lines(tier2_source)

    top_level_release = [
        i for i, l in enumerate(lines) if l == "\tdetachrid;"
    ]
    assert len(top_level_release) == 1, (
        f"expected exactly one unconditional detachrid; found "
        f"{len(top_level_release)}"
    )
    release = top_level_release[0]

    attach = next(i for i, l in enumerate(lines) if "if (!attachrid(" in l)
    assert attach < release

    exits = [
        i for i, l in enumerate(lines)
        if "goto L_Finish" in l or "goto L_Reschedule" in l
    ]
    after_attach = [i for i in exits if i > attach]
    assert after_attach, "no exits at all after attachrid( -- did the shape change?"

    # The first exit after the attachrid call is attachrid's own failure
    # branch: nothing was attached, so there is nothing to release.
    inside_attached = [i for i in after_attach[1:] if i < release]
    assert inside_attached, (
        "expected at least one exit between the attach and the release -- "
        "did the hook's failure branch move?"
    )
    for idx in inside_attached:
        block = lines[max(0, idx - 3):idx]
        assert any(l.strip() == "detachrid;" for l in block), (
            f"exit {lines[idx].strip()!r} is inside the attached region and "
            f"has no detachrid within its own block"
        )

    extra = [
        i for i, l in enumerate(lines)
        if l.strip() == "detachrid;" and i >= release and i != release
    ]
    assert not extra, (
        f"detachrid after the unconditional release at line {release} -- "
        f"a path could detach twice: {[lines[i] for i in extra]}"
    )

    finish = next(i for i, l in enumerate(lines) if l.strip() == "L_Finish:")
    assert release < finish, (
        "the fallthrough into L_Finish is only safe because the release "
        "precedes it"
    )
