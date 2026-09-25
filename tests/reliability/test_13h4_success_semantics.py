"""Phase 13-H4: turn-success semantics & replay integrity forensics.

AUDIT ONLY. No production file modified. Every test drives REAL
AgentRuntime.run_turn / SessionRepository / HeadlessTransport with scripted
fakes and pins OBSERVED behavior — including contradictions, which are
asserted exactly (they PASS by documenting).

Success-flag data flow (traced, not grepped):
  run_turn sets turn_succeeded=True on ANY generator exhaustion
    (wisp/core/runtime.py:463); its sole consumer gates the repo-DONE marker
    (runtime.py:588). The flag itself is never persisted; telemetry has no
    outcome dimension; downstream consumers (headless ok, CLI, server, SDK,
    benchmark) derive success from EVENTS, never the flag.
"""

from __future__ import annotations

import asyncio
import inspect
import threading

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session_repo import SessionRepository
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry
from wisp.providers.mock import MockProvider
from wisp.transport.headless import HeadlessTransport


async def _allow(ev) -> bool:
    return True


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


class _DictProvider:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        script = self._rounds[min(self.calls - 1, len(self._rounds) - 1)]
        yield from script()


def _content_only(text, reason="stop"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": reason}
    return _g


def _tool_round(calls):
    def _g():
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _think_then_hang(thinking):
    def _g():
        yield {"type": "thinking", "text": thinking}
        threading.Event().wait(30)
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _hang():
    def _g():
        threading.Event().wait(30)
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _garbage():
    def _g():
        yield object()
    return _g


def _empty_done():
    def _g():
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _runtime(provider, tmp_path, ws_files=None, **cfg_kw):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    for name, text in (ws_files or {}).items():
        (ws / name).write_text(text)
    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)

    def factory():
        c = WispAgentCore(config=config, provider=provider,
                          security=SecurityPolicy(), tool_executor=None)
        c.FIRST_TOKEN_DEADLINE_S = 0.2
        c.CHUNK_DEADLINE_S = 0.2
        return c

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory,
        session_repo=repo, config=config)
    return runtime, repo, str(ws)


def _session(ws, sid="h4"):
    return {"id": sid, "model": "mock", "workspace": ws, "messages": []}


def _run_turn(runtime, session, prompt="go"):
    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt=prompt)]
    return asyncio.run(_main())


def _types(evs):
    return [e.get("type") for e in evs]


def _repo_types(repo, sid):
    return [str(e.event_type) for e in repo.load_events(sid)]


# ── §5/§18. Assignment enumeration + H1-claim verification ────────────

class TestAssignmentsAndH1Claim:
    def test_completion_derives_from_terminal_evidence(self):
        # H5 compatibility: the unconditional-True assignment H4 pinned is
        # gone; the single decision point derives from terminal evidence.
        #
        # UPDATED by ADR-0044 (PM-24): the decision point now derives the
        # turn-level flag FROM the terminal outcome instead of re-implementing
        # the predicate. The expression this test used to pin
        # (`turn_succeeded = saw_done and not saw_fatal_error`) was a SECOND
        # implementation of the same rule that `terminal_outcome_from_evidence`
        # owns — and `derive_goal_state` reads both, so the two could have
        # disagreed about one turn. This test asserts the single-predicate form.
        import pathlib
        src = pathlib.Path("wisp/core/runtime.py").read_text()
        assert "turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED" in src
        assert "turn_succeeded = saw_done and not saw_fatal_error" not in src, (
            "the turn-success predicate was re-implemented (ADR-0044 R2)")
        assert "if turn_succeeded:" in src
        assert "SessionEvent.error(seq_num, terminal_error)" in src

    def test_no_other_producer_in_tree(self):
        """One **producer** of the turn-success rule, and only one.

        Refined from a whole-file substring search to an AST check over
        assignment targets. The substring form also caught *readers*, which is
        not what it meant to forbid: ADR-0035's goal arbiter
        (`wisp/core/goal.py`) consumes the turn-success fact as an input without
        producing it, and a guard that cannot tell those apart forces the code to
        stop naming the thing — the P8 trap in reverse. Same refinement M12 made
        to the denial-marker guard, and it is strictly stronger: a *reader* is
        now explicitly permitted while the producer stays pinned.
        """
        import ast
        import pathlib

        producers: list[str] = []
        for p in pathlib.Path("wisp").rglob("*.py"):
            for node in ast.walk(ast.parse(p.read_text())):
                targets: list = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "turn_succeeded":
                        producers.append(str(p))
                        break

        assert sorted(set(producers)) == ["wisp/core/runtime.py"], (
            "the turn-success rule gained a second producer: "
            f"{sorted(set(producers))}")
        # Non-vacuity: a rename must not make this pass by finding nothing.
        assert producers, "the AST check found no producer — it is vacuous"


# ── §6. Outcome matrix A–J (runtime level, repo-backed) ───────────────

class TestOutcomeMatrix:
    def _row(self, tmp_path, provider, sid, monkeypatch=None):
        if monkeypatch is not None:
            monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(provider, tmp_path)
        session = _session(ws, sid)
        evs = _run_turn(runtime, session)
        return evs, runtime, repo, session

    def test_a_clean_success(self, tmp_path):
        evs, _, repo, session = self._row(
            tmp_path, _DictProvider([_content_only("answer")]), "a")
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("a") is True
        # Migration P0: the log now also journals the assistant turn body, so
        # replay can reconstruct the turn. Before P0 it held only
        # user_message/error/done and `load_session()` returned a
        # user-message-only transcript.
        assert _repo_types(repo, "a") == [
            "user_message", "assistant_message", "done"]
        assistant = [e for e in repo.load_events("a")
                     if str(e.event_type) == "assistant_message"]
        assert assistant[0].payload["content"] == "answer"

    def test_b_h3_failure_repo_incomplete_derived(self, tmp_path, monkeypatch):
        # H5 compatibility: terminal error now derives repo-incomplete, and
        # the failure is recorded as an ERROR row (never DONE).
        evs, _, repo, _ = self._row(
            tmp_path, _DictProvider([_think_then_hang("craft")]), "b", monkeypatch)
        assert "done" not in _types(evs)
        assert _types(evs).count("error") == 1  # H3 terminal closure
        assert repo.was_last_turn_complete("b") is False
        assert _repo_types(repo, "b") == ["user_message", "error"]

    def test_c_timeout_repo_complete(self, tmp_path):
        runtime, repo, ws = _runtime(_DictProvider([_hang()]), tmp_path,
                                     turn_timeout=2)
        session = _session(ws, "c")
        evs = _run_turn(runtime, session)
        assert _types(evs).count("error") == 1
        assert _types(evs).count("done") == 1
        # H5 compatibility: fatal timeout error poisons the formal done.
        assert repo.was_last_turn_complete("c") is False
        assert _repo_types(repo, "c") == ["user_message", "error"]

    def test_d_cancel_repo_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("part")]), tmp_path)
        session = _session(ws, "d")

        async def _main():
            out: list = []
            gen = runtime.run_turn(session, prompt="go")
            task = asyncio.create_task(_drain(gen, out))
            while not out:
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return out

        async def _drain(gen, out):
            async for e in gen:
                out.append(e)

        evs = asyncio.run(_main())
        assert "done" not in _types(evs)
        # cancellation never reaches the exhaustion assignment …
        assert repo.was_last_turn_complete("d") is False
        assert _repo_types(repo, "d") == ["user_message"]

    def test_e_malformed_repo_incomplete(self, tmp_path, monkeypatch):
        evs, _, repo, _ = self._row(
            tmp_path, _DictProvider([_garbage()]), "e", monkeypatch)
        assert "done" not in _types(evs)
        # H5 compatibility: was repo-complete (H4 finding), now incomplete.
        assert repo.was_last_turn_complete("e") is False

    def test_f_empty_dict_repo_incomplete(self, tmp_path, monkeypatch):
        evs, _, repo, _ = self._row(
            tmp_path, _DictProvider([_empty_done()]), "f", monkeypatch)
        assert "done" not in _types(evs)
        # H5 compatibility: was repo-complete (H4 finding), now incomplete.
        assert repo.was_last_turn_complete("f") is False

    def test_f2_empty_object_repo_incomplete(self, tmp_path, monkeypatch):
        """REWRITTEN by ADR-0041 (PM-23).

        The old body asserted `_types(evs) == ["done"]  # bookkeeping-set
        mismatch (H2)` and `was_last_turn_complete is True` — i.e. it recorded
        F43 as the contract: a bare typed terminal counted as a meaningful
        response, so an empty stream was blessed as a completed turn.

        A bare terminal marker is now an empty attempt on both representations,
        so this row matches its dict twin (`test_f_empty_dict_repo_incomplete`).
        """
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(MockProvider(responses=[""]), tmp_path)
        session = _session(ws, "f2")
        evs = _run_turn(runtime, session)
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("f2") is False

    def test_g_iteration_exhaustion(self, tmp_path):
        prov = _DictProvider([
            _tool_round([_read_call("a.txt", "c0")]),
            _tool_round([_read_call("a.txt", "c1")]),
            _content_only("summary")])
        runtime, repo, ws = _runtime(
            prov, tmp_path, ws_files={"a.txt": "a"}, max_iterations=2)
        session = _session(ws, "g")
        evs = _run_turn(runtime, session)
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("g") is True

    def test_h_verification_then_finish(self, tmp_path):
        def _write(n):
            def _g():
                yield {"type": "tool_calls", "calls": [{
                    "id": f"w{n}", "type": "function",
                    "function": {"name": "write_file", "arguments": {
                        "path": f"o{n}.txt", "content": "x"}}}]}
                yield {"type": "done", "done_reason": "tool_calls"}
            return _g

        rounds = [_write(i) for i in range(5)] + [
            _content_only("d1"), _content_only("d2"), _content_only("ok")]
        runtime, repo, ws = _runtime(_DictProvider(rounds), tmp_path)
        session = _session(ws, "h")
        evs = _run_turn(runtime, session)
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("h") is True

    def test_i_content_plus_tool(self, tmp_path):
        def _both():
            def _g():
                yield {"type": "content", "text": "note"}
                yield {"type": "tool_calls", "calls": [_read_call("a.txt", "c0")]}
                yield {"type": "done", "done_reason": "tool_calls"}
            return _g

        runtime, repo, ws = _runtime(
            _DictProvider([_both(), _content_only("final")]), tmp_path,
            ws_files={"a.txt": "a"})
        session = _session(ws, "i")
        evs = _run_turn(runtime, session)
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("i") is True

    def test_j_retry_after_failure_new_attempt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("craft"),
                              _content_only("recovered")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "j")
        evs1 = _run_turn(runtime, session, prompt="first")
        evs2 = _run_turn(runtime, session, prompt="retry")
        assert "done" not in _types(evs1)  # attempt 1 failed …
        assert _types(evs2).count("done") == 1  # … attempt 2 succeeded
        assert prov.calls == 2  # retry is a NEW provider round, never suppressed
        # H5 compatibility: attempt 1 stays ERROR-marked (was DONE pre-fix).
        # Migration P0: attempt 2's assistant turn body is journaled too.
        assert _repo_types(repo, "j") == [
            "user_message", "error", "user_message", "assistant_message",
            "done"]
        errs = [e for e in repo.load_events("j")
                if str(e.event_type) == "error"]
        dones = [e for e in repo.load_events("j")
                 if str(e.event_type) == "done"]
        assert len(errs) == 1 and len(dones) == 1


# ── §7. Invariant directions ──────────────────────────────────────────

class TestSuccessInvariant:
    def test_forward_implication_holds(self, tmp_path, monkeypatch):
        # H5 compatibility: derivation repaired the forward direction —
        # repo-complete now implies a clean done with no fatal error.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        session = _session(ws, "inv-fwd")
        evs = _run_turn(runtime, session)
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("inv-fwd") is False

    def test_inverse_implication_holds(self, tmp_path):
        # every done-terminated turn in the matrix is repo-complete.
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("ok")]), tmp_path)
        session = _session(ws, "inv-inv")
        evs = _run_turn(runtime, session)
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("inv-inv") is True


# ── §8. H3 interaction ────────────────────────────────────────────────

class TestH3Interaction:
    def test_terminal_error_without_repo_success(self, tmp_path, monkeypatch):
        # H5 compatibility: the H4 contradiction is resolved — terminal
        # error coexists with repo-INCOMPLETE, never repo success.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        session = _session(ws, "h3x")
        evs = _run_turn(runtime, session)
        terminal_errors = [e for e in evs if e.get("type") == "error"]
        assert len(terminal_errors) == 1
        assert terminal_errors[-1] is evs[-1]  # closure is the last word …
        assert repo.was_last_turn_complete("h3x") is False  # … and repo agrees


# ── §10/§11. Resume + replay ──────────────────────────────────────────

class TestResumeAndReplay:
    def test_r1_success_resume_no_replay(self, tmp_path):
        prov = _DictProvider([_content_only("one"), _content_only("two")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "r1")
        _run_turn(runtime, session, prompt="first")
        assert repo.was_last_turn_complete("r1") is True
        evs2 = _run_turn(runtime, session, prompt="second")
        assert _types(evs2).count("done") == 1
        assert prov.calls == 2
        users = [m for m in session["messages"] if m.get("role") == "user"]
        assert [u["content"] for u in users] == ["first", "second"]

    def test_r2_failed_turn_resume_recovers(self, tmp_path, monkeypatch):
        # H5 compatibility: the failure is no longer DONE-marked, so resume
        # enters the crash-recovery branch (replays persisted prompts) and
        # then runs the new turn to completion.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("x"), _content_only("back")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "r2")
        _run_turn(runtime, session, prompt="first")
        assert repo.was_last_turn_complete("r2") is False
        evs2 = _run_turn(runtime, session, prompt="second")
        assert _types(evs2).count("done") == 1
        assert prov.calls == 2
        assert [m.get("content") for m in session["messages"]
                if m.get("role") == "user"] == ["first", "second"]

    def test_r4_cancelled_turn_replays_then_completes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("x"), _content_only("after")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "r4")

        async def _cancelled():
            out: list = []
            gen = runtime.run_turn(session, prompt="first")
            task = asyncio.create_task(_drain(gen, out))
            while not out:
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return out

        async def _drain(gen, out):
            async for e in gen:
                out.append(e)

        asyncio.run(_cancelled())
        assert repo.was_last_turn_complete("r4") is False
        # resume path replays persisted prompts, then runs the new turn fine.
        evs2 = _run_turn(runtime, session, prompt="second")
        assert _types(evs2).count("done") == 1
        assert repo.was_last_turn_complete("r4") is True

    def test_r6_manual_error_event_means_incomplete(self, tmp_path):
        from wisp.core.session import SessionEvent
        runtime, repo, ws = _runtime(_DictProvider([_content_only("ok")]), tmp_path)
        repo.append_event("r6", SessionEvent.user_message(0, "q"))
        repo.append_event("r6", SessionEvent.error(1, "boom", recoverable=True))
        assert repo.was_last_turn_complete("r6") is False
        session = _session(ws, "r6")
        evs = _run_turn(runtime, session, prompt="next")
        assert _types(evs).count("done") == 1

    def test_replay_log_distinguishes_failure(self, tmp_path, monkeypatch):
        # H5 compatibility: the log records the failure as an ERROR row, so
        # fail-then-success reads [user, error, user, assistant, done].
        # Failure is no longer DONE-shaped.
        #
        # Migration P0 closes the gap this docstring used to record
        # ("tool results still absent: deterministic replay remains NOT
        # ESTABLISHED"): the assistant body and tool events are now
        # journaled, so replay reconstructs the turn. The assertion below
        # checks that directly rather than asserting absence.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("x"), _content_only("ok")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "rp")
        _run_turn(runtime, session, prompt="first")
        _run_turn(runtime, session, prompt="second")
        log = repo.load_events("rp")
        assert [str(e.event_type) for e in log] == [
            "user_message", "error", "user_message", "assistant_message",
            "done"]
        assert not any("success" in e.payload or "outcome" in e.payload
                       for e in log)
        # Deterministic replay: the replayed transcript matches the live one.
        replayed = repo.load_session("rp")
        assert replayed is not None
        assert replayed.unknown_events == 0
        assert [(m["role"], m.get("content", "")) for m in replayed.messages] == \
               [(m["role"], m.get("content", "")) for m in session["messages"]]


# ── §12. Retry (G1E boundary: turn-level retry = new turn) ────────────

class TestRetryInteraction:
    def test_failed_turn_does_not_suppress_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("x"), _content_only("ok")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "rt")
        _run_turn(runtime, session, prompt="try")
        before = prov.calls
        evs = _run_turn(runtime, session, prompt="retry")
        assert prov.calls == before + 1
        assert _types(evs).count("done") == 1


# ── §13/§14. Consumers + observability ────────────────────────────────

class TestConsumersAndObservability:
    def test_headless_ok_derives_from_events_not_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, _, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        session = _session(ws, "co")

        async def _main():
            t = HeadlessTransport()
            t.start()
            async for ev in runtime.run_turn(session, prompt="go"):
                await t.send(ev)
            return t.collect_result()

        res = asyncio.run(_main())
        assert res["ok"] is False  # H3 closure error flips it …
        assert len(res["errors"]) == 1

    def test_headless_pre_closure_stream_would_read_ok(self):
        # the pre-H3 silent shape (status only, no error/done) reads successful
        # to every event-derived consumer — why the closure mattered.
        async def _main():
            t = HeadlessTransport()
            t.start()
            await t.send({"type": "thinking", "text": "craft"})
            await t.send({"type": "provider_status", "status": "chunk_stall"})
            return t.collect_result()

        res = asyncio.run(_main())
        assert res["ok"] is True

    def test_telemetry_counts_turns_without_outcome(self, tmp_path):
        from wisp.infra.telemetry import Telemetry
        assert "outcome" not in inspect.signature(Telemetry.record_turn).parameters
        assert "success" not in inspect.signature(Telemetry.record_turn).parameters
        runtime, _, ws = _runtime(_DictProvider([_content_only("ok")]), tmp_path)
        session = _session(ws, "tm")
        _run_turn(runtime, session)
        assert runtime.telemetry.turns_total == 1  # counted, outcome unknown


# ── §15/§16. Resurrection + attempt identity ──────────────────────────

class TestResurrectionAndIdentity:
    def test_t1_single_closure_error_row(self, tmp_path, monkeypatch):
        # H5 compatibility: the single closure is joined by a single repo
        # ERROR row; no DONE anywhere.
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        session = _session(ws, "t1")
        evs = _run_turn(runtime, session)
        assert _types(evs).count("error") == 1  # exactly one terminal …
        assert "done" not in _types(evs)  # … never followed by success …
        assert _repo_types(repo, "t1") == ["user_message", "error"]

    def test_t5_t6_history_immutable_across_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("x"), _content_only("ok")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "t56")
        _run_turn(runtime, session, prompt="first")
        before = [ (str(e.event_type), e.sequence_num) for e in repo.load_events("t56")]
        _run_turn(runtime, session, prompt="second")
        after = [(str(e.event_type), e.sequence_num) for e in repo.load_events("t56")]
        assert after[:len(before)] == before  # old rows untouched …
        assert [t for t, _ in after] == [
            "user_message", "error", "user_message", "assistant_message",
            "done"]  # failed stays failed


# ── §17. Adversarial combinations ─────────────────────────────────────

class TestContradictions:
    def test_save_failure_swallowed_repo_still_done(self, tmp_path):
        # persist order: repo-DONE (runtime.py:588) BEFORE store.save (603),
        # and the save sits inside try/except-log (602-605). A blob-save
        # failure is therefore invisible: no raise, repo still DONE.
        runtime, repo, ws = _runtime(_DictProvider([_content_only("ok")]), tmp_path)
        session = _session(ws, "fz")

        def _boom(sess):
            raise RuntimeError("disk gone")

        runtime.store.save_session = _boom  # type: ignore[method-assign]
        evs = _run_turn(runtime, session)  # does NOT raise
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("fz") is True
    def test_cancel_never_reports_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        session = _session(ws, "fzc")

        async def _main():
            out: list = []
            gen = runtime.run_turn(session, prompt="go")
            task = asyncio.create_task(_drain(gen, out))
            while not out:
                await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return out

        async def _drain(gen, out):
            async for e in gen:
                out.append(e)

        evs = asyncio.run(_main())
        assert "done" not in _types(evs)
        assert "error" not in _types(evs)
        assert repo.was_last_turn_complete("fzc") is False
