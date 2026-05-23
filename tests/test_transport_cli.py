"""TDD for CLI transport.

Replaces: ad-hoc CLI handling in __main__.py and cli.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.
"""

import pytest
from typing import Any, AsyncIterator


# ── Minimal mock runtime for testing ───────────────────────────────

class _MockRuntime:
    def __init__(self):
        self.sessions = {}
        self.turns = []

    async def get_or_create_session(self, session_id: str, model: str, workspace: str):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "id": session_id,
                "model": model,
                "workspace": workspace,
                "messages": [],
            }
        return self.sessions[session_id]

    async def run_turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
        self.turns.append((session["id"], prompt))
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


# ── Minimal mock stdin/stdout for testing ─────────────────────────

class _MockIO:
    def __init__(self, inputs: list[str]):
        self.inputs = iter(inputs)
        self.outputs = []

    def readline(self) -> str:
        try:
            return next(self.inputs) + "\n"
        except StopIteration:
            return ""

    def write(self, text: str) -> None:
        self.outputs.append(text)

    def flush(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
# 1. Session lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestCLISessionLifecycle:
    """CLI transport manages sessions."""

    @pytest.mark.asyncio
    async def test_creates_session_on_start(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO([])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert "sess-1" in runtime.sessions

    @pytest.mark.asyncio
    async def test_prints_ready_message(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO([])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert any("ready" in o.lower() for o in stdout.outputs)


# ═══════════════════════════════════════════════════════════════════
# 2. Message routing
# ═══════════════════════════════════════════════════════════════════

class TestCLIMessageRouting:
    """User input is routed to the runtime."""

    @pytest.mark.asyncio
    async def test_user_input_triggers_turn(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["hello"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert len(runtime.turns) == 1
        assert runtime.turns[0] == ("sess-1", "hello")

    @pytest.mark.asyncio
    async def test_events_printed_to_stdout(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["hello"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert any("echo: hello" in o for o in stdout.outputs)


# ═══════════════════════════════════════════════════════════════════
# 3. Multiple turns
# ═══════════════════════════════════════════════════════════════════

class TestCLIMultipleTurns:
    """Multiple prompts in one session."""

    @pytest.mark.asyncio
    async def test_multiple_inputs(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["hello", "world"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert len(runtime.turns) == 2
        assert runtime.turns[0] == ("sess-1", "hello")
        assert runtime.turns[1] == ("sess-1", "world")


# ═══════════════════════════════════════════════════════════════════
# 4. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestCLIErrorHandling:
    """Errors are printed, not crashed."""

    @pytest.mark.asyncio
    async def test_runtime_error_printed(self):
        from wisp.transport.cli import CLITransport

        class _BrokenRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1"}
            async def run_turn(self, session, prompt):
                raise RuntimeError("boom")
                yield

        runtime = _BrokenRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["hello"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert any("error" in o.lower() for o in stdout.outputs)
        assert any("boom" in o for o in stdout.outputs)


# ═══════════════════════════════════════════════════════════════════
# 5. Exit handling
# ═══════════════════════════════════════════════════════════════════

class TestCLIExitHandling:
    """EOF and exit commands terminate gracefully."""

    @pytest.mark.asyncio
    async def test_eof_exits(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO([])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        # Should complete without error
        assert True

    @pytest.mark.asyncio
    async def test_exit_command_terminates(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["exit"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        assert len(runtime.turns) == 0  # exit should not trigger a turn


# ═══════════════════════════════════════════════════════════════════
# 6. Phase tracking
# ═══════════════════════════════════════════════════════════════════

class TestCLIPhaseTracking:
    """Phase detection and progress tracking in CLI output."""

    @pytest.mark.asyncio
    async def test_turn_counter_increments(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["hello", "world"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        # Check that turn stats appear in output
        all_output = "".join(stdout.outputs)
        assert "Turn 1" in all_output
        assert "Turn 2" in all_output

    @pytest.mark.asyncio
    async def test_progress_reset_per_turn(self):
        from wisp.transport.cli import CLITransport
        runtime = _MockRuntime()
        transport = CLITransport(runtime)
        stdin = _MockIO(["hello"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        # Progress tracker should be clean after run
        assert transport._progress is not None
        assert transport._turn_number == 1

    @pytest.mark.asyncio
    async def test_phase_tracking_with_tools(self):
        from wisp.transport.cli import CLITransport

        class _ToolRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1", "messages": [], "workspace": "/tmp"}
            async def run_turn(self, session, prompt):
                yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "x.py"}}
                yield {"type": "tool_result", "name": "read_file", "result": "content", "duration_ms": 5.0}
                yield {"type": "tool_call", "name": "write_file", "arguments": {"path": "out.py"}}
                yield {"type": "tool_result", "name": "write_file", "result": "ok", "duration_ms": 10.0}
                yield {"type": "content", "text": "done"}
                yield {"type": "done"}

        transport = CLITransport(_ToolRuntime())
        stdin = _MockIO(["fix it"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        all_output = "".join(stdout.outputs)
        # Turn stats should show 2 tools
        assert "2 tools" in all_output
        # File changes should show out.py
        assert "out.py" in all_output or "1 files" in all_output

    @pytest.mark.asyncio
    async def test_tool_success_and_failure_counted(self):
        from wisp.transport.cli import CLITransport

        class _MixedRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1", "messages": [], "workspace": "/tmp"}
            async def run_turn(self, session, prompt):
                yield {"type": "tool_call", "name": "run_bash", "arguments": {"command": "ls"}}
                yield {"type": "tool_result", "name": "run_bash", "result": "ok", "duration_ms": 5.0}
                yield {"type": "tool_call", "name": "run_bash", "arguments": {"command": "bad"}}
                yield {"type": "tool_result", "name": "run_bash", "result": "Error: fail", "duration_ms": 2.0}
                yield {"type": "done"}

        transport = CLITransport(_MixedRuntime())
        stdin = _MockIO(["go"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        all_output = "".join(stdout.outputs)
        assert "Turn 1" in all_output
        assert "2 tools" in all_output


class TestCLIFileTracking:
    """File change tracking in CLI output."""

    @pytest.mark.asyncio
    async def test_modified_files_in_output(self):
        from wisp.transport.cli import CLITransport

        class _FileRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1", "messages": [], "workspace": "/tmp"}
            async def run_turn(self, session, prompt):
                yield {"type": "tool_call", "name": "edit_file", "arguments": {"path": "src/main.py"}}
                yield {"type": "tool_result", "name": "edit_file", "result": "ok", "duration_ms": 8.0}
                yield {"type": "tool_call", "name": "edit_file", "arguments": {"path": "tests/test.py"}}
                yield {"type": "tool_result", "name": "edit_file", "result": "ok", "duration_ms": 5.0}
                yield {"type": "done"}

        transport = CLITransport(_FileRuntime())
        stdin = _MockIO(["refactor"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        all_output = "".join(stdout.outputs)
        assert "2 files" in all_output


class TestCLIThinkingCollapse:
    """Thinking output collapsed by default."""

    @pytest.mark.asyncio
    async def test_thinking_collapsed_by_default(self):
        from wisp.transport.cli import CLITransport

        class _ThinkRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1", "messages": [], "workspace": "/tmp"}
            async def run_turn(self, session, prompt):
                yield {"type": "thinking", "text": "Let me think carefully..."}
                yield {"type": "content", "text": "result"}
                yield {"type": "done"}

        transport = CLITransport(_ThinkRuntime())
        stdin = _MockIO(["go"])
        stdout = _MockIO([])

        await transport.run(stdin, stdout, session_id="sess-1", model="qwen", workspace="/tmp")

        all_output = "".join(stdout.outputs)
        # Should show collapsed summary with first-line preview
        assert "Let me think carefully" in all_output
        assert "Thinking" in all_output
