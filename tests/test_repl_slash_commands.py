"""Test slash commands via dispatch() — the canonical command path.

The REPL loop (entry._run_repl) drives commands via dispatch().
Tests here validate command behavior in isolation.
"""

import pytest
from unittest.mock import MagicMock
from wisp.commands import dispatch
from wisp.transport.cli import AgentAdapter


class FakeRuntime:
    def __init__(self):
        self.store = MagicMock()
        self.telemetry = MagicMock()
        self.security = MagicMock()

    async def maybe_compact(self, session, force=False):
        return None

    def _get_core(self):
        return MagicMock()


class FakeConfig:
    model = "test-model"
    workspace = "/tmp"
    show_thinking = False
    auto_approve = False
    max_context_tokens = 128000
    chars_per_token = 4
    permission_mode = "auto"

    def replace(self, **kwargs):
        import copy
        inst = copy.copy(self)
        for k, v in kwargs.items():
            setattr(inst, k, v)
        return inst


def _make_adapter(session_id="test-session"):
    session = {
        "id": session_id,
        "model": "test-model",
        "workspace": "/tmp",
        "messages": [],
    }
    return AgentAdapter(FakeRuntime(), FakeConfig(), session)


class TestHelpCommand:
    def test_help_lists_commands(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/help", adapter)
        assert result is True  # Consumed
        output = capsys.readouterr().out
        assert "Available commands" in output or "/help" in output


class TestClearCommand:
    def test_clear_empties_messages(self, capsys):
        adapter = _make_adapter()
        adapter.messages.extend([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        result = dispatch("/clear", adapter)
        assert result is True  # Consumed
        assert len(adapter.messages) == 0


class TestSessionCommand:
    def test_session_shows_id(self, capsys):
        adapter = _make_adapter(session_id="abc-123")
        result = dispatch("/session", adapter)
        assert result is True  # Consumed
        output = capsys.readouterr().out
        assert "abc-123" in output


class TestExitCommand:
    def test_exit_raises(self):
        from wisp.exceptions import ExitREPL
        adapter = _make_adapter()
        with pytest.raises(ExitREPL):
            dispatch("/exit", adapter)


class TestThinkingCommand:
    def test_thinking_toggles(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/thinking", adapter)
        assert result is True  # Consumed


class TestUnknownCommand:
    def test_unknown_shows_error(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/unknown_cmd", adapter)
        # Unknown commands are consumed (True) with an error message
        assert result is True
        output = capsys.readouterr().out
        assert "Unknown command" in output


class TestContinueCommand:
    def test_continue_returns_prompt(self):
        adapter = _make_adapter()
        adapter.messages.append({"role": "assistant", "content": "Here is the code..."})
        result = dispatch("/continue", adapter)
        # /continue should return a string prompt (not True/False)
        assert isinstance(result, str)
        assert "continue" in result.lower() or "Context" in result

    def test_continue_no_history(self, capsys):
        adapter = _make_adapter()
        result = dispatch("/continue", adapter)
        # Should return True (consumed with warning) since no history
        assert result is True


class TestCompactCommand:
    def test_compact_with_few_messages(self, capsys):
        adapter = _make_adapter()
        # Session with only a few messages — should skip compaction
        result = dispatch("/compact", adapter)
        assert result is True  # Consumed

class TestHelpShowsEveryCommand:
    """`/help` listed only the built-in handlers and pointed at `/doctor` for the rest.

    The other 23 — `/thinking` among them — were therefore **undiscoverable**, and the renderer
    printed *"use /thinking to expand"* for a command `/help` never mentioned. Reported by the user
    as *"i cant toggle the tinking, there is no cmd to do this"*: there was one, and `/help` hid it.
    """

    def _help_text(self, cmd: str) -> str:
        from wisp.cli.dispatcher import Dispatcher, ReplContext

        adapter = _make_adapter()
        ctx = ReplContext(runtime=FakeRuntime(), transport=MagicMock(), session=adapter.session,
                          config=adapter.config, out=[], adapter=adapter)
        Dispatcher(legacy_dispatch=dispatch).dispatch(ctx, cmd)
        return "\n".join(ctx.out)

    def test_lists_the_legacy_commands_too(self):
        text = self._help_text("/help")
        for name in ("thinking", "approve", "read", "ls", "spawn", "swarm"):
            assert f"/{name}" in text, f"/help does not list /{name}"

    def test_lists_the_built_ins(self):
        text = self._help_text("/help")
        for name in ("help", "model", "provider", "rewind"):
            assert f"/{name}" in text

    def test_explains_a_single_command(self):
        text = self._help_text("/help thinking")
        assert "/thinking" in text and "usage" in text.lower()

    def test_unknown_command_says_so(self):
        assert "no such command" in self._help_text("/help wibble").lower()


class TestThinkingAcceptsAnExplicitValue:
    """The bare toggle always worked; making the value explicit also makes it scriptable, and
    removes the "which way does it toggle?" question."""

    def test_off_then_on(self):
        from wisp.repl.commands.agents import cmd_thinking

        adapter = _make_adapter()
        adapter.config = adapter.config.replace(show_thinking=True)
        cmd_thinking(adapter, "off")
        assert adapter.config.show_thinking is False
        cmd_thinking(adapter, "on")
        assert adapter.config.show_thinking is True

    def test_bare_form_still_toggles(self):
        from wisp.repl.commands.agents import cmd_thinking

        adapter = _make_adapter()
        adapter.config = adapter.config.replace(show_thinking=True)
        cmd_thinking(adapter, "")
        assert adapter.config.show_thinking is False

    def test_a_bogus_argument_changes_nothing(self):
        from wisp.repl.commands.agents import cmd_thinking

        adapter = _make_adapter()
        adapter.config = adapter.config.replace(show_thinking=True)
        cmd_thinking(adapter, "purple")
        assert adapter.config.show_thinking is True


class TestEventsSurfacesTheEngineDiagnostics:
    """The interceptor hides retries/SSE/tool-404s by design; `/events` is the opt-in that makes
    them visible, and `/events log` reads the file it always writes."""

    def test_toggles_the_console_echo(self):
        import agent.logger as engine_log
        from wisp.repl.commands.agents import cmd_events

        adapter = _make_adapter()
        was = engine_log.console_echo_enabled()
        try:
            cmd_events(adapter, "on")
            assert engine_log.console_echo_enabled() is True
            cmd_events(adapter, "off")
            assert engine_log.console_echo_enabled() is False
        finally:
            engine_log.set_console_echo(was)

    def test_log_reads_the_interceptors_file(self, tmp_path, monkeypatch):
        import agent.logger as engine_log
        from wisp.repl.commands.agents import cmd_events

        log = tmp_path / "runtime.log"
        log.write_text("alpha\nbravo\ncharlie\n")
        monkeypatch.setattr(engine_log, "LOG_PATH", log)
        monkeypatch.setattr(engine_log, "get_log_path", lambda: log)

        adapter = _make_adapter()
        cmd_events(adapter, "log 2")          # prints; the assertion is that it did not raise
        cmd_events(adapter, "log")            # default count, and a missing-file path is separate

    def test_log_on_a_missing_file_is_not_an_error(self, tmp_path, monkeypatch):
        import agent.logger as engine_log
        from wisp.repl.commands.agents import cmd_events

        missing = tmp_path / "nope.log"
        monkeypatch.setattr(engine_log, "get_log_path", lambda: missing)
        cmd_events(_make_adapter(), "log 5")  # must not raise

    def test_a_bogus_verb_is_rejected(self):
        import agent.logger as engine_log
        from wisp.repl.commands.agents import cmd_events

        was = engine_log.console_echo_enabled()
        try:
            cmd_events(_make_adapter(), "sideways")
            assert engine_log.console_echo_enabled() is was
        finally:
            engine_log.set_console_echo(was)
