"""TDD tests for tool_executor bugs found during code review.

Bug 2: FULL permission mode forces approval when auto_approve=False.
Bug 3: PRE_FILE_WRITE / PRE_BASH hooks fire twice (in execute() + _execute_tool()).
Bug 4: _run_write_verify calls sync tool_run_tests, blocking the event loop.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.config import WispConfig, PermissionMode
from wisp.tool_executor import ToolExecutor


def _mk_config(workspace: str, permission_mode=PermissionMode.FULL, auto_approve=False):
    cfg = WispConfig()
    cfg = cfg.replace(workspace=workspace, permission_mode=permission_mode, auto_approve=auto_approve)
    return cfg


def _make_async_hook_mgr():
    """Create a hook_manager mock where arun_hooks can be awaited."""
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _patch_to_thread():
    """Patch asyncio.to_thread so real tool execution is skipped but
    _execute_tool's real code path (hooks, write_verify) runs."""
    return patch("asyncio.to_thread", new_callable=AsyncMock,
                 return_value='{"status": "ok"}'.replace("'", '"'))


# ── Bug 2: FULL permission mode ────────────────────────────────────────


class TestFullPermissionMode:
    """In FULL mode, write tools should auto-approve — user chose 'no restrictions'."""

    def test_needs_forced_approval_returns_false_in_full_mode(self):
        """FULL mode: _needs_forced_approval should return False for all tools."""
        cfg = _mk_config("/tmp/test", permission_mode=PermissionMode.FULL)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        assert te._needs_forced_approval("write_file") is False
        assert te._needs_forced_approval("edit_file") is False
        assert te._needs_forced_approval("edit_file_multi") is False
        assert te._needs_forced_approval("run_bash") is False
        assert te._needs_forced_approval("git_push") is False

    def test_needs_forced_approval_bash_in_auto_edit_mode(self):
        """AUTO_EDIT mode: bash and git writes need approval, file edits don't."""
        cfg = _mk_config("/tmp/test", permission_mode=PermissionMode.AUTO_EDIT)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        assert te._needs_forced_approval("run_bash") is True
        assert te._needs_forced_approval("git_push") is True
        assert te._needs_forced_approval("write_file") is False
        assert te._needs_forced_approval("edit_file") is False

    def test_needs_forced_approval_all_in_ask_all_mode(self):
        """ASK_ALL mode: all write tools need approval."""
        cfg = _mk_config("/tmp/test", permission_mode=PermissionMode.ASK_ALL)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        assert te._needs_forced_approval("write_file") is True
        assert te._needs_forced_approval("edit_file") is True
        assert te._needs_forced_approval("run_bash") is True

    @pytest.mark.asyncio
    async def test_full_mode_skips_approval_handler(self, tmp_path):
        """FULL mode: write tools should NOT invoke approval handler.
        FULL = no restrictions, even when auto_approve=False."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=False)
        approval_handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        with _patch_to_thread() as mock_thread:
            with patch.object(te, "_run_write_verify", new_callable=AsyncMock, return_value=""):
                with patch.object(te, "_run_post_tool_hooks", new_callable=AsyncMock):
                    events = []
                    async for ev in te.execute(
                        "edit_file",
                        {"path": str(tmp_path / "test.py"), "old_string": "a", "new_string": "b"},
                        str(tmp_path),
                        approval_handler=approval_handler,
                    ):
                        events.append(ev)

        # In FULL mode, the approval handler should NOT have been called
        approval_handler.assert_not_called()
        # Tool should have executed (asyncio.to_thread was called)
        mock_thread.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_edit_mode_calls_approval_for_bash(self, tmp_path):
        """AUTO_EDIT mode: bash should call approval handler."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.AUTO_EDIT, auto_approve=False)
        approval_handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        with _patch_to_thread():
            with patch.object(te, "_run_post_tool_hooks", new_callable=AsyncMock):
                events = []
                async for ev in te.execute(
                    "run_bash",
                    {"command": "echo hi"},
                    str(tmp_path),
                    approval_handler=approval_handler,
                ):
                    events.append(ev)

        # In AUTO_EDIT mode, bash should call approval handler
        approval_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_edit_mode_skips_approval_for_file_edit(self, tmp_path):
        """AUTO_EDIT mode: file edits auto-approved, bash needs approval."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.AUTO_EDIT, auto_approve=True)
        approval_handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        with _patch_to_thread() as mock_thread:
            with patch.object(te, "_run_write_verify", new_callable=AsyncMock, return_value=""):
                with patch.object(te, "_run_post_tool_hooks", new_callable=AsyncMock):
                    events = []
                    async for ev in te.execute(
                        "edit_file",
                        {"path": str(tmp_path / "test.py"), "old_string": "a", "new_string": "b"},
                        str(tmp_path),
                        approval_handler=approval_handler,
                    ):
                        events.append(ev)

        # In AUTO_EDIT mode, file edits should NOT call approval handler
        approval_handler.assert_not_called()
        mock_thread.assert_called_once()


# ── Bug 3: duplicate hook calls ────────────────────────────────────────


class TestNoDuplicateHooks:
    """PRE_FILE_WRITE and PRE_BASH hooks must fire exactly once per tool call.

    We patch asyncio.to_thread (not _execute_tool) so the real _execute_tool
    code runs, including any duplicate hook calls.
    """

    @pytest.mark.asyncio
    async def test_pre_file_hooks_fire_once_for_edit_file(self, tmp_path):
        """edit_file: _run_pre_file_hooks called once in full execute() flow."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        hook_mgr = _make_async_hook_mgr()
        te = ToolExecutor(config=cfg, hook_manager=hook_mgr)

        with _patch_to_thread():
            with patch.object(te, "_run_write_verify", new_callable=AsyncMock, return_value=""):
                events = []
                async for ev in te.execute(
                    "edit_file",
                    {"path": str(tmp_path / "test.py"), "old_string": "a", "new_string": "b"},
                    str(tmp_path),
                ):
                    events.append(ev)

        pre_file_calls = [
            c for c in hook_mgr.arun_hooks.call_args_list
            if c.args and "pre_file_write" in str(c.args[0]).lower()
        ]
        assert len(pre_file_calls) == 1, (
            f"Expected 1 PRE_FILE_WRITE hook call, got {len(pre_file_calls)}"
        )

    @pytest.mark.asyncio
    async def test_pre_bash_hooks_fire_once_for_run_bash(self, tmp_path):
        """run_bash: _run_pre_bash_hooks called once in full execute() flow."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        hook_mgr = _make_async_hook_mgr()
        te = ToolExecutor(config=cfg, hook_manager=hook_mgr)

        with _patch_to_thread():
            events = []
            async for ev in te.execute(
                "run_bash",
                {"command": "echo hi"},
                str(tmp_path),
            ):
                events.append(ev)

        pre_bash_calls = [
            c for c in hook_mgr.arun_hooks.call_args_list
            if c.args and "pre_bash" in str(c.args[0]).lower()
        ]
        assert len(pre_bash_calls) == 1, (
            f"Expected 1 PRE_BASH hook call, got {len(pre_bash_calls)}"
        )

    @pytest.mark.asyncio
    async def test_pre_tool_hooks_fire_once_for_read_tool(self, tmp_path):
        """Read tools: only PRE_TOOL_USE fires, once."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        hook_mgr = _make_async_hook_mgr()
        te = ToolExecutor(config=cfg, hook_manager=hook_mgr)

        with _patch_to_thread():
            events = []
            async for ev in te.execute(
                "read_file",
                {"path": str(tmp_path / "test.py")},
                str(tmp_path),
            ):
                events.append(ev)

        pre_tool_calls = [
            c for c in hook_mgr.arun_hooks.call_args_list
            if c.args and "pre_tool_use" in str(c.args[0]).lower()
        ]
        assert len(pre_tool_calls) == 1, (
            f"Expected 1 PRE_TOOL_USE hook call, got {len(pre_tool_calls)}"
        )


# ── Bug 4: _run_write_verify blocks event loop ─────────────────────────


class TestWriteVerifyNonBlocking:
    """_run_write_verify must not block the asyncio event loop."""

    @pytest.mark.asyncio
    async def test_write_verify_does_not_block_event_loop(self, tmp_path):
        """Concurrent tasks make progress while _run_write_verify runs."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        task_started = asyncio.Event()
        task_done = asyncio.Event()

        async def concurrent_task():
            task_started.set()
            await asyncio.sleep(0.01)
            task_done.set()
            return "concurrent_result"

        with patch("wisp.tools.lsp.tool_lsp_diagnostics", return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests", return_value="## Test Results (1/1 passed)"):
                result, concurrent = await asyncio.gather(
                    te._run_write_verify("test.py", str(tmp_path)),
                    concurrent_task(),
                )

        assert task_started.is_set()
        assert task_done.is_set()
        assert concurrent == "concurrent_result"

    @pytest.mark.asyncio
    async def test_write_verify_delegates_sync_to_thread_pool(self, tmp_path):
        """_run_write_verify must wrap slow sync calls in asyncio.to_thread.

        Before fix: tool_lsp_diagnostics and tool_run_tests called directly
        on the event loop thread, blocking it.
        After fix: delegated to asyncio.to_thread.
        """
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        real_to_thread = asyncio.to_thread
        to_thread_calls = []

        async def tracking_to_thread(fn, *args, **kwargs):
            to_thread_calls.append(fn.__name__ if hasattr(fn, '__name__') else str(fn))
            return await real_to_thread(fn, *args, **kwargs)

        with patch("wisp.tools.lsp.tool_lsp_diagnostics", return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests", return_value="## Test Results (1/1 passed)"):
                with patch("asyncio.to_thread", side_effect=tracking_to_thread):
                    await te._run_write_verify(str(test_file), str(tmp_path))

        assert len(to_thread_calls) >= 1, (
            f"Expected asyncio.to_thread calls for sync operations, got {len(to_thread_calls)}. "
            "Sync functions must be delegated to thread pool to avoid blocking event loop."
        )


# ── MCP bare-name shadowing: builtins must always win ─────────────────


class _FakeMCPTool:
    def __init__(self, name):
        self.name = name


class _FakeMCPManager:
    def __init__(self, names):
        self._tools = [_FakeMCPTool(n) for n in names]

    def get_all_tools(self):
        return self._tools

    def call_tool(self, name, args):
        return "MCP-RESULT"


def _collect(te, tool_name, args, workspace="/tmp"):
    async def _run():
        out = []
        async for ev in te.execute(tool_name, args, workspace):
            out.append(ev)
        return out
    return asyncio.run(_run())


def _mcp_targets(to_thread_mock):
    """First positional arg of every to_thread call that targets MCP."""
    import wisp.tools.registry as reg
    return [c.args[0] for c in to_thread_mock.call_args_list
            if c.args and c.args[0] not in (reg.execute_tool,)]


def test_builtin_beats_shadowing_mcp_bare_name(tmp_path):
    """An MCP server advertising a builtin's name must not hijack dispatch."""
    from wisp.tool_executor import ToolExecutor
    import wisp.tools.registry as reg

    mgr = _FakeMCPManager(["read_file"])
    cfg = _mk_config(str(tmp_path), PermissionMode.FULL, auto_approve=True)
    te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr(), mcp=mgr)

    with patch("asyncio.to_thread", new_callable=AsyncMock,
               return_value='{"status": "ok", "data": "builtin"}') as tt:
        events = _collect(te, "read_file", {"path": "/tmp/x"})

    dispatched = [c.args[0] for c in tt.call_args_list if c.args]
    assert reg.execute_tool in dispatched, (
        f"builtin execute_tool must run for a shadowed bare name; got {dispatched}"
    )
    assert _mcp_targets(tt) == [], (
        f"shadowed bare name must not reach MCP; got {_mcp_targets(tt)}"
    )
    results = [e for e in events if getattr(e, "type", "") == "tool_result"]
    assert results, "shadowed builtin must still produce a tool_result event"
    assert "mcp" not in str(results[0]).lower() or "collide" in str(results[0]).lower(), (
        f"result should come from the builtin path: {results[0]}"
    )


def test_prefixed_mcp_still_routes_to_server(tmp_path):
    """Canonical prefixed form reaches the MCP server regardless of collisions."""
    from wisp.tool_executor import ToolExecutor

    mgr = _FakeMCPManager(["read_file"])
    cfg = _mk_config(str(tmp_path), PermissionMode.FULL, auto_approve=True)
    te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr(), mcp=mgr)

    with patch("asyncio.to_thread", new_callable=AsyncMock,
               return_value="MCP-RESULT") as tt:
        _collect(te, "mcp:srv/read_file", {"path": "/tmp/x"})

    targets = _mcp_targets(tt)
    assert len(targets) == 1 and targets[0] == mgr.call_tool
    call = next(c for c in tt.call_args_list if c.args and c.args[0] == mgr.call_tool)
    assert call.args[1] == "mcp:srv/read_file"


def test_noncolliding_bare_name_routes_to_mcp(tmp_path):
    """A pure MCP tool name (no builtin collision) still routes to MCP."""
    from wisp.tool_executor import ToolExecutor

    mgr = _FakeMCPManager(["acme_widget_spin"])
    cfg = _mk_config(str(tmp_path), PermissionMode.FULL, auto_approve=True)
    te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr(), mcp=mgr)

    with patch("asyncio.to_thread", new_callable=AsyncMock,
               return_value="MCP-RESULT") as tt:
        _collect(te, "acme_widget_spin", {"query": "x"})

    targets = _mcp_targets(tt)
    assert len(targets) == 1 and targets[0] == mgr.call_tool


def test_shadowed_builtin_not_forced_to_approval(tmp_path):
    """The forced-approval gate must agree with dispatch: a builtin whose
    name collides keeps builtin approval semantics (auto in FULL mode),
    not MCP's always-approve rule."""
    from wisp.tool_executor import ToolExecutor

    mgr = _FakeMCPManager(["read_file", "acme_widget_spin"])
    cfg = _mk_config(str(tmp_path), PermissionMode.FULL, auto_approve=True)
    te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr(), mcp=mgr)

    assert te._is_external_call("read_file") is False
    assert te._needs_forced_approval("read_file") is False
    # ...while a genuine MCP name stays gated
    assert te._is_external_call("acme_widget_spin") is True
    assert te._needs_forced_approval("acme_widget_spin") is True


# ── Depth inheritance: spawn/fanout stamp child contracts ────────────


def _fake_orchestrator(results):
    fake = MagicMock()
    calls = {"retry": [], "parallel": []}

    async def _retry(contract):
        calls["retry"].append(contract)
        return results[0]

    async def _parallel(contracts, max_concurrent=4, **kw):
        calls["parallel"].append(list(contracts))
        return results

    fake._run_with_retry = AsyncMock(side_effect=_retry)
    fake.run_parallel = AsyncMock(side_effect=_parallel)
    fake.calls = calls
    return fake


def test_spawn_contract_inherits_parent_depth(tmp_path):
    """A subagent running at depth 1 must spawn children stamped depth 2 —
    otherwise the orchestrator's recursion guard can never trip."""
    from wisp.tool_executor import ToolExecutor
    from wisp.multi_agent.task import SubagentResult

    cfg = _mk_config(str(tmp_path), PermissionMode.FULL, auto_approve=True)
    object.__setattr__(cfg, "_subagent_depth", 1)
    orch = _fake_orchestrator([SubagentResult(task_id="s", success=True, output="ok")])
    te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr(),
                      subagent_orchestrator=orch)

    _collect(te, "spawn", {"role": "coder", "task": "x"})

    assert orch.calls["retry"], "spawn must dispatch through the orchestrator"
    contract = orch.calls["retry"][0]
    assert getattr(contract, "_subagent_depth", 0) == 2


def test_fanout_contracts_inherit_incremented_depth(tmp_path):
    from wisp.tool_executor import ToolExecutor
    from wisp.multi_agent.task import SubagentResult

    cfg = _mk_config(str(tmp_path), PermissionMode.FULL, auto_approve=True)
    object.__setattr__(cfg, "_subagent_depth", 0)
    orch = _fake_orchestrator([
        SubagentResult(task_id=f"f{i}", success=True, output="ok") for i in range(2)
    ])
    te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr(),
                      subagent_orchestrator=orch)

    _collect(te, "fanout", {"tasks": [{"task": "a"}, {"task": "b"}]})

    assert orch.calls["parallel"], "fanout must call run_parallel"
    contracts = orch.calls["parallel"][0]
    depths = [getattr(c, "_subagent_depth", 0) for c in contracts]
    assert depths == [1, 1], f"all fanout children inherit depth+1, got {depths}"

class TestRepeatCallGuard:
    """Identical web_fetch/web_search calls: serve cache once, then block.

    Live evidence: a looping model re-fetched one URL for minutes,
    burning iterations and tripping API rate limits."""

    def _mk(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.tool_executor import ToolExecutor
        return ToolExecutor(
            config=WispConfig().replace(workspace=str(tmp_path), auto_approve=True),
            hook_manager=MagicMock(),
        )

    def _seed(self, te, args, count=0):
        import time as _t
        key = te._repeat_key("web_fetch", args)
        te._repeat_cache[key] = (_t.monotonic(), "CACHED_BODY", count)
        return key

    @pytest.mark.asyncio
    async def test_first_repeat_serves_cached_copy_with_nudge(self, tmp_path):
        te = self._mk(tmp_path)
        args = {"url": "https://x.example/a.html"}
        self._seed(te, args)
        events = []
        async for ev in te.execute("web_fetch", args, str(tmp_path)):
            events.append(ev)
        assert len(events) == 1  # short-circuited before hooks/dispatch
        raw = events[0].data.get("result", "")
        text = raw if isinstance(raw, str) else json.dumps(raw)
        assert "[REPEAT]" in text and "CACHED_BODY" in text

    @pytest.mark.asyncio
    async def test_third_identical_call_blocked_with_instruction(self, tmp_path):
        te = self._mk(tmp_path)
        args = {"url": "https://x.example/b.html"}
        self._seed(te, args, count=2)  # two prior identical calls
        events = []
        async for ev in te.execute("web_fetch", args, str(tmp_path)):
            events.append(ev)
        raw = events[0].data.get("result", "")
        text = raw if isinstance(raw, str) else json.dumps(raw)
        assert "[REPEAT BLOCKED]" in text
        assert "synthesize" in text.lower()

    def test_different_args_not_guarded(self, tmp_path):
        te = self._mk(tmp_path)
        assert te._check_repeat_call(
            "web_fetch", {"url": "https://x.example/c.html"}) is None

    def test_success_recorded_for_guarded_tools_only(self, tmp_path):
        te = self._mk(tmp_path)
        args = {"url": "https://x.example/d.html"}
        assert te._check_repeat_call("web_fetch", args) is None
        te._record_repeat_result("web_fetch", "PAGE_BODY")
        key = te._repeat_key("web_fetch", args)
        assert key in te._repeat_cache
        # Non-guarded tools never cached even if recorded.
        assert te._check_repeat_call("run_bash", {"command": "ls"}) is None
        te._record_repeat_result("run_bash", "out")
        assert not any(k.startswith("run_bash:") for k in te._repeat_cache)

    def test_expired_entries_refetch(self, tmp_path):
        import time as _t
        te = self._mk(tmp_path)
        args = {"url": "https://x.example/e.html"}
        key = te._repeat_key("web_fetch", args)
        te._repeat_cache[key] = (_t.monotonic() - 10_000.0, "STALE", 0)
        assert te._check_repeat_call("web_fetch", args) is None

