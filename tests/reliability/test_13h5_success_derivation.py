"""Phase 13-H5: success-derivation remediation gate (failing-first).

H4 CONFIRMED repo-DONE is written on every generator exhaustion regardless
of terminal outcome. H5 derives repository completion from terminal evidence:

    done seen, no fatal error  -> repo-DONE (complete)
    terminal error / no done   -> repo-ERROR or nothing (incomplete)

where "fatal" = error event with recoverable falsy. Mid-turn recoverable
diagnostics (denials, recovered transients) followed by a clean done still
complete — otherwise the crash-recovery replay branch would wipe live tool
history on the next turn.

Single decision point: the turn_succeeded assignment in run_turn, now fed by
terminal evidence instead of bare exhaustion. H3/G1B/replay untouched.
"""

from __future__ import annotations

import asyncio
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


def _session(ws, sid="h5"):
    return {"id": sid, "model": "mock", "workspace": ws, "messages": []}


def _run_turn(runtime, session, prompt="go"):
    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt=prompt)]
    return asyncio.run(_main())


def _types(evs):
    return [e.get("type") for e in evs]


def _repo_types(repo, sid):
    return [str(e.event_type) for e in repo.load_events(sid)]


# ── §8. H3 errors must never write DONE ───────────────────────────────

class TestErrorNeverComplete:
    def test_e1_stream_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(_DictProvider([
            _tool_round([_read_call("a.txt", "c0")]),
            _think_then_hang("craft")]), tmp_path, ws_files={"a.txt": "a"})
        evs = _run_turn(runtime, _session(ws, "e1"))
        assert "done" not in _types(evs) and "error" in _types(evs)
        assert repo.was_last_turn_complete("e1") is False
        assert "done" not in _repo_types(repo, "e1")

    def test_e2_partial_output_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")

        def _partial_then_down():
            def _g():
                yield {"type": "content", "text": "half answer"}
                raise ConnectionResetError("down")
            return _g

        runtime, repo, ws = _runtime(_DictProvider([_partial_then_down()]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "e2"))
        assert "half answer" in "".join(
            e.get("text", "") for e in evs if e.get("type") == "content")
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("e2") is False

    def test_e3_reasoning_only_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")

        def _think_then_down():
            def _g():
                yield {"type": "thinking", "text": "ruminate"}
                raise ConnectionResetError("down")
            return _g

        runtime, repo, ws = _runtime(_DictProvider([_think_then_down()]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "e3"))
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("e3") is False

    def test_e4_empty_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(_DictProvider([_empty_done()]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "e4"))
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("e4") is False


# ── §9. Success must still write DONE ─────────────────────────────────

class TestSuccessStillComplete:
    def test_s1_clean_success(self, tmp_path):
        runtime, repo, ws = _runtime(_DictProvider([_content_only("answer")]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "s1"))
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("s1") is True
        # Migration P0: the assistant turn body is journaled as well, so the
        # log is replayable. The §9 intent below is unchanged — success still
        # writes exactly one DONE, and DONE is still the terminal row.
        assert _repo_types(repo, "s1") == [
            "user_message", "assistant_message", "replay_digest", "done"]
        assert _repo_types(repo, "s1").count("done") == 1

    def test_s2_tool_turn_clean_finish(self, tmp_path):
        runtime, repo, ws = _runtime(_DictProvider([
            _tool_round([_read_call("a.txt", "c0")]),
            _content_only("final")]), tmp_path, ws_files={"a.txt": "a"})
        evs = _run_turn(runtime, _session(ws, "s2"))
        assert _types(evs).count("done") == 1
        assert "tool_result" in _types(evs)
        assert repo.was_last_turn_complete("s2") is True

    def test_s3_multi_iteration_success(self, tmp_path):
        runtime, repo, ws = _runtime(_DictProvider([
            _tool_round([_read_call("a.txt", "c0")]),
            _tool_round([_read_call("a.txt", "c1")]),
            _tool_round([_read_call("a.txt", "c2")]),
            _content_only("done after work")]), tmp_path, ws_files={"a.txt": "a"})
        evs = _run_turn(runtime, _session(ws, "s3"))
        assert _types(evs).count("tool_result") == 3
        assert _types(evs).count("done") == 1
        assert repo.was_last_turn_complete("s3") is True


# ── §10/§11. Timeout / cancellation ───────────────────────────────────

class TestTimeoutCancellation:
    def test_timeout_repo_incomplete(self, tmp_path):
        runtime, repo, ws = _runtime(_DictProvider([_hang()]), tmp_path,
                                     turn_timeout=2)
        evs = _run_turn(runtime, _session(ws, "to"))
        assert "error" in _types(evs)  # timeout error (+formal done) …
        assert repo.was_last_turn_complete("to") is False  # … but incomplete

    def test_cancel_stays_incomplete(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        session = _session(ws, "cx")

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
        assert repo.was_last_turn_complete("cx") is False
        # recovery still runs afterwards; the repeating hang script fails
        # again deterministically (preservation pin, not a RED row).
        evs2 = _run_turn(runtime, session, prompt="again")
        assert "done" not in _types(evs2)
        assert "error" in _types(evs2)


# ── §12/§13. Exhaustion / malformed / empty-object ────────────────────

class TestExhaustionMalformed:
    def test_exhaustion_delivers_wrapup_and_flag_pinned_F44(self, tmp_path):
        """UPDATED by ADR-0039 (PM-21) — the old assertion was F40-dependent.

        This test used to assert `was_last_turn_complete("ex") is False` and a
        "Max iterations reached" error. Both held ONLY because MockProvider
        emits *typed* events: the wrap-up raised AttributeError on the first
        one (F40-1), so the model's wrap-up text was discarded and the turn's
        last persisted event was the spurious error.

        MEASURED: with a dict-emitting provider the identical script yields
        `was_last_turn_complete == True` and no error — and the dict path is
        untouched by ADR-0039. So the old assertion was an artifact of the
        provider representation, which is precisely what ADR-0039 removes.

        The turn-level question it was reaching for — *should an exhausted turn
        that DID deliver a wrap-up summary count as complete?* — is a
        completion-authority question (ADR-0035), not a normalization one. It
        is recorded as **F44** and deliberately NOT decided here.
        """
        prov = MockProvider(responses=["", ""], tool_calls=[
            [_read_call("a.txt", "c0")], [_read_call("a.txt", "c1")]])
        runtime, repo, ws = _runtime(
            prov, tmp_path, ws_files={"a.txt": "a"}, max_iterations=2)
        evs = _run_turn(runtime, _session(ws, "ex"))
        # the wrap-up delivered the provider's text instead of a spurious error
        assert "done" in _types(evs)
        assert not any("Max iterations reached" in str(e.get("message", ""))
                       for e in evs if e.get("type") == "error"), (
            "the spurious budget error is still emitted for a typed provider")
        # F44 pin: typed and dict providers now AGREE on the completion flag.
        assert repo.was_last_turn_complete("ex") is True

    def test_malformed_not_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(_DictProvider([_garbage()]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "mf"))
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("mf") is False

    def test_natural_empty_not_success(self, tmp_path, monkeypatch):
        """REWRITTEN by ADR-0041 (PM-23).

        The old body was `test_natural_empty_success_preserved`: *"H2
        bookkeeping behavior: bare 'complete' marker alone still ends
        done(natural). H5 does not reclassify provider semantics."* — i.e. it
        preserved F43 deliberately, as a cross-phase compatibility pin.

        ADR-0041 does reclassify it, because the classification was wrong: a
        bare terminal marker is not a response. An empty stream is an empty
        stream whichever provider emitted it, so it is not a success.
        """
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(MockProvider(responses=[""]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "ne"))
        assert "done" not in _types(evs)
        assert repo.was_last_turn_complete("ne") is False


# ── §14/§15. Ordering + single terminality ────────────────────────────

class TestOrderingAndSingularity:
    def test_terminal_precedes_decision_no_done_after_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "od"))
        assert evs[-1].get("type") == "error"  # closure is the last word …
        assert "done" not in _types(evs)  # … no DONE after H3 error …
        assert "done" not in _repo_types(repo, "od")  # … no repo marker either

    def test_success_single_done_single_marker(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("ok")]), tmp_path)
        evs = _run_turn(runtime, _session(ws, "sg"))
        assert _types(evs).count("done") == 1
        assert _repo_types(repo, "sg").count("done") == 1


# ── §16. Resume after failure enters recovery ─────────────────────────

class TestResumeAfterFailure:
    def test_r1_failed_turn_resume_recovers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([
            _tool_round([_read_call("a.txt", "c0")]),
            _think_then_hang("craft"),
            _content_only("fresh answer")])
        runtime, repo, ws = _runtime(prov, tmp_path, ws_files={"a.txt": "a"})
        session = _session(ws, "rs")
        evs1 = _run_turn(runtime, session, prompt="first")
        assert "done" not in _types(evs1)
        assert repo.was_last_turn_complete("rs") is False
        had_exchanges = any(m.get("role") == "tool" for m in session["messages"])
        assert had_exchanges
        evs2 = _run_turn(runtime, session, prompt="second")
        assert _types(evs2).count("done") == 1
        # Migration P0 changed this deliberately. Recovery used to shed tool
        # history, because the event log carried no assistant/tool rows and
        # `load_session()` could only return the user messages — replay
        # overwrote the live transcript with a user-only list. Now that the
        # turn body is journaled, recovery restores it, and the replayed
        # transcript stays provider-valid (every tool_calls block is followed
        # by a reply for each id).
        assert any(m.get("role") == "tool" for m in session["messages"])
        assert [m.get("content") for m in session["messages"]
                if m.get("role") == "user"] == ["first", "second"]

    def test_r2_success_resume_unchanged(self, tmp_path):
        prov = _DictProvider([_content_only("one"), _content_only("two")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "rs2")
        _run_turn(runtime, session, prompt="first")
        assert repo.was_last_turn_complete("rs2") is True
        evs2 = _run_turn(runtime, session, prompt="second")
        assert _types(evs2).count("done") == 1
        assert prov.calls == 2


# ── §17. Append-only history ──────────────────────────────────────────

class TestAppendOnly:
    def test_error_then_done_distinct_rows(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        prov = _DictProvider([_think_then_hang("x"), _content_only("ok")])
        runtime, repo, ws = _runtime(prov, tmp_path)
        session = _session(ws, "ao")
        _run_turn(runtime, session, prompt="first")
        before = [(str(e.event_type), e.sequence_num) for e in repo.load_events("ao")]
        _run_turn(runtime, session, prompt="second")
        after = [(str(e.event_type), e.sequence_num) for e in repo.load_events("ao")]
        assert after[:len(before)] == before  # attempt 1 never mutated …
        kinds = [t for t, _ in after]
        assert "done" not in kinds[:len(before)]  # … stayed failed …
        assert kinds.count("done") == 1  # … success is the later row


# ── §19. Flag compatibility spy ───────────────────────────────────────

class TestFlagCompatibility:
    def _spy(self, runtime):
        seen: dict = {}
        orig = runtime._persist_turn_state

        def _wrap(session, sid, prompt, turn_succeeded, seq_num,
                  terminal_error=None):
            seen["turn_succeeded"] = turn_succeeded
            return orig(session, sid, prompt, turn_succeeded, seq_num,
                        terminal_error)

        runtime._persist_turn_state = _wrap  # type: ignore[method-assign]
        return seen

    def test_flag_false_on_terminal_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_think_then_hang("x")]), tmp_path)
        seen = self._spy(runtime)
        _run_turn(runtime, _session(ws, "fl"))
        assert seen["turn_succeeded"] is False  # stale-True impossible …
        assert repo.was_last_turn_complete("fl") is False  # … and not persisted

    def test_flag_true_on_clean_done(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("ok")]), tmp_path)
        seen = self._spy(runtime)
        _run_turn(runtime, _session(ws, "fl2"))
        assert seen["turn_succeeded"] is True
        assert repo.was_last_turn_complete("fl2") is True
