"""Phase 13-J1 contract repair tests (FAILING-FIRST → implementation).

Each test asserts the REPAIRED contract. Written before the fix;
red on baseline, green after. Sections map to the J1 spec.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _mk_config(workspace, **kw):
    from wisp.config import WispConfig
    from wisp.infra.security import PermissionMode
    base = {"workspace": str(workspace),
            "permission_mode": PermissionMode.FULL, "auto_approve": False}
    base.update(kw)
    return WispConfig().replace(**base)


def _hook_mgr():
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


class _MockProvider:
    def __init__(self, responses):
        self.responses = responses

    def generate_stream_events(self, system_prompt, messages, tools=None,
                               checkpoint_every=50):
        for resp in self.responses:
            yield resp


def _mk_core(provider, config):
    from wisp.core.engine import WispAgentCore
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import PermissionMode, SecurityPolicy
    # The gate reads the SECURITY policy, not config: mirror the mode or
    # approval behavior silently diverges from the configured posture.
    mode = getattr(config, "permission_mode", PermissionMode.FULL)
    return WispAgentCore(
        provider=provider,
        security=SecurityPolicy(permission_mode=PermissionMode(mode)),
        extensions=ExtensionHost(), config=config)


def _tasks(n, **over):
    base = {"task": "summarize a.py", "role": "researcher"}
    base.update(over)
    return [dict(base, task=f"summarize file {i}.py") for i in range(n)]


def _fanout_args(n=2, **over):
    args = {"tasks": _tasks(n), "max_concurrent": 2, "mode": "blocking"}
    args.update(over)
    return args


def _verr(args):
    core = _mk_core(_MockProvider([]), _mk_config("/tmp"))
    return core._validate_tool_args("fanout", args)


# ── A. nullable contract (§2) ─────────────────────────────────────

class TestNullableContract:
    def test_null_model_accepted(self):
        assert _verr(_fanout_args(
            tasks=[{"task": "x", "model": None}])) is None

    def test_null_timeout_accepted(self):
        assert _verr(_fanout_args(
            tasks=[{"task": "x", "timeout_seconds": None}])) is None

    def test_null_max_iterations_accepted(self):
        assert _verr(_fanout_args(
            tasks=[{"task": "x", "max_iterations": None}])) is None

    def test_omitted_fields_accepted(self):
        assert _verr({"tasks": [{"task": "x"}]}) is None

    def test_explicit_valid_accepted(self):
        assert _verr(_fanout_args(tasks=[{
            "task": "x", "role": "planner", "timeout_seconds": 60,
            "max_iterations": 5, "worktree_isolated": False,
            "model": "qwen2.5"}])) is None

    def test_malformed_model_rejected(self):
        assert _verr(_fanout_args(
            tasks=[{"task": "x", "model": 123}])) is not None

    def test_negative_timeout_rejected(self):
        assert _verr(_fanout_args(
            tasks=[{"task": "x", "timeout_seconds": -5}])) is not None

    def test_zero_timeout_means_default(self):
        # 0 is falsy → runtime falls back to role default; schema pins it.
        assert _verr(_fanout_args(
            tasks=[{"task": "x", "timeout_seconds": 0}])) is None


# ── B. additionalProperties (§4) ──────────────────────────────────

class TestAdditionalProperties:
    @pytest.mark.parametrize("key,value", [
        ("auto_approve", True),
        ("workspace", "/outside"),
        ("tools", ["run_bash"]),
        ("policy", "allow-all"),
        ("approved", True),
        ("unexpected_internal_flag", True),
    ])
    def test_undeclared_task_keys_rejected(self, key, value):
        assert _verr(_fanout_args(
            tasks=[{"task": "x", key: value}])) is not None

    def test_undeclared_top_key_rejected(self):
        assert _verr(_fanout_args(policy="allow-all")) is not None
        # Top-level auto_retry was never read by _fanout (per-task only);
        # undeclared means rejected, closing the bypass sketch.
        assert _verr(_fanout_args(auto_retry=True)) is not None

    def test_declared_auto_retry_accepted_bool_only(self):
        ok = {"tasks": [{"task": "x", "auto_retry": True}]}
        off = {"tasks": [{"task": "x", "auto_retry": False}]}
        bad = {"tasks": [{"task": "x", "auto_retry": 999999}]}
        assert _verr(ok) is None
        assert _verr(off) is None
        assert _verr(bad) is not None

    def test_label_top_level_accepted(self):
        assert _verr(_fanout_args(label="survey")) is None


# ── C. ordering: invalid never reaches approval (§5, §6) ──────────

async def _run_turn(tmp_path, provider_responses, decision,
                    permission_mode="auto_edit"):
    from wisp.infra.security import PermissionMode
    seen = []

    async def _handler(event):
        seen.append((event.get("name"), event.get("arguments")))
        return decision

    cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode(
        permission_mode))
    core = _mk_core(_MockProvider(provider_responses), cfg)
    session = {"id": "j1", "messages": [], "model": "m",
               "workspace": str(tmp_path)}
    events = [e async for e in core.turn(
        session, "analyze", approval_handler=_handler)]
    return seen, events


class TestValidationOrdering:
    @pytest.mark.asyncio
    async def test_invalid_never_reaches_approval(self, tmp_path):
        seen, events = await _run_turn(tmp_path, [
            {"type": "tool_call", "name": "fanout",
             "arguments": _fanout_args(
                 tasks=[{"task": "x", "model": 123}]), "id": "call_1"},
            {"type": "done"},
        ], decision=False)
        assert seen == []
        blob = json.dumps(events)
        assert "Schema validation failed" in blob
        assert "call_1" in blob

    @pytest.mark.asyncio
    async def test_valid_reaches_approval_as_before(self, tmp_path):
        seen, _ = await _run_turn(tmp_path, [
            {"type": "tool_call", "name": "fanout",
             "arguments": _fanout_args(), "id": "call_2"},
            {"type": "done"},
        ], decision=False)
        assert [n for n, _ in seen] == ["fanout"]

    @pytest.mark.asyncio
    async def test_validation_failure_audited(self, tmp_path):
        await _run_turn(tmp_path, [
            {"type": "tool_call", "name": "fanout",
             "arguments": _fanout_args(
                 tasks=[{"task": "x", "model": 123}]), "id": "call_3"},
            {"type": "done"},
        ], decision=True)
        audit = tmp_path / ".wisp" / "audit.jsonl"
        assert audit.exists()
        text = audit.read_text()
        assert "fanout" in text and "SCHEMA_INVALID" in text


# ── D. auto_approve/auto_retry host ownership (§3, §12) ───────────

class _StubOrchestrator:
    def __init__(self):
        self.contracts = []
        self.calls = 0

    async def run_parallel(self, contracts, max_concurrent=None):
        self.contracts = list(contracts)
        self.calls += 1
        self.max_concurrent_seen = max_concurrent
        return [SimpleNamespace(
            task_id=c.name, success=True, output="stub-done", error=None,
            files_changed=[], elapsed_seconds=0.1, tokens_used=10)
            for c in contracts]


def _mk_executor(tmp_path, **cfgkw):
    from wisp.tool_executor import ToolExecutor
    cfg = _mk_config(str(tmp_path), **cfgkw)
    te = ToolExecutor(config=cfg, hook_manager=_hook_mgr())
    stub = _StubOrchestrator()
    te.subagent_orchestrator = stub
    return te, stub


class TestAuthorityOwnership:
    @pytest.mark.asyncio
    async def test_auto_approve_true_ignored(self, tmp_path):
        te, stub = _mk_executor(tmp_path, max_subagent_branching=8)
        await te._fanout(_fanout_args(
            2, auto_approve=True), str(tmp_path))
        assert stub.contracts, "no contracts built"
        assert all(c.auto_approve is False for c in stub.contracts)

    @pytest.mark.asyncio
    async def test_auto_retry_false_disables_retries(self, tmp_path):
        # auto_retry is per-task (top-level key is undeclared by design).
        te, stub = _mk_executor(tmp_path, max_subagent_branching=8)
        await te._fanout(_fanout_args(
            2, tasks=[{"task": "a", "auto_retry": False},
                      {"task": "b", "auto_retry": False}]),
            str(tmp_path))
        assert [c.max_retries for c in stub.contracts] == [0, 0]

    @pytest.mark.asyncio
    async def test_auto_retry_default_bounded(self, tmp_path):
        te, stub = _mk_executor(tmp_path, max_subagent_branching=8)
        await te._fanout(_fanout_args(2), str(tmp_path))
        assert [c.max_retries for c in stub.contracts] == [2, 2]


# ── E. limits: tasks, concurrency, branching, depth (§8, §9, §10) ──

class TestLimits:
    @pytest.mark.asyncio
    async def test_zero_tasks_rejected(self, tmp_path):
        te, stub = _mk_executor(tmp_path)
        out = json.loads(await te._fanout(
            {"tasks": []}, str(tmp_path)))
        assert out["status"] == "error"
        assert stub.calls == 0

    @pytest.mark.asyncio
    async def test_branch_cap_enforced_default(self, tmp_path):
        te, stub = _mk_executor(tmp_path)  # branching default 3
        out = json.loads(await te._fanout(
            _fanout_args(4), str(tmp_path)))
        assert out["status"] == "error"
        assert stub.calls == 0

    @pytest.mark.asyncio
    async def test_branch_cap_host_raisable(self, tmp_path):
        te, stub = _mk_executor(tmp_path, max_subagent_branching=8)
        out = json.loads(await te._fanout(
            _fanout_args(4), str(tmp_path)))
        assert out["status"] == "ok"
        assert len(stub.contracts) == 4

    @pytest.mark.asyncio
    async def test_branch_cap_maximum_plus_one(self, tmp_path):
        te, stub = _mk_executor(tmp_path, max_subagent_branching=20)
        out = json.loads(await te._fanout(
            _fanout_args(21, max_concurrent=20), str(tmp_path)))
        assert out["status"] == "error"
        assert stub.calls == 0

    @pytest.mark.asyncio
    async def test_max_concurrent_clamped_to_tasks(self, tmp_path):
        te, stub = _mk_executor(tmp_path, max_subagent_branching=8)
        out = json.loads(await te._fanout(
            _fanout_args(2, max_concurrent=99), str(tmp_path)))
        assert out["status"] == "ok"
        assert stub.max_concurrent_seen == 2

    @pytest.mark.asyncio
    async def test_max_concurrent_zero_rejected(self, tmp_path):
        te, stub = _mk_executor(tmp_path)
        out = json.loads(await te._fanout(
            _fanout_args(2, max_concurrent=0), str(tmp_path)))
        assert out["status"] == "error"
        assert stub.calls == 0

    @pytest.mark.asyncio
    async def test_depth_refused_before_launch(self, tmp_path):
        from wisp.tool_executor import ToolExecutor
        cfg = _mk_config(str(tmp_path), max_subagent_branching=8)
        cfg = cfg.replace(_subagent_depth=2)
        te = ToolExecutor(config=cfg, hook_manager=_hook_mgr())
        stub = _StubOrchestrator()
        te.subagent_orchestrator = stub
        out = json.loads(await te._fanout(
            _fanout_args(2), str(tmp_path)))
        assert out["status"] == "error"
        assert stub.calls == 0


# ── F. real execution 1/2/4 (§15) + inheritance (§14) ─────────────

def _mock_factory_provider(monkeypatch, model="test-model"):
    from wisp.providers.mock import MockProvider
    seen = []
    mock = MockProvider(responses=["worker done"])

    def _fake_from_config(config):
        seen.append(getattr(config, "model", None))
        return mock
    monkeypatch.setattr(
        "wisp.providers.factory.ProviderFactory.from_config",
        lambda self, config: _fake_from_config(config))
    return seen


def _mk_real_orch(tmp_path):
    from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
    cfg = _mk_config(str(tmp_path), max_subagent_branching=8)
    cfg = cfg.replace(provider="mock", model="test-model")
    return SubagentOrchestrator(config=cfg, workspace=tmp_path), cfg


class TestRealExecution:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("n", [1, 2, 4])
    async def test_workers_execute(self, tmp_path, monkeypatch, n):
        from wisp.tool_executor import ToolExecutor
        orch, cfg = _mk_real_orch(tmp_path)
        _mock_factory_provider(monkeypatch)
        te = ToolExecutor(config=cfg, hook_manager=_hook_mgr())
        te.subagent_orchestrator = orch
        out = json.loads(await te._fanout(
            _fanout_args(n, max_concurrent=4), str(tmp_path)))
        assert out["status"] == "ok", out
        assert out["data"]["ok"] is True
        assert len(out["data"]["results"]) == n
        assert all(r["ok"] for r in out["data"]["results"])

    @pytest.mark.asyncio
    async def test_model_inheritance(self, tmp_path, monkeypatch):
        from wisp.tool_executor import ToolExecutor
        orch, cfg = _mk_real_orch(tmp_path)
        seen = _mock_factory_provider(monkeypatch)
        te = ToolExecutor(config=cfg, hook_manager=_hook_mgr())
        te.subagent_orchestrator = orch
        out = json.loads(await te._fanout(_fanout_args(
            2, tasks=[{"task": "a"}, {"task": "b", "model": None}],
            max_concurrent=2), str(tmp_path)))
        assert out["data"]["ok"] is True
        assert seen and all(m == "test-model" for m in seen)

    @pytest.mark.asyncio
    async def test_explicit_override(self, tmp_path, monkeypatch):
        from wisp.tool_executor import ToolExecutor
        orch, cfg = _mk_real_orch(tmp_path)
        seen = _mock_factory_provider(monkeypatch)
        te = ToolExecutor(config=cfg, hook_manager=_hook_mgr())
        te.subagent_orchestrator = orch
        out = json.loads(await te._fanout(_fanout_args(
            1, tasks=[{"task": "a", "model": "other-model"}],
            max_concurrent=1), str(tmp_path)))
        assert out["data"]["ok"] is True
        assert "other-model" in seen


# ── G. incident regression (§16) ──────────────────────────────────

class TestIncidentRegression:
    @pytest.mark.asyncio
    async def test_valid_null_model_end_to_end(self, tmp_path, monkeypatch):
        from wisp.infra.security import PermissionMode
        from wisp.tool_executor import ToolExecutor
        orch, cfg = _mk_real_orch(tmp_path)
        cfg = cfg.replace(permission_mode=PermissionMode.AUTO_EDIT)
        _mock_factory_provider(monkeypatch)
        seen = []

        async def _allow(event):
            seen.append((event.get("name"), event.get("arguments")))
            return True

        from wisp.core.engine import WispAgentCore
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import PermissionMode, SecurityPolicy
        core = WispAgentCore(
            provider=_MockProvider([
                {"type": "tool_call", "name": "fanout",
                 "arguments": {
                     "tasks": [{"task": "analyze CLI", "role": "researcher",
                                "model": None}],
                     "max_concurrent": 1, "mode": "blocking"},
                 "id": "call_inc"},
                {"type": "done"},
            ]),
            security=SecurityPolicy(permission_mode=PermissionMode.AUTO_EDIT),
            extensions=ExtensionHost(), config=cfg,
            tool_executor=ToolExecutor(
                config=cfg, hook_manager=_hook_mgr()),
        )
        core.tool_executor.subagent_orchestrator = orch
        session = {"id": "j1-inc", "messages": [], "model": "test-model",
                   "workspace": str(tmp_path)}
        events = [e async for e in core.turn(
            session, "do the analysis of this codebase a technical one",
            approval_handler=_allow)]
        assert [n for n, _ in seen] == ["fanout"]
        blob = json.dumps(events)
        assert "Schema validation failed" not in blob
        assert "call_inc" in blob


# ── H. adversarial table (§17) ────────────────────────────────────

class TestAdversarial:
    @pytest.mark.parametrize("mut,valid", [
        ({"tasks": [{"task": "x", "model": None}]}, True),
        ({"tasks": [{"task": "x", "timeout_seconds": None}]}, True),
        ({"tasks": [{"task": "x", "max_iterations": None}]}, True),
        ({"tasks": [{"task": "x", "auto_approve": True}]}, False),
        ({"tasks": [{"task": "x", "auto_retry": 999999}]}, False),
        ({"tasks": [{"task": "x", "workspace": "/outside"}]}, False),
        ({"tasks": [{"task": "x", "tools": ["run_bash"]}]}, False),
        ({"tasks": [{"task": "x", "approved": True}]}, False),
        ({"tasks": [{"task": "x", "policy": "allow-all"}]}, False),
        ({"tasks": [{"task": "x",
                     "unexpected_internal_field": True}]}, False),
    ])
    def test_adversarial_matrix(self, mut, valid):
        err = _verr(mut)
        assert (err is None) == valid


# ── I. performance (§20) ──────────────────────────────────────────

class TestPerf:
    def test_validation_latency(self):
        import timeit
        args = _fanout_args(4)
        t = timeit.timeit(lambda: _verr(args), number=200)
        assert t < 5.0
        print(f"\nvalidation {t/200*1000:.2f}ms/call")

    def test_launch_latency_stub(self, tmp_path):
        import asyncio
        te, _ = _mk_executor(tmp_path, max_subagent_branching=8)
        t0 = time.perf_counter()
        asyncio.run(te._fanout(_fanout_args(4), str(tmp_path)))
        dt = time.perf_counter() - t0
        assert dt < 10.0
        print(f"\nstub launch 4 workers: {dt*1000:.1f}ms")
