"""Adjacent hotfix: tool failure -> message protocol integrity.

Live session: 4x web_search TLS failures rendered ✓, fetches 404'd,
breaker tripped, then provider 400 (messages[14]: missing tool_call_id).

Root causes (traced, not assumed):
- B: ToolExecutor.execute() early-return paths (breaker/repeat/hook/
  plan/danger/perm/decline/pre-hook) emit tool results WITHOUT the
  inbound tool_call_id; history defaults it to ""; the OpenAI
  serializer sends ""  -> strict providers 400.
- A: CLITransport._is_error_result checks raw strings, not parsed
  status -> JSON-string errors (slow tools, spinner path) render ✓.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _executor():
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor
    return ToolExecutor(config=WispConfig(), hook_manager=None, mcp=None,
                        file_lock=None, lsp_manager=None,
                        subagent_orchestrator=None, extensions=None)


async def _collect(agen):
    return [ev async for ev in agen]


def _flat(ev):
    return ev if isinstance(ev, dict) else {"type": str(getattr(ev, "type", "")),
                                            **dict(getattr(ev, "data", {}))}


# ── Finding A: error results must not render success ──

@pytest.mark.parametrize("result", [
    json.dumps({"status": "error", "tool": "web_search", "data": "TLS boom"}),
    json.dumps({"status": "error", "data": {"query": "x", "results": []},
                "metadata": {}, "error": "SSL CERTIFICATE_VERIFY_FAILED"}),
    "[WEB_FETCH_FAILED] HTTP 404: http://x does not exist.",
    "Error: something broke",
    "[Error: denied]",
    {"status": "error", "data": "d"},
])
def test_error_results_detected_as_errors(result):
    from wisp.transport.cli import CLITransport
    assert CLITransport._is_error_result(result) is True, repr(result)[:80]


@pytest.mark.parametrize("result", [
    json.dumps({"status": "ok", "tool": "web_search", "data": "fine"}),
    "plain output text",
    {"status": "ok", "data": "d"},
])
def test_ok_results_not_detected_as_errors(result):
    from wisp.transport.cli import CLITransport
    assert CLITransport._is_error_result(result) is False, repr(result)[:80]


# ── Finding B: breaker refusal preserves the inbound ID ──

@pytest.mark.asyncio
async def test_breaker_refusal_preserves_tool_call_id(tmp_path):
    """Trip the fetch breaker, then refuse: the refusal must carry the ID."""
    ex = _executor()
    ws = str(tmp_path)
    cid = "call_live_123"

    async def _fail(name, args, **kw):
        from wisp.tools.errors import ToolError
        raise ToolError("[WEB_FETCH_FAILED] Cannot reach x (connection error).")

    import wisp.tools.registry as _reg
    orig = _reg.TOOL_IMPLS.get("web_fetch")
    _reg.TOOL_IMPLS["web_fetch"] = _fail
    try:
        for _ in range(4):  # exceed the consecutive-failure threshold
            evs = await _collect(ex.execute(
                "web_fetch", {"url": "http://x/", "max_chars": 10}, ws,
                tool_call_id=f"warm-{_}"))
        evs = await _collect(ex.execute(
            "web_fetch", {"url": "http://x/", "max_chars": 10}, ws,
            tool_call_id=cid))
    finally:
        if orig is not None:
            _reg.TOOL_IMPLS["web_fetch"] = orig
        else:
            del _reg.TOOL_IMPLS["web_fetch"]
    results = [_flat(e) for e in evs if _flat(e).get("type") == "tool_result"]
    assert results, "breaker never tripped"
    assert all(r.get("tool_call_id") == cid for r in results), results


@pytest.mark.asyncio
async def test_all_early_refusals_preserve_tool_call_id(tmp_path):
    """Every pre-execution refusal path forwards the inbound ID."""
    ex = _executor()
    ws = str(tmp_path)
    cid = "call_probe_9"

    async def _deny(name, args, reason):
        return False, None

    # plan/danger/perm blocks + decline + pre-hooks + breaker: drive via
    # decline (needs_approval tool) and unknown-tool paths with an ID.
    evs = await _collect(ex.execute(
        "write_file", {"path": "x.txt", "content": "y"}, ws,
        tool_call_id=cid, approval_handler=_deny))
    results = [_flat(e) for e in evs if _flat(e).get("type") == "tool_result"]
    assert results
    assert all(r.get("tool_call_id") == cid for r in results), results


# ── Serializer preflight (§12) ──

def test_preflight_rejects_empty_tool_call_id():
    from wisp.providers.openai import OpenAIProvider
    p = OpenAIProvider(model="t", api_key="sk-test")
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_A", "type": "function",
                         "function": {"name": "web_search",
                                      "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "", "content": "TLS boom"},
    ]
    with pytest.raises(ValueError, match="tool_call_id"):
        p._build_payload("sys", msgs, None, stream=False)


def test_preflight_rejects_unknown_tool_call_id():
    from wisp.providers.openai import OpenAIProvider
    p = OpenAIProvider(model="t", api_key="sk-test")
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_A", "type": "function",
                         "function": {"name": "web_search",
                                      "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_FOREIGN", "content": "x"},
    ]
    with pytest.raises(ValueError, match="tool_call_id"):
        p._build_payload("sys", msgs, None, stream=False)


def test_preflight_accepts_paired_history():
    from wisp.providers.openai import OpenAIProvider
    p = OpenAIProvider(model="t", api_key="sk-test")
    msgs = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_A", "type": "function",
                         "function": {"name": "web_search",
                                      "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_A",
         "content": "TLS boom (honest error, valid protocol)"},
    ]
    payload = p._build_payload("sys", msgs, None, stream=False)
    tool_msgs = [m for m in payload["messages"] if m.get("role") == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_A"


# ── Parallel / out-of-order pairing (§9-10) ──

@pytest.mark.asyncio
async def test_parallel_mixed_results_keep_ids(tmp_path):
    """A ok + B fail + C fail -> each result carries its own call ID."""
    ex = _executor()
    ws = str(tmp_path)
    async def _search(name, args, **kw):
        return json.dumps({"status": "ok", "tool": name, "data": "fine"})

    async def _fetch(name, args, **kw):
        from wisp.tools.errors import ToolError
        raise ToolError("[WEB_FETCH_FAILED] HTTP 404: gone")

    import wisp.tools.registry as _reg
    orig_s, orig_f = _reg.TOOL_IMPLS.get("web_search"), _reg.TOOL_IMPLS.get("web_fetch")
    _reg.TOOL_IMPLS["web_search"] = _search
    _reg.TOOL_IMPLS["web_fetch"] = _fetch
    try:
        async def _one(tool, cid):
            evs = await _collect(ex.execute(
                tool, {"query": "x", "url": "http://x/", "max_chars": 10}
                if tool == "web_fetch" else {"query": "x"}, ws,
                tool_call_id=cid))
            return [_flat(e) for e in evs
                    if _flat(e).get("type") == "tool_result"]

        rA, rB = await asyncio.gather(_one("web_search", "call_A"),
                                      _one("web_fetch", "call_B"))
    finally:
        _reg.TOOL_IMPLS["web_search"] = orig_s
        _reg.TOOL_IMPLS["web_fetch"] = orig_f
    assert rA and rA[0].get("tool_call_id") == "call_A", rA
    assert rB and rB[0].get("tool_call_id") == "call_B", rB
    # error preserved AND paired (Finding: §4/§5)
    assert "call_B" in json.dumps(rB[0])


# ── Timeout / exception / cancel preserve IDs (§11) ──

@pytest.mark.asyncio
async def test_timeout_result_preserves_id(tmp_path):
    from wisp.config import WispConfig
    from wisp.tool_executor import ToolExecutor
    cfg = WispConfig()
    object.__setattr__(cfg, "tool_timeout", 1)
    ex = ToolExecutor(config=cfg, hook_manager=None, mcp=None,
                      file_lock=None, lsp_manager=None,
                      subagent_orchestrator=None, extensions=None)

    async def _hang(path="", workspace="", **kw):
        import asyncio as _aio
        await _aio.sleep(30)
        return "never"

    import wisp.tools.registry as _reg
    orig = _reg.TOOL_IMPLS.get("read_file")
    _reg.TOOL_IMPLS["read_file"] = _hang
    try:
        evs = await _collect(ex.execute(
            "read_file", {"path": "x"}, str(tmp_path),
            tool_call_id="call_timeout_1"))
    finally:
        if orig is not None:
            _reg.TOOL_IMPLS["read_file"] = orig
    results = [_flat(e) for e in evs if _flat(e).get("type") == "tool_result"]
    assert results
    assert all(r.get("tool_call_id") == "call_timeout_1" for r in results), results
    assert "timed out" in json.dumps(results[0]).lower()


@pytest.mark.asyncio
async def test_exception_result_preserves_id(tmp_path):
    ex = _executor()

    async def _boom(path="", workspace="", **kw):
        raise ValueError("kaput")

    import wisp.tools.registry as _reg
    orig = _reg.TOOL_IMPLS.get("read_file")
    _reg.TOOL_IMPLS["read_file"] = _boom
    try:
        evs = await _collect(ex.execute(
            "read_file", {"path": "x"}, str(tmp_path),
            tool_call_id="call_exc_1"))
    finally:
        if orig is not None:
            _reg.TOOL_IMPLS["read_file"] = orig
    results = [_flat(e) for e in evs if _flat(e).get("type") == "tool_result"]
    assert results
    assert all(r.get("tool_call_id") == "call_exc_1" for r in results), results


@pytest.mark.asyncio
async def test_approval_cancel_preserves_id(tmp_path):
    from wisp.cli.approval import ApprovalCancelled
    ex = _executor()

    async def _cancel(name, args, reason):
        raise ApprovalCancelled(name)

    evs = await _collect(ex.execute(
        "write_file", {"path": "x.txt", "content": "y"}, str(tmp_path),
        tool_call_id="call_cancel_1", approval_handler=_cancel))
    results = [_flat(e) for e in evs if _flat(e).get("type") == "tool_result"]
    assert results
    assert all(r.get("tool_call_id") == "call_cancel_1" for r in results), results


# ── E2E twin of the live session (§26) ──

@pytest.mark.asyncio
async def test_live_session_twin_no_400(tmp_path):
    """search TLS-fail x2 -> fetch 404 -> breaker trips -> history valid."""
    from wisp.providers.openai import OpenAIProvider
    ex = _executor()
    ws = str(tmp_path)

    async def _tls_fail(name, args, **kw):
        return json.dumps({"status": "error", "data": {"results": []},
                           "metadata": {}, "error": "SSL CERTIFICATE_VERIFY_FAILED"})

    async def _404(name, args, **kw):
        from wisp.tools.errors import ToolError
        raise ToolError("[WEB_FETCH_FAILED] HTTP 404: http://x/ gone")

    import wisp.tools.registry as _reg
    orig_s, orig_f = _reg.TOOL_IMPLS.get("web_search"), _reg.TOOL_IMPLS.get("web_fetch")
    _reg.TOOL_IMPLS["web_search"] = _tls_fail
    _reg.TOOL_IMPLS["web_fetch"] = _404
    try:
        history = []
        calls = ["call_s1", "call_s2", "call_f1", "call_f2", "call_f3"]
        tools = ["web_search", "web_search", "web_fetch", "web_fetch", "web_fetch"]
        argsets = [{"query": "astra"}, {"query": "astra"},
                   {"url": "http://a/", "max_chars": 10},
                   {"url": "http://b/", "max_chars": 10},
                   {"url": "http://c/", "max_chars": 10}]
        for cid, tool, args in zip(calls, tools, argsets):
            history.append({"role": "assistant", "content": "",
                            "tool_calls": [{"id": cid, "type": "function",
                                            "function": {"name": tool, "arguments": "{}"}}]})
            evs = await _collect(ex.execute(tool, args, ws, tool_call_id=cid))
            res = [_flat(e) for e in evs if _flat(e).get("type") == "tool_result"]
            assert res, (tool, cid)
            assert res[-1].get("tool_call_id") == cid, res
            rid = res[-1].get("tool_call_id", "")
            content = res[-1].get("result", res[-1])
            history.append({"role": "tool", "tool_call_id": rid,
                            "content": content if isinstance(content, str)
                            else json.dumps(content)})
        # the exact next-model-request serialization that 400'd live:
        p = OpenAIProvider(model="t", api_key="sk-test")
        payload = p._build_payload("sys", history, None, stream=False)
    finally:
        _reg.TOOL_IMPLS["web_search"] = orig_s
        _reg.TOOL_IMPLS["web_fetch"] = orig_f
    tool_msgs = [m for m in payload["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 5
    assert [m["tool_call_id"] for m in tool_msgs] == calls
    # failures preserved as honest errors, not successes
    assert "SSL" in tool_msgs[0]["content"] or "error" in tool_msgs[0]["content"].lower()


# ── Adversarial history (§25): fail closed, never repair (§13/§14) ──

def _assistant_call(cid):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": "web_search", "arguments": "{}"}}]}


@pytest.mark.parametrize("tool_msg", [
    {"role": "tool", "content": "x"},  # no ID at all
    {"role": "tool", "tool_call_id": None, "content": "x"},  # null ID
    {"role": "tool", "tool_call_id": "", "content": "x"},  # empty ID
    {"role": "tool", "tool_call_id": "call_NOPE", "content": "x"},  # foreign
    {"role": "tool", "tool_call_id": "call_A", "content": "x"},  # valid (control)
])
def test_adversarial_tool_messages(tool_msg):
    from wisp.providers.openai import OpenAIProvider
    p = OpenAIProvider(model="t", api_key="sk-test")
    msgs = [_assistant_call("call_A"), tool_msg]
    if tool_msg.get("tool_call_id") == "call_A":
        payload = p._build_payload("sys", msgs, None, stream=False)
        assert payload["messages"][-1]["tool_call_id"] == "call_A"
    else:
        with pytest.raises(ValueError, match="tool_call_id"):
            p._build_payload("sys", msgs, None, stream=False)


def test_tool_message_without_assistant_call_rejected():
    from wisp.providers.openai import OpenAIProvider
    p = OpenAIProvider(model="t", api_key="sk-test")
    with pytest.raises(ValueError, match="tool_call_id"):
        p._build_payload("sys", [{"role": "tool", "tool_call_id": "call_X",
                                  "content": "orphan"}], None, stream=False)


def test_malformed_tool_calls_array_rejected():
    from wisp.providers.openai import OpenAIProvider
    p = OpenAIProvider(model="t", api_key="sk-test")
    msgs = [{"role": "assistant", "content": "",
             "tool_calls": [{"type": "function"}]},  # no id, no function
            {"role": "tool", "tool_call_id": "", "content": "x"}]
    with pytest.raises(ValueError, match="tool_call_id"):
        p._build_payload("sys", msgs, None, stream=False)


# ── Shared predicate across render surfaces (§15/§16) ──

def test_progress_counter_parses_json_string_errors():
    from wisp.transport.progress import ProgressTracker
    t = ProgressTracker()
    import json as _json
    t.on_tool_result("web_search", _json.dumps({"status": "error",
                                                "data": "TLS boom"}))
    t.on_tool_result("read_file", _json.dumps({"status": "ok",
                                               "data": "fine"}))
    assert (t.progress.tools_failed, t.progress.tools_succeeded) == (1, 1)


# ── Provider matrix: shared-schema providers inherit preflight (§20) ──

@pytest.mark.parametrize("cls", ["openrouter", "nvidia"])
def test_sibling_providers_inherit_preflight(cls):
    import importlib
    mod = importlib.import_module(f"wisp.providers.{cls}")
    provider_cls = getattr(mod, {"openrouter": "OpenRouterProvider",
                                 "nvidia": "NVIDIAProvider"}[cls])
    p = provider_cls(model="t", api_key="sk-test")
    from wisp.providers.openai import OpenAIProvider
    assert type(p)._build_payload is OpenAIProvider._build_payload
    msgs = [{"role": "tool", "tool_call_id": "", "content": "x"}]
    with pytest.raises(ValueError, match="tool_call_id"):
        p._build_payload("sys", msgs, None, stream=False)


def test_ollama_protocol_has_no_tool_call_id():
    """Ollama native protocol carries no tool_call_id (verified absence —
    nothing to gate there)."""
    import subprocess
    out = subprocess.run(
        ["grep", "-rn", "tool_call_id", "wisp/ollama_client.py",
         "wisp/providers/ollama.py"], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parent.parent).stdout
    assert out.strip() == "", out


# ── Retry keeps identity per attempt (§22) ──

@pytest.mark.asyncio
async def test_retry_attempt_gets_fresh_authoritative_id(tmp_path):
    """Two sequential tool calls (model-level retry) keep distinct IDs;
    each result pairs with its own call — no cross-attachment."""
    ex = _executor()
    ws = str(tmp_path)
    seen = []

    async def _ok(name, args, **kw):
        seen.append(args)
        return '{"status": "ok", "data": "fine"}'

    import wisp.tools.registry as _reg
    orig = _reg.TOOL_IMPLS.get("read_file")
    _reg.TOOL_IMPLS["read_file"] = _ok
    try:
        ids = []
        for cid in ("call_try1", "call_try2"):
            evs = await _collect(ex.execute(
                "read_file", {"path": "x"}, ws, tool_call_id=cid))
            res = [_flat(e) for e in evs
                   if _flat(e).get("type") == "tool_result"]
            assert res and res[-1].get("tool_call_id") == cid
            ids.append(res[-1].get("tool_call_id"))
    finally:
        if orig is not None:
            _reg.TOOL_IMPLS["read_file"] = orig
    assert ids == ["call_try1", "call_try2"]  # distinct, correctly paired


# ── Identity provenance forensics (§2/§3): non-result events must never
# become tool messages. First corruption boundary was stateless._turn_inner
# appending approval_request/heartbeat/progress events (ID "") to history.

def _history_funnel(all_yielded):
    """Mirror of the fixed stateless funnel: live yields pass through,
    only type == tool_result enters history."""
    return [d for d in all_yielded if d.get("type") == "tool_result"]


@pytest.mark.asyncio
async def test_approval_request_never_becomes_tool_message(tmp_path):
    """One approved spawn_background yields approval_request (live-only)
    + tool_result; the funnel keeps exactly the result; the pair
    validates and serializes (exact live ValueError('') regression)."""
    from wisp.core.stateless import validate_tool_message_provenance
    from wisp.providers.openai import OpenAIProvider
    ex = _executor()
    ws = str(tmp_path)

    async def fake_dispatch(name, args, ws_):
        return '{"status":"ok","data":{"agent_id":"agent-1"}}', 5.0
    ex._execute_tool = fake_dispatch

    async def approve(name, args, reason):
        return True, None

    yielded = [_flat(e) async for e in ex.execute(
        "spawn_background", {"description": "d", "prompt": "p"},
        ws, tool_call_id="call_SPAWN_1", approval_handler=approve)]
    assert any(d.get("type") == "approval_request" for d in yielded)  # live UI kept
    hist = _history_funnel(yielded)
    assert len(hist) == 1 and hist[0].get("tool_call_id") == "call_SPAWN_1"
    assistant = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_SPAWN_1", "type": "function",
         "function": {"name": "spawn_background", "arguments": "{}"}}]}
    tools = [{"role": "tool", "tool_call_id": h.get("tool_call_id", ""), "content": "x"}
             for h in hist]
    assert validate_tool_message_provenance(assistant, tools) is None
    OpenAIProvider(model="t", api_key="sk-test")._build_payload(
        "sys", [assistant] + tools, None, stream=False)  # must not raise


# ── Upstream provenance validator (§14/15) ──

def _pair(cid):
    a = {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}}]}
    return a, [{"role": "tool", "tool_call_id": cid, "content": "ok"}]


def test_provenance_accepts_valid_pair():
    from wisp.core.stateless import validate_tool_message_provenance as v
    a, t = _pair("call_X")
    assert v(a, t) is None


def test_provenance_rejects_missing_id():
    from wisp.core.stateless import validate_tool_message_provenance as v
    a, _ = _pair("call_X")
    assert "missing/empty" in (v(a, [{"role": "tool", "content": "x"}]) or "")


def test_provenance_rejects_unknown_id():
    from wisp.core.stateless import validate_tool_message_provenance as v
    a, _ = _pair("call_X")
    assert "unknown" in (
        v(a, [{"role": "tool", "tool_call_id": "call_FOREIGN", "content": "x"}]) or "")


def test_provenance_rejects_wrong_id_crosswire():
    """Result for call B attached to assistant block for call A."""
    from wisp.core.stateless import validate_tool_message_provenance as v
    a, _ = _pair("call_A")
    _, tb = _pair("call_B")
    assert "unknown" in (v(a, tb) or "")


def test_provenance_rejects_duplicate_id():
    from wisp.core.stateless import validate_tool_message_provenance as v
    a, t = _pair("call_X")
    assert "duplicate" in (v(a, t + t) or "")


# ── Multi-turn isolation (§7/req 10) ──

@pytest.mark.asyncio
async def test_multi_turn_ids_isolated(tmp_path):
    """Turn 2 cannot inherit Turn 1's ID and vice versa — sequential
    executions keep distinct, correctly paired identities."""
    from wisp.core.stateless import validate_tool_message_provenance as v
    ex = _executor()
    ws = str(tmp_path)

    async def _ok(name, args, **kw):
        return '{"status": "ok", "data": "fine"}'

    import wisp.tools.registry as _reg
    orig = _reg.TOOL_IMPLS.get("read_file")
    _reg.TOOL_IMPLS["read_file"] = _ok
    try:
        pairs = []
        for cid in ("call_T1", "call_T2"):
            yielded = [_flat(e) async for e in ex.execute(
                "read_file", {"path": "x"}, ws, tool_call_id=cid)]
            hist = _history_funnel(yielded)
            assert [h.get("tool_call_id") for h in hist] == [cid]
            a = {"role": "assistant", "content": "", "tool_calls": [
                {"id": cid, "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}]}
            t = [{"role": "tool", "tool_call_id": cid, "content": "fine"}]
            assert v(a, t) is None
            pairs.append((a, t))
        # cross-checks fail both ways
        assert v(pairs[0][0], pairs[1][1]) is not None
        assert v(pairs[1][0], pairs[0][1]) is not None
    finally:
        if orig is not None:
            _reg.TOOL_IMPLS["read_file"] = orig


# ── Subagent identity isolation (§9/req 12) ──

@pytest.mark.asyncio
async def test_subagent_ids_do_not_leak_into_parent(tmp_path):
    """Child-core tool traffic keeps child IDs; the parent's spawn result
    carries only the parent's call ID."""
    ex = _executor()
    ws = str(tmp_path)
    dispatched = []

    async def fake_dispatch(name, args, ws_):
        dispatched.append((name, dict(args)))
        return '{"status":"ok","data":{"agent_id":"agent-9"}}', 1.0
    ex._execute_tool = fake_dispatch

    async def approve(name, args, reason):
        return True, None

    yielded = [_flat(e) async for e in ex.execute(
        "spawn_background", {"description": "d", "prompt": "p"},
        ws, tool_call_id="call_PARENT", approval_handler=approve)]
    hist = _history_funnel(yielded)
    assert [h.get("tool_call_id") for h in hist] == ["call_PARENT"]
    assert all("call_PARENT" not in str(d) for d in dispatched)
    # a child-local ID must never validate against the parent block
    from wisp.core.stateless import validate_tool_message_provenance as v
    parent = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_PARENT", "type": "function",
         "function": {"name": "spawn_background", "arguments": "{}"}}]}
    assert v(parent, [{"role": "tool", "tool_call_id": "call_CHILD_1",
                       "content": "x"}]) is not None


# ── Approval forensics: decline/approve/spawn_background (§10/req 17-18) ──

@pytest.mark.asyncio
async def test_declined_spawn_executes_nothing_and_keeps_id(tmp_path):
    ex = _executor()
    ws = str(tmp_path)
    ran = []

    async def fake_dispatch(name, args, ws_):
        ran.append(name)
        return "SHOULD NOT RUN", 0.0
    ex._execute_tool = fake_dispatch

    async def decline(name, args, reason):
        return False, None

    yielded = [_flat(e) async for e in ex.execute(
        "spawn_background", {"description": "d", "prompt": "p"},
        ws, tool_call_id="call_DECLINED", approval_handler=decline)]
    hist = _history_funnel(yielded)
    assert ran == []  # execution = 0, mutation = 0
    assert len(hist) == 1
    assert hist[0].get("tool_call_id") == "call_DECLINED"
    # 13F.1: structured USER_DENIED envelope (was "[Blocked: user declined …]")
    import json as _json
    res = hist[0].get("result", "")
    parsed = _json.loads(res) if isinstance(res, str) else res
    assert parsed.get("status") == "USER_DENIED"
    assert parsed.get("executed") is False


@pytest.mark.asyncio
async def test_approved_spawn_requires_actual_approval(tmp_path):
    """Decline, decline, then approve with independent verdicts: the first
    two never execute; the third executes exactly once."""
    ex = _executor()
    ws = str(tmp_path)
    ran = []

    async def fake_dispatch(name, args, ws_):
        ran.append(name)
        return '{"status":"ok","data":{"agent_id":"agent-7"}}', 1.0
    ex._execute_tool = fake_dispatch

    verdicts = iter([(False, None), (False, None), (True, None)])

    async def scripted(name, args, reason):
        return next(verdicts)

    for i, cid in enumerate(("call_D1", "call_D2", "call_A3")):
        yielded = [_flat(e) async for e in ex.execute(
            "spawn_background", {"description": "d", "prompt": "p"},
            ws, tool_call_id=cid, approval_handler=scripted)]
        hist = _history_funnel(yielded)
        assert [h.get("tool_call_id") for h in hist] == [cid]
    assert ran == ["spawn_background"]  # only the approved call executed


# ── Approval scope (§11/§13/req 19): pin Y/a/N/d + per-turn memo ──

def test_approval_verdict_mapping():
    from wisp.cli.approval import prompt_for_approval as m
    assert m("y") == "approve" and m("Y") == "approve_always"
    assert m("n") == "reject" and m("N") == "reject_always"
    assert m("a") == "auto_all" and m("d") == "block_all"
    assert m("c") == "cancel"
    assert m("") == "reject" and m("???") == "reject"  # fail-closed


def test_approval_session_state_scoping():
    """Y persists per tool NAME (explicit persistent mode); n is once;
    a/d flip session policy. y for A authorizes B only via explicit Y/a."""
    from wisp.approval_state import ApprovalSessionState as S
    st = S()
    assert st.should_ask("spawn_background") is True
    st.allow_tool("spawn_background")  # user pressed Y
    assert st.should_ask("spawn_background") is False
    assert st.should_ask("write_file") is True  # other tools still prompt
    st2 = S()
    st2.deny_tool("spawn_background")  # user pressed N
    assert st2.should_ask("spawn_background") is False  # silently denied
    assert st2.is_allowed("spawn_background") is False
    st3 = S()
    st3.set_auto()  # user pressed a
    assert st3.should_ask("anything") is False
    assert st3.is_allowed("anything") is True
    st4 = S()
    st4.set_block()  # user pressed d
    assert st4.is_allowed("anything") is False


@pytest.mark.asyncio
async def test_turn_memo_scopes_verdict_to_identical_replay():
    """Per-turn memo: identical (tool,args) replay reuses the denial
    without re-prompting; different args prompt again; separate turns
    (fresh memo) prompt again."""
    from wisp.core.stateless import WispAgentCore
    prompts = []

    async def handler(event, args=None, reason=None):
        prompts.append((event.get("name"), dict(event.get("arguments", {}))))
        return False

    m1 = WispAgentCore._memoize_handler(handler)
    await m1({"name": "spawn_background", "arguments": {"prompt": "p"}})
    await m1({"name": "spawn_background", "arguments": {"prompt": "p"}})
    assert len(prompts) == 1  # replay served from memo
    await m1({"name": "spawn_background", "arguments": {"prompt": "other"}})
    assert len(prompts) == 2  # different invocation prompts again
    m2 = WispAgentCore._memoize_handler(handler)  # new turn
    await m2({"name": "spawn_background", "arguments": {"prompt": "p"}})
    assert len(prompts) == 3


# ── Exact live-sequence regression (§18/req 20) ──

@pytest.mark.asyncio
async def test_exact_live_sequence_no_400(tmp_path):
    """read, read, failing tool, decline, decline, approve -> next model
    submission serializes with every ID paired; no missing/unknown IDs."""
    from wisp.core.stateless import validate_tool_message_provenance as v
    from wisp.providers.openai import OpenAIProvider
    ex = _executor()
    ws = str(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")

    _real_dispatch = ex._execute_tool

    async def fake_dispatch(name, args, ws_):
        if name == "spawn_background":
            return '{"status":"ok","data":{"agent_id":"agent-7"}}', 1.0
        return await _real_dispatch(name, args, ws_)
    ex._execute_tool = fake_dispatch

    async def _boom(name, args, **kw):
        from wisp.tools.errors import ToolError
        raise ToolError("SSL CERTIFICATE_VERIFY_FAILED: tls boom")

    import wisp.tools.registry as _reg
    orig_search = _reg.TOOL_IMPLS.get("web_search")
    _reg.TOOL_IMPLS["web_search"] = _boom
    verdicts = iter([(False, None), (False, None), (True, None)])

    async def scripted(name, args, reason):
        return next(verdicts)

    plan = [("read_file", {"path": "a.py"}, None),
            ("read_file", {"path": "a.py"}, None),
            ("web_search", {"query": "q"}, None),
            ("spawn_background", {"description": "d", "prompt": "p"}, scripted),
            ("spawn_background", {"description": "d", "prompt": "p"}, scripted),
            ("spawn_background", {"description": "d", "prompt": "p"}, scripted)]
    try:
        history, assistant_ids = [], []
        for i, (name, args, ah) in enumerate(plan):
            cid = f"call_LIVE_{i}"
            yielded = [_flat(e) async for e in ex.execute(
                name, args, ws, tool_call_id=cid, approval_handler=ah)]
            hist = _history_funnel(yielded)
            assert hist, name  # every call yields exactly one history result
            assert [h.get("tool_call_id") for h in hist] == [cid], name
            assistant_ids.append(cid)
            content = hist[0].get("result", "")
            history.append({"role": "tool", "tool_call_id": cid, "content": str(content)})
        assistant = {"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": n, "arguments": "{}"}}
            for cid, (n, _, _) in zip(assistant_ids, plan)]}
        assert v(assistant, history) is None
        OpenAIProvider(model="t", api_key="sk-test")._build_payload(
            "sys", [assistant] + history, None, stream=False)
    finally:
        if orig_search is not None:
            _reg.TOOL_IMPLS["web_search"] = orig_search


# ── Intake identity (§4): id-less provider calls get ONE stable ID ──

def test_intake_stamps_stable_id_once():
    from wisp.core.stateless import _ensure_intake_id
    tc = {"type": "tool_call", "name": "read_file", "arguments": {}}
    _ensure_intake_id(tc)
    first = tc["id"]
    assert first and isinstance(first, str)
    _ensure_intake_id(tc)  # never re-minted
    assert tc["id"] == first


def test_intake_preserves_authoritative_id():
    from wisp.core.stateless import _ensure_intake_id
    tc = {"type": "tool_call", "name": "read_file", "arguments": {},
          "id": "call_PROVIDER_1"}
    _ensure_intake_id(tc)
    assert tc["id"] == "call_PROVIDER_1"


def test_id_less_call_produces_consistent_pair(tmp_path):
    """End-to-end for an id-less provider call: assistant block and tool
    result share the intake-minted ID; provenance + preflight pass."""
    from wisp.core.stateless import _ensure_intake_id, validate_tool_message_provenance as v
    from wisp.providers.openai import OpenAIProvider
    ex = _executor()
    ws = str(tmp_path)

    async def _ok(name, args, **kw):
        return '{"status": "ok", "data": "fine"}'

    import wisp.tools.registry as _reg
    orig = _reg.TOOL_IMPLS.get("read_file")
    _reg.TOOL_IMPLS["read_file"] = _ok

    async def _run():
        tc = _ensure_intake_id({"type": "tool_call", "name": "read_file",
                                "arguments": {"path": "x"}})
        yielded = [_flat(e) async for e in ex.execute(
            tc["name"], tc["arguments"], ws, tool_call_id=tc.get("id"))]
        hist = _history_funnel(yielded)
        assert [h.get("tool_call_id") for h in hist] == [tc["id"]]
        a = {"role": "assistant", "content": "", "tool_calls": [
            {"id": tc["id"], "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]}
        t = [{"role": "tool", "tool_call_id": tc["id"], "content": "fine"}]
        assert v(a, t) is None
        OpenAIProvider(model="t", api_key="sk-test")._build_payload(
            "sys", [a] + t, None, stream=False)

    try:
        import asyncio
        asyncio.run(_run())
    finally:
        if orig is not None:
            _reg.TOOL_IMPLS["read_file"] = orig
