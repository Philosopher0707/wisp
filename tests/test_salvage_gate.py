"""G1D salvage-to-side-effect integrity (§2-§28).

Invariant: complete ∧ valid ∧ authorized -> execute; anything else ->
no mutation. Authorization ALLOW is necessary but NOT sufficient:
the provider round must also be complete.

Round shapes drive a real turn (provider -> stream layer -> parser/
salvage -> ToolExecutor -> workspace) with an allow-all or deny-all
approval handler; the workspace is the assertion surface.
"""
from __future__ import annotations

import asyncio
import os
import random
import tempfile

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.tool_executor import ToolExecutor

ARGS = {"path": "target.txt", "content": "payload-bytes"}


def _harness():
    ws = tempfile.mkdtemp(prefix="g1d-")
    ex = ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                      file_lock=None, lsp_manager=None,
                      subagent_orchestrator=None, extensions=None)
    return ws, ex


async def _allow(ev):
    return True


async def _deny(ev):
    return False


def _run_turn(provider, ws, ex, handler, limit=8, stop_after="tool_result"):
    core = WispAgentCore(provider=provider, tool_executor=ex)
    session = {"id": "g1d", "messages": [], "model": "mock",
               "workspace": ws}

    async def _main():
        evs = []
        async for ev in core.turn(session, "go",
                                  approval_handler=handler):
            evs.append(ev)
            # scripted providers replay; one round suffices unless the test
            # needs transcript persistence (which happens at round end)
            if stop_after and any(e.get("type") == stop_after for e in evs):
                break
            if len([e for e in evs if e.get("type") == "done"]) >= 2:
                break
        return evs

    return asyncio.run(_main()), session


def _tool_call_round(args, terminal=True):
    """Scripted provider: one tool_call, optional terminal marker."""
    class _P:
        def generate_stream_events(self, system_prompt, messages, tools=None,
                                   checkpoint_every=50):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": dict(args)}
            if terminal:
                yield {"type": "done", "done_reason": "tool_calls"}

    return _P()


def _mutated(ws):
    return os.path.exists(os.path.join(ws, "target.txt"))


def _ev_text(e):
    for key in ("message", "data", "result"):
        val = e.get(key, "")
        if val:
            return str(val)
    return ""


# ── §27 required matrix ──

def test_complete_valid_authorized_executes():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round(ARGS), ws, ex, _allow,
                       stop_after="done")
    assert _mutated(ws)
    assert open(os.path.join(ws, "target.txt")).read() == "payload-bytes"
    assert any(e.get("type") == "done" for e in evs)


def test_complete_valid_denied_blocks():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round(ARGS), ws, ex, _deny)
    assert not _mutated(ws)
    assert any(e.get("type") == "tool_result" for e in evs)


def test_complete_invalid_no_mutation():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round({"content": "no-path"}), ws, ex,
                       _allow)
    assert not _mutated(ws)  # schema rejects pathless write
    assert any(e.get("type") == "tool_result" for e in evs)


def test_partial_error_no_mutation_despite_allow():
    from wisp.stream_events import TokenBatch
    import requests

    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": dict(ARGS)}
            yield TokenBatch(phase="content", text="x", batch_index=0)
            raise requests.ConnectionError("reset mid-stream")

    ws, ex = _harness()
    evs, _ = _run_turn(_P(), ws, ex, _allow)
    assert not _mutated(ws)
    assert any(e.get("type") == "error" for e in evs)
    assert not any(e.get("type") == "done" for e in evs)


def test_truncated_no_mutation_despite_allow():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round(ARGS, terminal=False), ws, ex,
                       _allow)
    assert not _mutated(ws)
    types = [e.get("type") for e in evs]
    assert "error" in types  # round failure surfaced (G1B)
    results = [e for e in evs if e.get("type") == "tool_result"]
    assert results and all("Refused" in str(r.get("result", r))
                           for r in results)


def test_timeout_no_mutation_despite_allow():
    from wisp.stream_events import TokenBatch

    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": dict(ARGS)}
            yield TokenBatch(phase="content", text="x", batch_index=0)
            import time
            time.sleep(30)

    ws, ex = _harness()
    core = WispAgentCore(provider=_P(), tool_executor=ex)
    core.CHUNK_DEADLINE_S = 0.2
    core.FIRST_TOKEN_DEADLINE_S = 5.0
    session = {"id": "g1d", "messages": [], "model": "mock",
               "workspace": ws}

    async def _main():
        evs = []
        async for ev in core.turn(session, "go",
                                  approval_handler=_allow):
            evs.append(ev)
            if any(e.get("type") == "tool_result" for e in evs):
                break
        return evs

    evs = asyncio.run(_main())
    assert not _mutated(ws)
    assert not any(e.get("type") == "done" for e in evs)


def test_cancelled_no_mutation():
    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": dict(ARGS)}
            raise asyncio.CancelledError()

    ws, ex = _harness()
    core = WispAgentCore(provider=_P(), tool_executor=ex)
    session = {"id": "g1d", "messages": [], "model": "mock",
               "workspace": ws}

    async def _main():
        async for ev in core.turn(session, "go",
                                  approval_handler=_allow):
            pass

    try:
        asyncio.run(_main())
    except (asyncio.CancelledError, Exception):
        pass
    assert not _mutated(ws)


def test_malformed_no_mutation_despite_allow():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round({"path": 12345,
                                         "content": "x"}), ws, ex, _allow)
    assert not _mutated(ws)  # wrong arg type: schema rejects


def test_empty_no_mutation():
    from wisp.providers.mock import MockProvider
    ws, ex = _harness()
    core = WispAgentCore(provider=MockProvider(responses=["just text"]),
                         tool_executor=ex)
    session = {"id": "g1d", "messages": [], "model": "mock",
               "workspace": ws}

    async def _main():
        return [ev async for ev in core.turn(session, "go",
                                             approval_handler=_allow)]

    asyncio.run(_main())
    assert not _mutated(ws)


def test_unknown_state_no_mutation():
    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "mystery", "frobnicate": True}
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": dict(ARGS)}
            # no terminal: unknown + unterminated -> truncated

    ws, ex = _harness()
    evs, _ = _run_turn(_P(), ws, ex, _allow)
    assert not _mutated(ws)
    assert not any(e.get("type") == "done" for e in evs)


# ── §9/§14 malformed-but-complete: salvage intentionally supported ──

def test_complete_raw_malformed_salvage_executes():
    """COMPLETE round + recoverable _raw args + allow -> executes.
    The gate keys on round completion, not _raw presence."""
    ws, ex = _harness()

    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": {"_raw": '{"path": "target.txt", '
                                         '"content": "salvaged-ok"}'}}
            yield {"type": "done", "done_reason": "tool_calls"}

    evs, _ = _run_turn(_P(), ws, ex, _allow)
    assert _mutated(ws)


def test_truncated_raw_never_salvaged_to_mutation():
    """Same _raw shape, TRUNCATED round -> refused before salvage matters."""
    ws, ex = _harness()

    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": {"_raw": '{"path": "target.txt", '
                                         '"content": "salvaged-no"}'}}

    evs, _ = _run_turn(_P(), ws, ex, _allow)
    assert not _mutated(ws)
    refusal = " ".join(_ev_text(e)
                       for e in evs if e.get("type") == "tool_result")
    assert "Refused" in refusal and "Salvaged candidate" in refusal


# ── §10 truncated tool call, §15 path safety ──

def test_truncated_tool_call_missing_args_no_mutation():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round({"path": "target.txt"},
                                        terminal=False), ws, ex, _allow)
    assert not _mutated(ws)


def test_salvage_path_traversal_contained():
    ws, ex = _harness()

    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "write_file", "id": "c1",
                   "arguments": {"_raw": '{"content": "evil"}'}}
            yield {"type": "done", "done_reason": "tool_calls"}

    evs, _ = _run_turn(_P(), ws, ex, _allow)
    # invented path is workspace-relative (./output.txt); nothing escapes
    assert not os.path.exists("/tmp/g1d-escape-probe")
    parent = os.path.dirname(ws.rstrip("/"))
    assert not os.path.exists(os.path.join(parent, "output.txt")), \
        "write escaped the workspace"


def test_absolute_path_outside_workspace_blocked():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round({"path": "/tmp/g1d-abs-probe",
                                         "content": "x"}), ws, ex, _allow)
    assert not os.path.exists("/tmp/g1d-abs-probe")


# ── §5/§19 read-only salvage proceeds, mutation refused ──

def test_truncated_round_read_only_executes():
    ws, ex = _harness()
    with open(os.path.join(ws, "notes.txt"), "w") as fh:
        fh.write("hello-read")

    class _P:
        def generate_stream_events(self, *a, **k):
            yield {"type": "tool_call", "name": "read_file", "id": "c1",
                   "arguments": {"path": "notes.txt"}}
            # truncated: no terminal

    evs, _ = _run_turn(_P(), ws, ex, _allow)
    results = [e for e in evs if e.get("type") == "tool_result"]
    assert results, "read-only call must still execute on partial rounds"
    assert "hello-read" in str(results[0].get("result", results[0]))


# ── §20 cross-layer pair, §21 crash interaction, §25 observability ──

def test_cross_layer_pair():
    ws_ok, ex_ok = _harness()
    _run_turn(_tool_call_round(ARGS), ws_ok, ex_ok, _allow)
    assert _mutated(ws_ok)
    ws_bad, ex_bad = _harness()
    _run_turn(_tool_call_round(ARGS, terminal=False), ws_bad, ex_bad,
              _allow)
    assert not _mutated(ws_bad)


def test_refused_turn_leaves_no_recovery_state():
    # Rejected candidates stay rejected: repeat turns refuse identically,
    # never promote to executable, create no journal. (Candidate tracking
    # is in-flow only; durable G2 tracking is a documented limitation.)
    from wisp.workspace import _journal_dir, recover
    ws, ex = _harness()
    for _ in range(2):
        evs, _session = _run_turn(_tool_call_round(ARGS, terminal=False),
                                  ws, ex, _allow, stop_after=None)
        assert not _mutated(ws)
        assert any("Refused" in _ev_text(e)
                   for e in evs if e.get("type") == "tool_result")
    assert recover(ws) == "clean"  # refused calls create no journal
    assert not os.path.exists(_journal_dir(ws))


def test_refusal_names_round_state_and_salvage():
    ws, ex = _harness()
    evs, _ = _run_turn(_tool_call_round(
        {"_raw": '{"path": "target.txt", "content": "x"}'},
        terminal=False), ws, ex, _allow)
    assert not _mutated(ws)
    texts = " ".join(_ev_text(e)
                     for e in evs if e.get("type") == "tool_result")
    assert "terminal marker" in texts  # round state named
    assert "Salvaged candidate" in texts  # parse mode named


# ── §28 bounded fuzz: gate decision is total over hostile inputs ──

def test_fuzz_gate_decision_never_mutates_when_incomplete():
    """Bounded behavioral fuzz: hostile args x incomplete round through the
    real _execute_tool gate -> refusal always, mutation never."""
    from wisp.core.engine import WispAgentCore
    rng = random.Random(13131)
    alphabet = list('{"}:[],.abcdefghijklmnopqrstuvwxyz0123456789\x00\u00e9../\\')
    ws, ex = _harness()
    core = WispAgentCore(provider=None, tool_executor=ex)
    session = {"id": "fz", "messages": [], "model": "mock", "workspace": ws}

    async def _one(tool, args):
        ev = {"name": tool, "id": "c9", "arguments": args,
              "_round_complete": False, "_round_state": "fuzzed-incomplete"}
        out = []
        async for e in core._execute_tool(ev, session,
                                          approval_handler=_allow):
            out.append(e)
        return out

    async def _main():
        n_refused = 0
        for i in range(120):
            mode = rng.randrange(4)
            if mode == 0:  # truncation at byte boundaries
                full = '{"path": "target.txt", "content": "bytes"}'
                args = {"_raw": full[: rng.randrange(len(full) + 1)]}
            elif mode == 1:  # garbage keys
                args = {"".join(rng.choice(alphabet)
                                for _ in range(rng.randrange(1, 60))): "x"}
            elif mode == 2:  # traversal + sizes
                args = {"path": "../" * rng.randrange(4) + "x.txt",
                        "content": "x" * rng.randrange(3000)}
            else:  # oversized extras
                args = {"path": "target.txt", "content": "x",
                        "extra": "".join(rng.choice(alphabet)
                                         for _ in range(200))}
            tool = rng.choice(("write_file", "read_file", "run_bash",
                               "edit_file", "no_such_tool_xyz"))
            evs = await _one(tool, args)
            assert len(evs) == 1 and evs[0].get("type") == "tool_result"
            if tool == "read_file":
                continue  # read-only: gate passes, tool decides
            assert "Refused" in str(evs[0].get("result", evs[0])), (i, tool)
            n_refused += 1
        return n_refused

    n_refused = asyncio.run(_main())
    assert n_refused > 80, n_refused
    assert not _mutated(ws)
    assert os.listdir(ws) == [], os.listdir(ws)
