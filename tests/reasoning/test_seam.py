"""The reasoning core through the live engine (RC4, RC6, RC12): the seams are reached by a real turn, `observe` changes nothing the user sees,
and a broken core can never fail a turn."""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from tests.reliability.test_verification_evidence_adapter import _content_round, _run_turn, _tool_round
from wisp.core.reasoning import runtime as rt
from wisp.infra.security import PermissionMode

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("WISP_REASONING_CORE", "WISP_INVARIANT_GATES", "WISP_DEPENDENCY_LOCK"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def cores(monkeypatch):
    made: list[rt.TurnReasoning] = []
    orig = rt.TurnReasoning

    class Capturing(orig):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)

    monkeypatch.setattr(rt, "TurnReasoning", Capturing)
    return made


def _turn(ws, rounds, sid):
    return _run_turn(ws, rounds, mode=PermissionMode.ASK_ALL, approve_calls=True, sid=sid)


def _final_text(turn) -> str:
    return "".join(str(e.get("text", "")) for e in turn.events if e.get("type") == "content")


class TestDefaultEnforces:
    """The owner's 2026-10-07 decision, witnessed through a real turn: with nothing set, R1 and R4 are enforced and the rest observe."""

    def test_with_nothing_set_a_real_turn_withholds_an_unbacked_claim_once(self, tmp_path, cores, monkeypatch):
        monkeypatch.delenv("WISP_REASONING_CORE_RULES", raising=False)
        monkeypatch.setattr("wisp.config.load_config", lambda: {})
        _turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("All tests pass."), _content_round("All tests pass.")], "e1")
        modes = cores[0].modes
        assert (modes.for_rule("R1").value, modes.for_rule("R4").value, modes.for_rule("R2").value) == ("enforce", "enforce", "observe")
        assert any(r.get("applied") and r["action"] == "withhold_done" for r in cores[0].journal)

    def test_the_kill_switch_restores_observe_through_a_real_turn(self, tmp_path, cores, monkeypatch):
        monkeypatch.setenv("WISP_REASONING_CORE", "observe")
        _turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("All tests pass.")], "e2")
        assert not any(r.get("applied") for r in cores[0].journal)


class TestObserve:
    def test_the_default_mode_is_observe_and_a_core_exists(self, tmp_path, cores):
        _turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("All tests pass.")], "d1")
        assert len(cores) == 1 and cores[0].mode.value == "observe"

    def test_the_ledger_sees_the_real_tool_results(self, tmp_path, cores):
        _turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("ok")], "d2")
        kinds = [(f.kind.value, f.subject) for f in cores[0].ledger.facts]
        assert ("file_mutation", "write_file") in kinds

    def test_an_unbacked_claim_is_journaled_but_the_answer_is_untouched(self, tmp_path, cores, monkeypatch):
        monkeypatch.setenv("WISP_REASONING_CORE", "observe")  # an explicit setting replaces the enforce default: this is the kill switch
        turn = _turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("All tests pass.")], "d3")
        rows = [r for r in cores[0].journal if r["seam"] == "final"]
        assert rows and rows[0]["action"] == "annotate_final" and rows[0]["claims"][0]["verdict"] == "unsupported"
        assert "the completion claim above is" not in json.dumps(turn.events, default=str)  # RC4: no R1 note leaks in observe (the floor's own UNVERIFIED note is not the core's)

    def test_a_shell_edit_is_a_mutation(self, tmp_path, cores):
        _turn(tmp_path, [_tool_round("run_bash", {"command": "echo hi > made.txt"}, "c0"), _content_round("ok")], "d4")
        assert cores[0].ledger.mutation_count == 1

    def test_a_gate_refusal_is_a_denied_fact(self, tmp_path, cores):
        _turn(tmp_path, [_tool_round("run_bash", {"command": "git reset --hard"}, "c0"), _content_round("ok")], "d5")
        assert [f.kind.value for f in cores[0].ledger.facts] == ["gate_decision"]

    def test_observe_is_byte_identical_to_off_for_the_user(self, tmp_path, monkeypatch):
        rounds = lambda: [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("All tests pass.")]  # noqa: E731
        monkeypatch.setenv("WISP_REASONING_CORE", "off")
        off = _turn(tmp_path, rounds(), "same")
        monkeypatch.setenv("WISP_REASONING_CORE", "observe")
        obs = _turn(tmp_path, rounds(), "same2")
        strip = lambda t: [(e.get("type"), e.get("name"), e.get("text")) for e in t.events]  # noqa: E731
        assert strip(off) == strip(obs)


class TestModes:
    def test_off_creates_no_core(self, tmp_path, cores, monkeypatch):
        monkeypatch.setenv("WISP_REASONING_CORE", "off")
        _turn(tmp_path, [_content_round("hello")], "m1")
        assert cores == []

    @pytest.mark.parametrize("typo", ["enfroce", "on", "true", "1", "", "ENFORCE"])
    def test_rc13_a_typo_is_never_more_intrusive_than_observe(self, tmp_path, cores, monkeypatch, typo):
        monkeypatch.setenv("WISP_REASONING_CORE", typo)
        turn = _turn(tmp_path, [_content_round("hello")], "t" + str(abs(hash(typo)) % 9999))
        assert all(c.mode.value in ("observe", "enforce") for c in cores)
        if typo.strip().lower() != "enforce":
            assert all(c.mode.value == "observe" for c in cores)
        assert "Harness note" not in json.dumps(turn.events, default=str)


class TestRC6TheCoreCannotFailATurn:
    def test_a_broken_ledger_degrades_to_a_core_error_row(self, tmp_path, cores, monkeypatch):
        def boom(self, ev):
            raise RuntimeError("ledger exploded")

        rounds = lambda: [_tool_round("write_file", {"path": str(tmp_path / "a.txt"), "content": "x"}, "c0"), _content_round("done")]  # noqa: E731
        monkeypatch.setenv("WISP_REASONING_CORE", "off")
        baseline = _final_text(_turn(tmp_path, rounds(), "e0"))  # the verification floor re-asks after an unverified edit; the core must not change that
        monkeypatch.setenv("WISP_REASONING_CORE", "observe")
        monkeypatch.setattr(rt.Ledger, "observe", boom)
        turn = _turn(tmp_path, rounds(), "e1")
        assert (tmp_path / "a.txt").read_text() == "x"
        assert _final_text(turn) == baseline
        assert any(r["action"] == "core_error" for r in cores[0].journal)

    def test_a_broken_audit_degrades_too(self, tmp_path, cores, monkeypatch):
        monkeypatch.setattr(rt, "audit", lambda *a, **k: (_ for _ in ()).throw(ValueError("audit exploded")))
        turn = _turn(tmp_path, [_content_round("All tests pass.")], "e2")
        assert _final_text(turn).strip() == "All tests pass."
        assert any(r["action"] == "core_error" and r["seam"] == "final" for r in cores[0].journal)


class TestRC12OneCallSitePerSeam:
    """A second path that bypasses the core would be invisible to the ledger. Count the call sites in the engine."""

    SRC = pathlib.Path("wisp/core/stateless.py")

    def _calls(self, attr: str) -> int:
        n = 0
        for node in ast.walk(ast.parse(self.SRC.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr and isinstance(node.func.value, ast.Name) and node.func.value.id == "reasoning":
                n += 1
        return n

    @pytest.mark.parametrize("attr", ["observe_tool_result", "observe_refusal", "observe_final", "observe_provider_error"])
    def test_exactly_one(self, attr):
        assert self._calls(attr) == 1
