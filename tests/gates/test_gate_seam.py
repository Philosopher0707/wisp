"""The gates, through the live engine: the pre-dispatch refusal, the result scrub, and the completion guard.

Unit tests of the layers cannot detect a caller that does not call them, so these drive a real turn through `AgentRuntime` and look at
what actually happened on disk and in the conversation the model was given.
"""

from __future__ import annotations

import json
import logging

import pytest

from tests.reliability.test_verification_evidence_adapter import _content_round, _mutate_then, _Provider, _run_turn, _tool_round
from wisp.infra.security import PermissionMode

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


def _text(turn) -> str:
    return json.dumps(turn.events, default=str)


@pytest.fixture(autouse=True)
def _gate_env(monkeypatch):
    for k in ("WISP_INVARIANT_GATES", "WISP_DEPENDENCY_LOCK", "WISP_GATE_WRITE_ROOTS"):
        monkeypatch.delenv(k, raising=False)


class TestPreDispatchRefusal:
    def test_an_irreversible_command_never_runs_even_when_the_human_approves(self, tmp_path):
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "keep.txt").write_text("x")
        turn = _run_turn(tmp_path, [_tool_round("run_bash", {"command": "git reset --hard"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="irr")
        assert "Blocked by the harness gate" in _text(turn) and "IRREVERSIBLE_GIT" in _text(turn)
        assert "POLICY_DENIED" in _text(turn)

    def test_a_write_outside_the_workspace_never_happens(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        escape = tmp_path / "escape.txt"
        turn = _run_turn(ws, [_tool_round("run_bash", {"command": "echo pwned > ../escape.txt"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="esc")
        assert not escape.exists()
        assert "OUTSIDE_WORKSPACE" in _text(turn)

    def test_a_write_tool_outside_the_workspace_is_refused(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        target = tmp_path / "outside.txt"
        turn = _run_turn(ws, [_tool_round("write_file", {"path": str(target), "content": "x"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="wt")
        assert not target.exists()
        assert "OUTSIDE_WORKSPACE" in _text(turn)

    def test_a_dependency_install_is_refused_while_locked(self, tmp_path):
        turn = _run_turn(tmp_path, [_tool_round("run_bash", {"command": "pip install requests"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="dep")
        assert "DEPENDENCY_LOCKED" in _text(turn)

    def test_a_manifest_write_is_refused_then_allowed_once_unlocked(self, tmp_path, monkeypatch):
        refused = _run_turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "package.json"), "content": "{}"}, "c0"), _content_round()],
                            mode=PermissionMode.ASK_ALL, approve_calls=True, sid="m1")
        assert not (tmp_path / "package.json").exists() and "MANIFEST_LOCKED" in _text(refused)
        monkeypatch.setenv("WISP_DEPENDENCY_LOCK", "unlocked")
        _run_turn(tmp_path, [_tool_round("write_file", {"path": str(tmp_path / "package.json"), "content": "{}"}, "c0"), _content_round()],
                  mode=PermissionMode.ASK_ALL, approve_calls=True, sid="m2")
        assert (tmp_path / "package.json").read_text() == "{}"

    def test_ordinary_work_still_runs(self, tmp_path):
        turn = _run_turn(tmp_path, [_tool_round("run_bash", {"command": "echo hello > made.txt"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="ok")
        assert (tmp_path / "made.txt").read_text().strip() == "hello"
        assert "Blocked by the harness gate" not in _text(turn)


class TestModes:
    def test_off_restores_the_old_behaviour(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("WISP_INVARIANT_GATES", "off")
        turn = _run_turn(ws, [_tool_round("run_bash", {"command": "echo x > ../off.txt"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="off")
        assert "Blocked by the harness gate" not in _text(turn)

    def test_observe_logs_but_does_not_block(self, tmp_path, monkeypatch, caplog):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("WISP_INVARIANT_GATES", "observe")
        with caplog.at_level(logging.WARNING):
            turn = _run_turn(ws, [_tool_round("run_bash", {"command": "echo x > ../observed.txt"}, "c0"), _content_round()],
                             mode=PermissionMode.ASK_ALL, approve_calls=True, sid="obs")
        assert "Blocked by the harness gate" not in _text(turn)
        assert any("invariant gate (observe only)" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("typo", ["enfroce", "ENFORCE ", "on", "true", "1", "yes", "strict", "disabled", ""])
    def test_g4_a_typo_never_weakens_the_policy(self, tmp_path, monkeypatch, typo):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("WISP_INVARIANT_GATES", typo)
        turn = _run_turn(ws, [_tool_round("run_bash", {"command": "echo x > ../typo.txt"}, "c0"), _content_round()],
                         mode=PermissionMode.ASK_ALL, approve_calls=True, sid="typo")
        assert not (tmp_path / "typo.txt").exists() and "OUTSIDE_WORKSPACE" in _text(turn)


class _CapturingProvider(_Provider):
    """Records the messages it is shown, i.e. what the model actually reads."""

    def __init__(self, rounds):
        super().__init__(rounds)
        self.seen: list[list[dict]] = []

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        yield from super().generate_stream_events(system_prompt, messages, tools)


class TestResultScrub:
    def _run(self, ws, command, sid):
        import tests.reliability.test_verification_evidence_adapter as adapter

        provider_holder: dict = {}
        original = adapter._Provider

        def _factory(rounds):
            p = _CapturingProvider(rounds)
            provider_holder["p"] = p
            return p

        adapter._Provider = _factory  # type: ignore[assignment]
        try:
            turn = adapter._run_turn(ws, [_tool_round("run_bash", {"command": command}, "c0"), _content_round()],
                                     mode=PermissionMode.ASK_ALL, approve_calls=True, sid=sid)
        finally:
            adapter._Provider = original  # type: ignore[assignment]
        return turn, provider_holder["p"]

    def test_a_secret_a_command_prints_never_reaches_the_model(self, tmp_path):
        secret = "AKIA" + "IOSFODNN7EXAMPLE"
        # The command only ASSEMBLES the secret, so the text of the command (which the model wrote) does not contain it.
        turn, provider = self._run(tmp_path, "printf 'found %s%s\\n' AKIA IOSFODNN7EXAMPLE", "scrub")
        assert secret not in json.dumps(provider.seen), "the model was shown the secret"
        assert "[REDACTED:aws-access-key]" in json.dumps(provider.seen)
        assert secret not in _text(turn)

    def test_the_scrub_is_recorded_on_the_event(self, tmp_path):
        secret = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        turn, _provider = self._run(tmp_path, "printf '%s%s\\n' gh p_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "scrub2")
        assert secret not in _text(turn)
        assert "scrubbed_secret_kinds" in _text(turn)

    def test_clean_output_is_untouched(self, tmp_path):
        turn, provider = self._run(tmp_path, "echo all good here", "clean")
        assert "all good here" in json.dumps(provider.seen)
        assert "REDACTED" not in json.dumps(provider.seen)


class TestCompletionGuard:
    def test_echo_no_longer_counts_as_verification(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "echo fine"}, sid="v1")
        assert turn.guard.wrote_code is True
        assert turn.guard.verify_ok_after_edit is None and turn.guard.resolved() is False

    def test_a_real_verifier_still_counts(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "python3 -m compileall -q ."}, sid="v2")
        assert turn.guard.verify_ok_after_edit is True and turn.guard.resolved() is True

    def test_a_masked_verifier_does_not_count(self, tmp_path):
        turn = _mutate_then(tmp_path, "run_bash", {"command": "python3 -m compileall -q . || true"}, sid="v3")
        assert turn.guard.verify_ok_after_edit is None

    def test_with_the_gates_off_the_old_behaviour_returns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_INVARIANT_GATES", "off")
        turn = _mutate_then(tmp_path, "run_bash", {"command": "echo fine"}, sid="v4")
        assert turn.guard.verify_ok_after_edit is True


def test_the_production_call_site_passes_the_command_to_the_guard():
    """A guard that is never given the command silently reverts to the old behaviour, so pin the call site itself."""
    import ast
    import pathlib

    src = pathlib.Path("wisp/core/stateless.py").read_text()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "note_tool_result"]
    assert len(calls) == 1, "note_tool_result must keep a single call site"
    assert "COMMAND_ARG" in src[src.index("g_args = "):src.index("guard.note_tool_result(")], "the call site must put the full command in the args"
