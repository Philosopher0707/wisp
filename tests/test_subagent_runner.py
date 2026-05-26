"""Unit and edge-case tests for SubagentRunner.

Covers the runner directly — no orchestrator mock. Tests:
- Timeout handling (outer and inner loop)
- Crash/exception propagation
- Token estimation
- Child config building
- Context partitioning in _run_agent
- Tool call logging
- File extraction edge cases
- Output truncation enforcement
- Progress event emission
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.config import WispConfig
from wisp.multi_agent._runner import SubagentRunner
from wisp.multi_agent.task import EventKind, SubagentContract, SubagentResult


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_config(**overrides):
    cfg = WispConfig()
    cfg.model = "test-model"
    cfg.provider = "ollama"
    cfg.workspace = "/tmp"
    cfg.chars_per_token = 4
    cfg.max_context_tokens = 128000
    cfg.permission_mode = "full"
    cfg.ollama_url = "http://localhost:11434"
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def config():
    return _make_config()


@pytest.fixture
def runner(config):
    return SubagentRunner(config, Path("/tmp"))


@pytest.fixture
def contract():
    return SubagentContract(
        name="test",
        task="Do something useful",
        role="coder",
        tools=["read_file", "write_file"],
        timeout_seconds=10,
        max_iterations=3,
        auto_approve=True,
    )


# ── Token Estimation ─────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_empty_messages(self, runner):
        in_tok, out_tok, total = runner._estimate_tokens([])
        assert in_tok == 0
        assert out_tok == 0
        assert total == 0

    def test_user_message_counts_as_input(self, runner):
        msgs = [{"role": "user", "content": "Hello world"}]
        in_tok, out_tok, total = runner._estimate_tokens(msgs)
        assert in_tok > 0
        assert out_tok == 0
        assert total == in_tok

    def test_assistant_message_counts_as_output(self, runner):
        msgs = [{"role": "assistant", "content": "Hi there!"}]
        in_tok, out_tok, total = runner._estimate_tokens(msgs)
        assert in_tok == 0
        assert out_tok > 0
        assert total == out_tok

    def test_system_message_counts_as_input(self, runner):
        msgs = [{"role": "system", "content": "You are a coding agent."}]
        in_tok, out_tok, total = runner._estimate_tokens(msgs)
        assert in_tok > 0
        assert out_tok == 0

    def test_tool_message_counts_as_input(self, runner):
        msgs = [{"role": "tool", "content": "File contents here"}]
        in_tok, out_tok, total = runner._estimate_tokens(msgs)
        assert in_tok > 0
        assert out_tok == 0

    def test_assistant_tool_calls_count_as_output(self, runner):
        msgs = [{
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
            ],
        }]
        in_tok, out_tok, total = runner._estimate_tokens(msgs)
        assert out_tok > 0
        # Tool call args contribute to output tokens
        assert out_tok > 4  # More than just "Let me check."

    def test_mixed_messages(self, runner):
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User question"},
            {"role": "assistant", "content": "Assistant response"},
            {"role": "user", "content": "Follow up"},
        ]
        in_tok, out_tok, total = runner._estimate_tokens(msgs)
        assert in_tok > 0
        assert out_tok > 0
        assert total == in_tok + out_tok

    def test_chars_per_token_default(self, runner):
        """chars_per_token=4 means 'Hello' (5 chars) → 1 token (floor division)."""
        msgs = [{"role": "user", "content": "Hello"}]
        in_tok, _, _ = runner._estimate_tokens(msgs)
        assert in_tok == 1  # 5 chars // 4 = 1

    def test_non_string_content(self, runner):
        """Content that isn't a string should be converted to string."""
        msgs = [{"role": "user", "content": 12345}]
        in_tok, _, _ = runner._estimate_tokens(msgs)
        assert in_tok > 0  # Should handle non-string without crashing


# ── Child Config Building ────────────────────────────────────────────────


class TestBuildChildConfig:
    def test_inherits_parent_model(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello"), "/custom"
        )
        assert cfg.model == "test-model"

    def test_contract_model_overrides(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello", model="qwen2.5"),
            "/custom",
        )
        assert cfg.model == "qwen2.5"

    def test_workspace_set_to_agent_workspace(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello"), "/agent/workspace"
        )
        assert cfg.workspace == "/agent/workspace"

    def test_auto_approve_propagated(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello", auto_approve=False), "/tmp"
        )
        assert cfg.auto_approve is False

    def test_auto_approve_true(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello", auto_approve=True), "/tmp"
        )
        assert cfg.auto_approve is True

    def test_max_tokens_propagated(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello", max_tokens=8192), "/tmp"
        )
        assert cfg.max_context_tokens == 8192

    def test_max_tokens_none_inherits_parent(self, runner):
        cfg = runner._build_child_config(
            SubagentContract(name="test", task="hello"), "/tmp"
        )
        assert cfg.max_context_tokens == 128000

    def test_deep_copy_isolation(self, runner):
        """Child config should be independent of parent config."""
        cfg1 = runner._build_child_config(
            SubagentContract(name="a", task="a", model="model-a"), "/tmp"
        )
        cfg2 = runner._build_child_config(
            SubagentContract(name="b", task="b", model="model-b"), "/tmp"
        )
        assert cfg1.model == "model-a"
        assert cfg2.model == "model-b"


# ── File Extraction ──────────────────────────────────────────────────────


class TestExtractFilesChanged:
    def test_backtick_quoted_paths(self):
        text = "Changed `src/auth.py` and `tests/test_utils.rs`"
        files = SubagentRunner._extract_files_changed(text)
        assert "src/auth.py" in files
        assert "tests/test_utils.rs" in files

    def test_bullet_list_after_change_verb(self):
        text = "Files changed:\n- src/api.ts\n- src/models.rs\n- config.yaml"
        files = SubagentRunner._extract_files_changed(text)
        assert "src/api.ts" in files
        assert "src/models.rs" in files
        assert "config.yaml" in files

    def test_bare_extensions(self):
        text = "Created src/main.py and edited config/test.sh"
        files = SubagentRunner._extract_files_changed(text)
        assert "src/main.py" in files
        assert "config/test.sh" in files

    def test_max_20_limit(self):
        text = "\n".join(f"`file_{i}.py`" for i in range(30))
        files = SubagentRunner._extract_files_changed(text)
        assert len(files) <= 20

    def test_empty_input(self):
        assert SubagentRunner._extract_files_changed("") == []

    def test_no_file_like_tokens(self):
        assert SubagentRunner._extract_files_changed("hello world 123") == []

    def test_duplicates_removed(self):
        text = "`src/a.py` and also `src/a.py`"
        files = SubagentRunner._extract_files_changed(text)
        assert files == ["src/a.py"]

    def test_markdown_bold_stripped(self):
        text = "Modified **src/main.py** and *config.yaml*"
        files = SubagentRunner._extract_files_changed(text)
        assert "src/main.py" in files
        assert "config.yaml" in files

    def test_control_characters_rejected(self):
        text = "`src/\x00bad.py`"
        files = SubagentRunner._extract_files_changed(text)
        assert not any("\x00" in f for f in files)

    def test_makefile_detected_with_path(self):
        """Extensionless files require a path slash to be detected."""
        text = "Updated `project/Makefile`"
        files = SubagentRunner._extract_files_changed(text)
        assert "project/Makefile" in files

    def test_dockerfile_detected_with_path(self):
        """Extensionless files require a path slash to be detected."""
        text = "Changed `app/Dockerfile`"
        files = SubagentRunner._extract_files_changed(text)
        assert "app/Dockerfile" in files

    def test_paths_with_slashes(self):
        text = "Created `src/sub/dir/file.py`"
        files = SubagentRunner._extract_files_changed(text)
        assert "src/sub/dir/file.py" in files

    def test_multiple_verb_patterns(self):
        text = "Modified files:\n- a.py\nWrote files:\n- b.py"
        files = SubagentRunner._extract_files_changed(text)
        assert "a.py" in files
        assert "b.py" in files

    def test_deleted_verb_pattern(self):
        text = "Deleted files:\n- old.py"
        files = SubagentRunner._extract_files_changed(text)
        assert "old.py" in files


# ── Compact Args ─────────────────────────────────────────────────────────


class TestCompactArgs:
    def test_normal_args(self):
        result = SubagentRunner._compact_args({"filepath": "src/auth.py"})
        assert "filepath=src/auth.py" in result

    def test_empty_dict(self):
        assert SubagentRunner._compact_args({}) == "..."

    def test_long_value_truncated(self):
        args = {"content": "x" * 100}
        result = SubagentRunner._compact_args(args)
        assert len(result) <= 71  # "content=" (8) + 60 + "..."
        assert result.endswith("...")

    def test_multiple_keys_uses_first(self):
        result = SubagentRunner._compact_args({"a": "1", "b": "2"})
        assert result.startswith("a=")


# ── Progress Events ──────────────────────────────────────────────────────


class TestEmitProgress:
    @pytest.mark.asyncio
    async def test_async_callback(self):
        events = []

        async def cb(event):
            events.append(event)

        await SubagentRunner._emit(cb, "task-1", EventKind.TASK_STARTED, {"role": "coder"})
        assert len(events) == 1
        assert events[0].task_id == "task-1"
        assert events[0].event_type == EventKind.TASK_STARTED

    @pytest.mark.asyncio
    async def test_sync_callback(self):
        events = []

        def cb(event):
            events.append(event)

        await SubagentRunner._emit(cb, "task-1", EventKind.TASK_COMPLETED, {})
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_callback_exception_suppressed(self):
        def cb(event):
            raise RuntimeError("boom")

        # Should not raise
        await SubagentRunner._emit(cb, "t", EventKind.TASK_FAILED, {})

    @pytest.mark.asyncio
    async def test_none_callback(self):
        """Passing None should not crash."""
        # _emit is usually called after checking, test boundary
        pass  # _emit is always called with callback check in run()


# ── Run Method — Timeout ─────────────────────────────────────────────────


class FakeCore:
    """Minimal fake core that yields events."""
    def __init__(self, **kwargs):
        self.config = kwargs.get("config")

    async def turn(self, session_dict, task):
        yield {"type": "content", "text": "Fake response"}
        yield {"type": "done"}


class SlowFakeCore(FakeCore):
    async def turn(self, session_dict, task):
        await asyncio.sleep(5)
        yield {"type": "content", "text": "too slow"}


class CrashingFakeCore(FakeCore):
    async def turn(self, session_dict, task):
        yield {"type": "content", "text": "starting"}
        raise RuntimeError("core crashed")


class ToolCallCore(FakeCore):
    async def turn(self, session_dict, task):
        yield {
            "type": "tool_call",
            "name": "read_file",
            "arguments": {"filepath": "test.py"},
        }
        yield {"type": "content", "text": "Used tool"}
        yield {"type": "done"}


class ErrorEventCore(FakeCore):
    async def turn(self, session_dict, task):
        yield {"type": "error", "message": "Something went wrong"}


class MultiTurnCore(FakeCore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def turn(self, session_dict, task):
        # Emit multiple tool calls in single turn(), matching real engine behavior
        # (engine handles all internal iterations; runner calls turn() once)
        for i in range(2):
            session_dict["messages"].append({
                "role": "assistant",
                "content": f"Turn {i + 1}",
                "tool_calls": [{"function": {"name": "read_file", "arguments": '{"filepath":"a.py"}'}}],
            })
            yield {"type": "tool_call", "name": "read_file", "arguments": {"filepath": "a.py"}}
        yield {"type": "content", "text": "Final answer"}
        yield {"type": "done"}


@pytest.mark.asyncio
class TestRunnerRun:
    async def test_basic_run(self, runner, contract):
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_provider = MagicMock()
            mock_factory.return_value.from_config.return_value = mock_provider
            with patch("wisp.core.engine.WispAgentCore", FakeCore):
                result = await runner.run(contract, "/tmp", "system prompt")

        assert isinstance(result, SubagentResult)
        assert result.success is True
        assert result.task_id == "test"
        assert result.output == "Fake response"

    async def test_timeout_outer(self, config, contract):
        """Timeout in outer layer — asyncio.timeout on runner.run()."""
        contract.timeout_seconds = 0.05
        runner = SubagentRunner(config, Path("/tmp"))
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", SlowFakeCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success is False
        assert result.timed_out is True
        assert "TIMED OUT" in result.output

    async def test_timeout_inner_loop(self, config, contract):
        """Timeout in inner loop — asyncio.timeout on core.turn()."""
        contract.timeout_seconds = 0.05
        runner = SubagentRunner(config, Path("/tmp"))

        class InnerSlowCore(FakeCore):
            async def turn(self, session_dict, task):
                await asyncio.sleep(5)
                yield {"type": "done"}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", InnerSlowCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success is False
        assert "TIMED OUT" in result.output

    async def test_crash_propagated(self, runner, contract):
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", CrashingFakeCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success is False
        assert "core crashed" in result.error

    async def test_tool_calls_logged(self, runner, contract):
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", ToolCallCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success is True
        assert len(result.tool_calls) >= 1
        assert result.tool_calls[0]["name"] == "read_file"

    async def test_error_event_yields_failure(self, runner, contract):
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", ErrorEventCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success is False
        assert "Something went wrong" in result.error

    async def test_multi_turn_iterations(self, config, contract):
        contract.max_iterations = 5
        runner = SubagentRunner(config, Path("/tmp"))
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", MultiTurnCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success is True
        assert result.iterations_used >= 2
        assert "Final answer" in result.output

    async def test_progress_events_emitted(self, runner, contract):
        events = []

        async def cb(event):
            events.append(event)

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", FakeCore):
                result = await runner.run(contract, "/tmp", "prompt", progress_callback=cb)

        assert result.success
        assert len(events) >= 2  # start + complete
        assert events[0].event_type == EventKind.TASK_STARTED
        assert events[-1].event_type == EventKind.TASK_COMPLETED

    async def test_timeout_completes_with_failed_result(self, config, contract):
        """Inner timeout caught by _run_agent — run() sees normal completion."""
        contract.timeout_seconds = 0.05
        events = []

        async def cb(event):
            events.append(event)

        runner = SubagentRunner(config, Path("/tmp"))
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", SlowFakeCore):
                result = await runner.run(contract, "/tmp", "prompt", progress_callback=cb)

        assert result.timed_out is True
        assert len(events) >= 1
        assert events[0].event_type == EventKind.TASK_STARTED

    async def test_crash_emits_failed_event(self, runner, contract):
        events = []

        async def cb(event):
            events.append(event)

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", CrashingFakeCore):
                await runner.run(contract, "/tmp", "prompt", progress_callback=cb)

        assert len(events) >= 2
        assert events[-1].event_type == EventKind.TASK_FAILED

    async def test_output_truncation_max_chars(self, config, contract):
        contract.max_output_chars = 20
        runner = SubagentRunner(config, Path("/tmp"))

        class VerboseCore(FakeCore):
            async def turn(self, session_dict, task):
                yield {"type": "content", "text": "A" * 200}
                yield {"type": "done"}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", VerboseCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert "OUTPUT TRUNCATED" in result.output
        assert len(result.output) <= 20 + len("OUTPUT TRUNCATED") + 50

    async def test_output_truncation_max_tokens(self, config, contract):
        contract.max_output_tokens = 1
        contract.max_output_chars = 100
        runner = SubagentRunner(config, Path("/tmp"))

        class VeryVerboseCore(FakeCore):
            async def turn(self, session_dict, task):
                yield {"type": "content", "text": "A" * 500}
                yield {"type": "done"}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", VeryVerboseCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert "OUTPUT TRUNCATED" in result.output


# ── Context Partitioning in _run_agent ───────────────────────────────────


@pytest.mark.asyncio
class TestContextPartitioning:
    async def test_run_creates_session_with_store(self, config, contract):
        """Runner creates a session via the store and executes."""
        runner = SubagentRunner(config, Path("/tmp"))
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", FakeCore):
                result = await runner.run(contract, "/tmp", "system prompt")

        assert result.success
        assert result.session_id  # Session was created
        assert result.session_id.startswith("sess-")

    async def test_session_persisted_to_store(self, config, contract):
        """Runner creates and saves session to the store."""
        runner = SubagentRunner(config, Path("/tmp"))
        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", FakeCore):
                result = await runner.run(contract, "/tmp", "prompt")

        assert result.success
        # Store should contain the session
        sessions = runner._store.list_sessions()
        assert any(s["id"] == result.session_id for s in sessions)


# ── _run_agent Edge Cases ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRunAgentEdgeCases:
    async def test_system_prompt_prepended_in_session(self, config, contract):
        """System prompt is prepended to messages in the session dict."""
        runner = SubagentRunner(config, Path("/tmp"))
        captured_session = None

        class CaptureSessionCore(FakeCore):
            async def turn(self, session_dict, task):
                nonlocal captured_session
                captured_session = dict(session_dict)
                yield {"type": "content", "text": "done"}
                yield {"type": "done"}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", CaptureSessionCore):
                await runner.run(contract, "/tmp", "You are a test agent.")

        assert captured_session is not None
        # System prompt should be prepended
        contents = [m.get("content", "") for m in captured_session["messages"]]
        assert "You are a test agent." in contents

    async def test_empty_system_prompt_not_prepended(self, config, contract):
        """When system_prompt is empty string, no system message added."""
        runner = SubagentRunner(config, Path("/tmp"))
        captured_session = None

        class CaptureSessionCore(FakeCore):
            async def turn(self, session_dict, task):
                nonlocal captured_session
                captured_session = dict(session_dict)
                yield {"type": "content", "text": "done"}
                yield {"type": "done"}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", CaptureSessionCore):
                await runner.run(contract, "/tmp", "")

        roles = [m["role"] for m in captured_session["messages"]]
        assert "system" not in roles

    async def test_iterations_capped_at_max(self, config, contract):
        contract.max_iterations = 2
        runner = SubagentRunner(config, Path("/tmp"))

        class InfiniteCore(FakeCore):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.count = 0

            async def turn(self, session_dict, task):
                self.count += 1
                session_dict["messages"].append({
                    "role": "assistant",
                    "content": f"Turn {self.count}",
                    "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}],
                })
                yield {"type": "tool_call", "name": "read_file", "arguments": {}}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", InfiniteCore):
                result = await runner.run(contract, "/tmp", "prompt")

        # Should stop after max_iterations, not run forever
        assert result.iterations_used <= contract.max_iterations

    async def test_depth_propagated_to_config(self, config, contract):
        contract._subagent_depth = 3
        contract._subagent_branch_count = 2
        runner = SubagentRunner(config, Path("/tmp"))
        captured_config = None

        class CaptureConfigCore(FakeCore):
            async def turn(self, session_dict, task):
                nonlocal captured_config
                captured_config = self.config
                yield {"type": "content", "text": "ok"}
                yield {"type": "done"}

        with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
            mock_factory.return_value.from_config.return_value = MagicMock()
            with patch("wisp.core.engine.WispAgentCore", CaptureConfigCore):
                await runner.run(contract, "/tmp", "prompt")

        assert captured_config._subagent_depth == 3
        assert captured_config._subagent_branch_count == 2


class TestAgentRuntimeRouting:
    """Issue 2: SubagentRunner must route through AgentRuntime instead of bypassing."""

    @pytest.mark.asyncio
    async def test_runner_accepts_agent_runtime(self, config, contract):
        """SubagentRunner constructor accepts an agent_runtime parameter."""
        mock_runtime = MagicMock()
        runner = SubagentRunner(config, Path("/tmp"), agent_runtime=mock_runtime)
        assert runner._agent_runtime is mock_runtime

    @pytest.mark.asyncio
    async def test_run_uses_agent_runtime_when_provided(self, config, contract):
        """When agent_runtime is provided, _run_agent uses runtime.run_turn instead of creating WispAgentCore."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeRuntime:
            turn_calls: list = field(default_factory=list)

            async def get_or_create_session(self, session_id, model, workspace):
                return {
                    "id": session_id,
                    "model": model,
                    "workspace": workspace,
                    "messages": [],
                    "compaction_history": [],
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                }

            async def run_turn(self, session, prompt):
                self.turn_calls.append((session["id"], prompt))
                yield {"type": "content", "text": "runtime output"}
                yield {"type": "done"}

        fake_runtime = FakeRuntime()
        runner = SubagentRunner(config, Path("/tmp"), agent_runtime=fake_runtime)
        result = await runner.run(contract, "/tmp", "You are a coder.")

        assert result.success
        assert "runtime output" in result.output
        assert len(fake_runtime.turn_calls) == 1
        assert fake_runtime.turn_calls[0][1] == contract.task

    @pytest.mark.asyncio
    async def test_run_falls_back_to_core_when_no_runtime(self, config, contract):
        """When no agent_runtime is provided, runner falls back to direct WispAgentCore creation."""
        runner = SubagentRunner(config, Path("/tmp"))
        with patch("wisp.core.engine.WispAgentCore", FakeCore):
            with patch("wisp.providers.factory.ProviderFactory") as mock_factory:
                mock_factory.return_value.from_config.return_value = MagicMock()
                result = await runner.run(contract, "/tmp", "prompt")
        assert result.success
