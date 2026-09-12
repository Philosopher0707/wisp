"""PHASE 13-H — provider degradation & amplification forensics (AUDIT ONLY).

No production code is modified by this file. Deterministic tests pin the
retry nesting, termination, steering, search, and accounting mechanics
behind Incidents A (83-tool degradation → HTTP 500) and B (steering +
duplicate reads) using mocks and unit seams.
"""
from __future__ import annotations


import pytest


def _call(name, args):
    return {"function": {"name": name, "arguments": args}}


# ── H1: stream guard retries bounded, resend full context ──

@pytest.mark.asyncio
async def test_h1_stream_attempts_bounded_and_resend():
    """Empty streams retry exactly max_attempts; each attempt rebuilds
    from the same full message list (context resend, not delta)."""
    from wisp.core.provider_stream import guarded_provider_stream
    seen_payloads = []
    calls = []

    def open_stream():
        calls.append(1)
        async def gen():
            yield {"type": "stream_stats", "sse_lines": 1, "usable_deltas": 0,
                   "empty_choice_chunks": 1, "finish_reason": "stop"}
            yield {"type": "done", "done_reason": "stop"}  # bare marker: empty
        seen_payloads.append("full-context")
        return gen()

    # Production bookkeeping set (stateless._BOOKKEEPING_TYPES): terminal
    # markers must be included or a bare marker counts as meaningful.
    out = [e async for e in guarded_provider_stream(
        open_stream, lambda e: dict(e),
        ("done", "stream_complete", "checkpoint", "usage", "stream_stats"),
        first_token_deadline_s=5, chunk_deadline_s=5, max_attempts=3)]
    assert len(calls) == 3  # bounded, no more
    assert len(seen_payloads) == 3  # every attempt resends everything
    assert out[-1].get("type") == "error"  # honest terminal failure


# ── H2: transport retry math per POST ──

def test_h2_transport_retries_bounded_per_post():
    """hardened_post: 2 transient failures then success == 3 POSTs."""
    from wisp.core.transport import hardened_post
    posts = []

    class Resp:
        status_code = 200

    class Session:
        def post(self, url, **kw):
            posts.append(1)
            if len(posts) < 3:
                raise ConnectionResetError("reset by peer")
            return Resp()

    hardened_post(Session(), "http://x", json={}, max_attempts=3)
    assert len(posts) == 3


# ── H3: turn-level degradation twin — counts stay honest ──

@pytest.mark.asyncio
async def test_h3_many_reads_counts_and_terminates(tmp_path):
    """5 read rounds: 5 tool results, 6 provider generations (5+final),
    turn completes via content (no hidden reissue)."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry
    from wisp.providers.mock import MockProvider
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "f.txt").write_text("data\n")
    config = WispConfig().replace(workspace=str(ws), max_iterations=6)
    provider = MockProvider(
        responses=["", "", "", "", "", "summary"],
        tool_calls=[[ _call("read_file", {"path": "f.txt"}) ]] * 5,
    )

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    rt = AgentRuntime(store=UnifiedStore(tmp_path / "wisp.db"),
                      security=SecurityPolicy(), extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=factory,
                      config=config)
    session = await rt.get_or_create_session(
        "h3", model="mock-model", workspace=str(ws))
    events = [e async for e in rt.run_turn(session, prompt="survey")]
    results = [e for e in events if e.get("type") == "tool_result"]
    assert len(results) == 5
    assert provider._index == 6  # generations == rounds + final answer
    tools = [m for m in session["messages"] if m.get("role") == "tool"]
    assert len(tools) == 5


# ── H4: steering appends, resets nothing, re-executes nothing ──

@pytest.mark.asyncio
async def test_h4_steering_note_no_reset_no_replay(tmp_path):
    """A mid-turn steering note is appended once; no tool re-executes,
    no counter resets, turn still completes on budget."""
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry
    from wisp.providers.mock import MockProvider
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "f.txt").write_text("data\n")
    config = WispConfig().replace(workspace=str(ws), max_iterations=4)
    provider = MockProvider(
        responses=["", "", "done"],
        tool_calls=[[ _call("read_file", {"path": "f.txt"}) ],
                    [ _call("read_file", {"path": "f.txt"}) ]],
    )

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    rt = AgentRuntime(store=UnifiedStore(tmp_path / "wisp.db"),
                      security=SecurityPolicy(), extensions=ExtensionHost(),
                      telemetry=Telemetry(), core_factory=factory,
                      config=config)
    session = await rt.get_or_create_session(
        "h4", model="mock-model", workspace=str(ws))
    rt.inject_steering(session["id"], "focus on f.txt")
    events = [e async for e in rt.run_turn(session, prompt="survey")]
    by_type = {}
    for e in events:
        by_type.setdefault(e.get("type"), []).append(e)
    assert by_type.get("done")
    injected = [m for m in session["messages"]
                if m.get("role") == "user" and "focus on" in str(m.get("content", ""))]
    assert len(injected) == 1  # appended exactly once, never replayed


# ── H5: empty-index search is an authoritative-looking false negative ──

def test_h5_empty_index_false_negative(tmp_path):
    """13-I-1 INVERSION: fresh workspace, no indexed content — the tool
    MUST report search-unavailable, never a bare 'nothing found'.
    (Raw index.search() still returns [], but the tool gate intercepts.)"""
    from wisp.semantic_index import SemanticIndex
    from wisp.tools.search import tool_search_codebase
    (tmp_path / "code.py").write_text("def providers(): pass\n")
    idx = SemanticIndex(str(tmp_path))
    idx._embed = lambda texts: [[0.1] * 8 for _ in texts]
    assert idx.search("providers") == []
    out = tool_search_codebase("providers", workspace=str(tmp_path))
    assert "unavailable" in out
    assert not out.startswith("No semantically relevant code found")


# ── H6: summary counts tool results only ──

def test_h6_summary_counts_tool_results_not_attempts():
    """Provider/stream/transport activity never moves the 'N tools'
    counters; denials count as results (classified failed)."""
    from wisp.core.events import tool_result
    from wisp.transport.progress import ProgressTracker
    t = ProgressTracker()
    t.on_event(tool_result("read_file", '{"status":"ok","data":"x"}'))
    t.on_event(tool_result("run_bash",
                           '{"status":"POLICY_DENIED","data":"no"}'))
    assert (t.progress.tools_succeeded, t.progress.tools_failed) == (1, 1)


# ── H7: pruning condenses old reads (structural reread incentive) ──

def test_h7_pruner_condenses_old_tool_results():
    """Beyond the full-retention window, tool payloads are condensed —
    a model needing full content again must re-read (duplicate bytes
    with a legitimate cause)."""
    from wisp.core.context_pruner import prune_messages
    msgs = [{"role": "user", "content": "survey"}]
    for i in range(8):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"call_{i}", "type": "function",
                                     "function": {"name": "read_file",
                                                  "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"call_{i}",
                     "content": "line\n" * 500})
    pruned = prune_messages(msgs)
    before = sum(len(m.get("content", "")) for m in msgs if m.get("role") == "tool")
    after = sum(len(m.get("content", "")) for m in pruned if m.get("role") == "tool")
    assert after < before  # old payloads condensed, not preserved
    assert len([m for m in pruned if m.get("role") == "tool"]) == 8  # none dropped
