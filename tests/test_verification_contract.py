"""The verification contract: the shell success encoding, pinned end to end.

Context (see PHASE_CANONICAL_CONTRACT_FREEZE.md C5 / F15, F16):

The completion gate decided "was this turn verified?" with

    verify_ok_after_edit = not result_text.startswith("[exit code:")

which is correct ONLY because `tools/bash.py::_format_bash_output` emits the
`[exit code: N]` prefix exclusively on a non-zero exit. That dependency was
undocumented, so a change to the formatter would have silently inverted the
gate — every successful verification would read as unverified, and every
failure would read as verified.

These tests pin BOTH sides together by driving the real formatter into the
real guard. If someone changes the output format, these fail.
"""

from __future__ import annotations

import pytest

from wisp.core.verification import VerificationFloorGuard, _verify_result_is_success
from wisp.tools.bash import _format_bash_output


def _guard_after(returncode: int, stdout: str = "", stderr: str = "") -> VerificationFloorGuard:
    """Drive the REAL formatter into the REAL guard, as production does."""
    text = _format_bash_output(returncode, stdout, stderr)
    g = VerificationFloorGuard(enabled=True)
    g.note_tool_result("write_file", "ok", {"path": "a.py"})   # mutate
    g.note_tool_result("run_bash", text, {})                   # verify
    return g


# ── The encoding contract itself ─────────────────────────────────────

def test_success_output_carries_no_prefix():
    """If this fails, the gate's premise is broken."""
    assert not _format_bash_output(0, "3 passed in 1.2s", "").startswith("[exit code:")
    assert not _format_bash_output(0, "", "").startswith("[exit code:")


@pytest.mark.parametrize("rc", [1, 2, 127, 130])
def test_failure_output_carries_the_prefix(rc):
    assert _format_bash_output(rc, "boom", "").startswith("[exit code:")


# ── The gate agrees with the encoding ────────────────────────────────

def test_green_run_verifies_the_turn():
    g = _guard_after(0, "3 passed in 1.2s")
    assert g.verify_ok_after_edit is True
    assert g.resolved() is True
    assert g.rejection() is None


def test_silent_green_run_verifies_the_turn():
    g = _guard_after(0, "")
    assert g.verify_ok_after_edit is True
    assert g.resolved() is True


@pytest.mark.parametrize("rc", [1, 2, 127])
def test_red_run_never_verifies_the_turn(rc):
    g = _guard_after(rc, "1 failed", "AssertionError")
    assert g.verify_ok_after_edit is False
    assert g.resolved() is False
    assert g.rejection() is not None, "a red verification must block completion"


def test_mutation_after_verification_invalidates_it():
    g = _guard_after(0, "all green")
    assert g.verify_ok_after_edit is True
    g.note_tool_result("edit_file", "ok", {"path": "a.py"})
    assert g.verify_ok_after_edit is None, (
        "evidence predating the last mutation must not count"
    )


# ── The false negative that used to exist (F16) ──────────────────────

def test_literal_zero_prefix_is_success_not_failure():
    """A successful command echoing "[exit code: 0]" is still a success.

    The formatter never emits a zero code, so this text can only come from the
    command's own stdout. Prefix-matching read it as a failure; parsing the
    code does not.
    """
    text = _format_bash_output(0, "[exit code: 0] echoed by the command", "")
    assert _verify_result_is_success(text) is True

    g = _guard_after(0, "[exit code: 0] echoed by the command", "")
    assert g.resolved() is True


def test_literal_nonzero_prefix_still_fails():
    """Parsing must not weaken the gate: a real non-zero code still fails."""
    assert _verify_result_is_success("[exit code: 7]\nboom") is False
    assert _verify_result_is_success("[exit code: 1]\n") is False


def test_arbitrary_output_without_prefix_is_success():
    assert _verify_result_is_success("") is True
    assert _verify_result_is_success("command finished") is True
