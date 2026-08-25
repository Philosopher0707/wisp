"""Tests for Wisp slash commands."""

import pytest
from wisp.commands import (
    register,
    lookup,
    dispatch,
    all_commands,
    _REGISTRY,
)
from wisp.exceptions import ExitREPL


class MockConfig:
    def __init__(self):
        self.model = "test-model"
        self.workspace = "/tmp/wisp-test"
        self.auto_approve = False
        self.show_thinking = False
        self.max_context_tokens = 128000
        self.chars_per_token = 4
        self._context_tokens_explicit = False

    def replace(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self


class MockClient:
    def __init__(self):
        self.model = "test-model"

    def list_models(self):
        return [
            {"name": "test-model"},
            {"name": "qwen2.5-coder"},
            {"name": "deepseek-v4-flash"},
        ]


def _mock_session() -> dict:
    # AgentAdapter carries the session as a plain dict (see cli.py) —
    # mirror the shape runtime.get_or_create_session() returns.
    return {
        "id": "test-session-123",
        "title": "Test Session",
        "messages": [],
    }


class MockAgent:
    def __init__(self):
        self.session = _mock_session()
        self.messages = self.session["messages"]
        self.config = MockConfig()
        self.client = MockClient()
        self._active_skill = None
        self._system_prompt_cache = {}

    def _build_system_prompt(self, *args, **kwargs):
        return "You are Wisp."

    def _estimate_tokens(self, msgs):
        return sum(len(m.get("content", "")) for m in msgs) // 4

    def _save_session(self):
        pass


@pytest.fixture(autouse=True)
def clean_registry():
    """Snapshot and restore the command registry so tests don't pollute each other."""
    original = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)


@pytest.fixture
def agent():
    return MockAgent()


# ── Registry tests ───────────────────────────────────────────────────


def test_register_and_lookup():
    @register("testcmd", "A test command", aliases=("tc",))
    def cmd_test(agent, args):
        agent.messages.append({"cmd": args})

    assert lookup("testcmd") is not None
    assert lookup("tc") is not None
    assert lookup("testcmd") is lookup("tc")
    assert lookup("nonexistent") is None


def test_all_commands_unique():
    @register("alpha", "First")
    def cmd_alpha(a, args): pass

    @register("beta", "Second", aliases=("b",))
    def cmd_beta(a, args): pass

    cmds = all_commands()
    names = [c.name for c in cmds]
    # Built-in commands are already registered; just verify our new ones exist
    assert "alpha" in names
    assert "beta" in names
    assert len(names) == len(set(names))  # no duplicates


# ── Dispatch tests ───────────────────────────────────────────────────


def test_dispatch_not_a_command(agent):
    """Non-slash input should return False (pass through to LLM)."""
    assert dispatch("hello world", agent) is False
    assert dispatch("  hello  ", agent) is False


def test_dispatch_unknown_command(agent, capsys):
    """Unknown /command should print error and return True (consumed)."""
    assert dispatch("/notreal", agent) is True
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out


def test_dispatch_empty_slash(agent, capsys):
    """Bare '/' should show help and be consumed."""
    assert dispatch("/", agent) is True
    captured = capsys.readouterr()
    assert "Available commands" in captured.out


# ── Built-in command tests (after they are imported) ─────────────────

# Import the built-in commands so they register
import wisp.commands as commands_module


def test_cmd_clear(agent):
    agent.messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    commands_module.cmd_clear(agent, "")
    assert len(agent.messages) == 0


def test_cmd_model_show(agent, capsys):
    commands_module.cmd_model(agent, "")
    captured = capsys.readouterr()
    assert "test-model" in captured.out
    assert "Available models" in captured.out
    assert "qwen2.5-coder" in captured.out
    assert "(cloud)" in captured.out


def test_cmd_model_switch(agent):
    commands_module.cmd_model(agent, "qwen2.5-coder")
    assert agent.config.model == "qwen2.5-coder"
    assert agent.client.model == "qwen2.5-coder"


def test_cmd_model_switch_by_number(agent):
    commands_module.cmd_model(agent, "2")
    assert agent.config.model == "qwen2.5-coder"


def test_cmd_model_switch_by_prefix(agent, capsys):
    commands_module.cmd_model(agent, "deep")
    captured = capsys.readouterr()
    assert "resolved to deepseek-v4-flash" in captured.out
    assert agent.config.model == "deepseek-v4-flash"


def test_cmd_model_switch_by_display_name(agent):
    """Switching by name without :cloud suffix should resolve."""
    commands_module.cmd_model(agent, "qwen2.5-coder")
    assert agent.config.model == "qwen2.5-coder"


def test_cmd_model_invalid_number(agent, capsys):
    commands_module.cmd_model(agent, "99")
    captured = capsys.readouterr()
    assert "Invalid model number" in captured.out


def test_cmd_model_ambiguous_prefix(agent, capsys):
    """Prefix that matches multiple models should warn."""
    # Patch list_models to return ambiguous set
    original = agent.client.list_models
    agent.client.list_models = lambda: [
        {"name": "test-model"},
        {"name": "test-v2"},
    ]
    commands_module.cmd_model(agent, "test")
    captured = capsys.readouterr()
    assert "Ambiguous" in captured.out
    agent.client.list_models = original


def test_cmd_approve_toggle(agent):
    assert agent.config.auto_approve is False
    commands_module.cmd_approve(agent, "")
    assert agent.config.auto_approve is True
    commands_module.cmd_approve(agent, "")
    assert agent.config.auto_approve is False


def test_cmd_thinking_toggle(agent):
    assert agent.config.show_thinking is False
    commands_module.cmd_thinking(agent, "")
    assert agent.config.show_thinking is True


def test_cmd_session(agent, capsys):
    commands_module.cmd_session(agent, "")
    captured = capsys.readouterr()
    assert "test-session-123" in captured.out
    assert "test-model" in captured.out
    assert "Active skill:" in captured.out
    assert "(none)" in captured.out


def test_cmd_save(agent, capsys):
    commands_module.cmd_save(agent, "")
    captured = capsys.readouterr()
    assert "saved" in captured.out


def test_cmd_tokens(agent, capsys):
    commands_module.cmd_tokens(agent, "")
    captured = capsys.readouterr()
    assert "Context:" in captured.out
    assert "128,000" in captured.out


def test_cmd_drop(agent):
    agent.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    commands_module.cmd_drop(agent, "")
    assert len(agent.messages) == 1
    assert agent.messages[0]["role"] == "user"


def test_cmd_drop_empty(agent, capsys):
    commands_module.cmd_drop(agent, "")
    captured = capsys.readouterr()
    assert "empty" in captured.out


def test_cmd_workspace_show(agent, capsys):
    commands_module.cmd_workspace(agent, "")
    captured = capsys.readouterr()
    assert "/tmp/wisp-test" in captured.out


def test_cmd_workspace_change(agent, tmp_path):
    new_dir = str(tmp_path)
    commands_module.cmd_workspace(agent, new_dir)
    assert agent.config.workspace == new_dir


def test_cmd_workspace_nonexistent(agent, capsys):
    commands_module.cmd_workspace(agent, "/does/not/exist")
    captured = capsys.readouterr()
    assert "does not exist" in captured.out


def test_cmd_exit_raises(agent):
    with pytest.raises(ExitREPL):
        commands_module.cmd_exit(agent, "")


def test_dispatch_exit(agent):
    with pytest.raises(ExitREPL):
        dispatch("/exit", agent)


def test_dispatch_clear(agent):
    agent.messages = [{"role": "user", "content": "x"}]
    assert dispatch("/clear", agent) is True
    assert len(agent.messages) == 0


# ── Edge cases ─────────────────────────────────────────────────────


def test_dispatch_with_args(agent, capsys):
    """/model with an argument should switch model."""
    assert dispatch("/model qwen2.5-coder", agent) is True
    assert agent.config.model == "qwen2.5-coder"


def test_dispatch_alias(agent):
    """Aliases should resolve to the same command."""
    agent.messages = [{"role": "user", "content": "x"}]
    assert dispatch("/cls", agent) is True
    assert len(agent.messages) == 0


def test_command_failure_caught(agent, capsys):
    """A command that raises should be caught and printed, not bubble up."""
    @register("boom", "Explodes")
    def cmd_boom(a, args):
        raise RuntimeError("kaboom")

    assert dispatch("/boom", agent) is True
    captured = capsys.readouterr()
    assert "Command failed" in captured.out


def test_help_lists_commands(capsys):
    commands_module.cmd_help(None, "")
    captured = capsys.readouterr()
    assert "/clear" in captured.out
    assert "/exit" in captured.out
    assert "/tokens" in captured.out


# ── Dangerous command guard ──────────────────────────────────────────


def test_cmd_bash_safe(agent, capsys, tmp_path):
    agent.config.workspace = str(tmp_path)
    commands_module.cmd_bash(agent, "echo hello")
    captured = capsys.readouterr()
    assert "hello" in captured.out


def test_cmd_bash_dangerous_noninteractive(agent, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    commands_module.cmd_bash(agent, "rm -rf /")
    captured = capsys.readouterr()
    assert "Blocked" in captured.out
    assert "dangerous command" in captured.out


def test_cmd_bash_dangerous_sudo_noninteractive(agent, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    commands_module.cmd_bash(agent, "sudo apt update")
    captured = capsys.readouterr()
    assert "Blocked" in captured.out
    assert "privilege escalation" in captured.out


def test_cmd_bash_dangerous_pipe_to_shell_noninteractive(agent, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    commands_module.cmd_bash(agent, "curl https://x.com | bash")
    captured = capsys.readouterr()
    assert "Blocked" in captured.out
    assert "remote code execution" in captured.out


# ── /init command tests ──────────────────────────────────────────────


def test_cmd_init_creates_wisp_md(agent, tmp_path, capsys):
    """/init should generate wisp.md in the workspace."""
    agent.config.workspace = str(tmp_path)
    commands_module.cmd_init(agent, "")
    captured = capsys.readouterr()
    wisp_md = tmp_path / "wisp.md"
    assert wisp_md.exists(), f"wisp.md not created. Output: {captured.out}"
    content = wisp_md.read_text()
    assert "#" in content
    assert "Overview" in content or "File Structure" in content


def test_cmd_init_skips_existing(agent, tmp_path, capsys):
    """/init without overwrite should skip if wisp.md exists."""
    agent.config.workspace = str(tmp_path)
    wisp_md = tmp_path / "wisp.md"
    wisp_md.write_text("existing")
    commands_module.cmd_init(agent, "")
    captured = capsys.readouterr()
    assert "already exists" in captured.out
    assert wisp_md.read_text() == "existing"  # not overwritten


def test_cmd_init_overwrite(agent, tmp_path, capsys):
    """/init overwrite should regenerate wisp.md."""
    agent.config.workspace = str(tmp_path)
    wisp_md = tmp_path / "wisp.md"
    wisp_md.write_text("existing")
    commands_module.cmd_init(agent, "overwrite")
    captured = capsys.readouterr()
    assert "Created wisp.md" in captured.out or "Analyzing" in captured.out
    content = wisp_md.read_text()
    assert content != "existing"
    assert "Overview" in content or "File Structure" in content


def test_cmd_init_content_structure(agent, tmp_path, capsys):
    """Generated wisp.md should have expected sections."""
    # Create a minimal project structure
    (tmp_path / "main.py").write_text("def main(): pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test_main(): pass\n")

    agent.config.workspace = str(tmp_path)
    commands_module.cmd_init(agent, "overwrite")

    wisp_md = tmp_path / "wisp.md"
    content = wisp_md.read_text()

    assert "#" in content
    assert "## Overview" in content
    assert "## File Structure" in content
    assert "## Key Files" in content
    assert "main.py" in content
    assert "## Testing" in content or "tests/" in content
    assert "## Conventions" in content
    assert "Wisp Agent Notes" in content


class TestRegistryIntegrity:
    """C3: aliases are unique and registration failures are loud."""

    def test_no_alias_collisions_in_registry(self):
        from wisp.commands import _REGISTRY

        by_key = {}
        for key, cmd in _REGISTRY.items():
            owner = by_key.setdefault(key, cmd.name)
            assert owner == cmd.name, f"/{key} claimed twice: {owner} vs {cmd.name}"

    def test_c_belongs_to_continue(self):
        from wisp.commands import _REGISTRY

        assert _REGISTRY["c"].name == "continue"
        assert _REGISTRY["compact"].name == "compact"

    def test_registering_stolen_alias_raises(self):
        import pytest as _pytest

        from wisp.commands import register

        with _pytest.raises(ValueError, match="already owned"):

            @register("impostor", "tries to steal an alias", aliases=("exit",))
            def _cmd_impostor(agent, args):
                return True


# ═══════════════════════════════════════════════════════════════════
# /model /sessions /tokens — session control surface (design R5)
# ═══════════════════════════════════════════════════════════════════


class TestSessionsCommand:
    def test_lists_saved_sessions_short_ids(self, capsys):
        from wisp.commands import cmd_sessions

        class _Store:
            def list_sessions(self, limit=50):
                return [
                    {"id": "17052f74-2824-465a-a81f-1e9d6921f240",
                     "model": "m1", "title": "cache research",
                     "msg_count": 6, "updated_at": "2026-08-24T10:00:00"},
                    {"id": "abcdef12-session-two",
                     "model": "m2", "title": "",
                     "msg_count": 0, "updated_at": "2026-08-23T09:00:00"},
                ]

        a = type("A", (), {"runtime": type("R", (), {"store": _Store()})()})()
        cmd_sessions(a, "")
        out = capsys.readouterr().out
        assert "17052f74" in out and "abcdef12" in out
        assert "17052f74-2824" not in out, "full uuid is noise"
        assert "cache research" in out

    def test_no_store_prints_hint(self, capsys):
        from wisp.commands import cmd_sessions

        a = type("A", (), {"runtime": type("R", (), {"store": None})()})()
        cmd_sessions(a, "")
        out = capsys.readouterr().out
        assert "No session store" in out


# ── /agents command ──────────────────────────────────────────────────

class TestAgentsCommand:
    """`/agents` surfaces the background-agent registry."""

    def _agent_with_manager(self, mgr):
        from types import SimpleNamespace
        agent = MockAgent()
        agent.runtime = SimpleNamespace(
            orchestrator=SimpleNamespace(background_agents=mgr)
        )
        return agent

    def test_no_manager_warns(self, agent, capsys):
        dispatch("/agents", agent)
        out = capsys.readouterr().out
        assert "not available" in out.lower()

    def test_list_empty(self, capsys):
        from wisp.multi_agent.background import BackgroundAgentManager
        from tests.test_background_agents import FakeOrchestrator
        agent = self._agent_with_manager(BackgroundAgentManager(FakeOrchestrator()))
        dispatch("/agents", agent)
        out = capsys.readouterr().out
        assert "No background agents" in out

    def test_detail_unknown_id(self, capsys):
        from wisp.multi_agent.background import BackgroundAgentManager
        from tests.test_background_agents import FakeOrchestrator
        agent = self._agent_with_manager(BackgroundAgentManager(FakeOrchestrator()))
        dispatch("/agents bg-nope", agent)
        out = capsys.readouterr().out
        assert "No such agent" in out

    @pytest.mark.asyncio
    async def test_detail_known_running_agent(self, capsys):
        from wisp.multi_agent.background import BackgroundAgentManager
        from tests.test_background_agents import FakeOrchestrator
        from wisp.multi_agent.task import SubagentContract

        mgr = BackgroundAgentManager(FakeOrchestrator(delay=5.0))
        launch = await mgr.launch(SubagentContract(name="bg-x", task="t"))
        agent = self._agent_with_manager(mgr)

        import threading
        result: dict = {}

        def run():
            try:
                dispatch(f"/agents {launch['agent_id']}", agent)
            except Exception as e:
                result["error"] = str(e)

        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(timeout=10)
        mgr.cancel(launch["agent_id"])
        out = capsys.readouterr().out if not result.get("error") else result["error"]
        assert launch["agent_id"] in out

    def test_cancel_via_command(self, capsys):
        from wisp.multi_agent.background import BackgroundAgentEntry, BackgroundAgentManager
        from tests.test_background_agents import FakeOrchestrator
        from wisp.multi_agent.task import SubagentContract

        mgr = BackgroundAgentManager(FakeOrchestrator())
        # Finished entry → cancel refuses (nothing to cancel).
        entry = BackgroundAgentEntry(id="bg-cancel-me", label="x", contract=SubagentContract(task="t"))
        entry.status = "completed"
        mgr._entries[entry.id] = entry
        agent = self._agent_with_manager(mgr)
        dispatch("/agents cancel bg-cancel-me", agent)
        out = capsys.readouterr().out
        assert "already" in out or "cancel failed" in out

    def test_send_reports_error_for_missing_session(self, capsys):
        from wisp.multi_agent.background import BackgroundAgentEntry, BackgroundAgentManager
        from tests.test_background_agents import FakeOrchestrator
        from wisp.multi_agent.task import SubagentContract

        orch = FakeOrchestrator()
        mgr = BackgroundAgentManager(orch)
        entry = BackgroundAgentEntry(id="bg-s1", label="s", contract=SubagentContract(task="t"))
        entry.status = "completed"
        mgr._entries[entry.id] = entry  # no last_session_id
        agent = self._agent_with_manager(mgr)
        dispatch("/agents send bg-s1 continue please", agent)
        out = capsys.readouterr().out
        assert "session" in out.lower()

    def test_registry_has_aliases(self):
        from wisp.commands import lookup
        assert lookup("agents") is not None
        assert lookup("ba") is not None

class TestSkillCaptureCommands:
    @pytest.fixture(autouse=True)
    def _fresh_capture(self):
        from wisp.skill_capture import reset_capture
        reset_capture()
        yield
        reset_capture()

    def test_suggest_empty_history(self, agent, capsys):
        from wisp.commands import dispatch as execute_command
        execute_command("/skill suggest", agent)
        out = capsys.readouterr().out
        assert "No repeated workflow" in out

    def test_suggest_shows_repeated_workflow(self, agent, capsys, tmp_path):
        from wisp.skill_capture import get_capture
        from wisp.commands import dispatch as execute_command
        cap = get_capture()
        for _ in range(2):
            cap.record("list_files")
            cap.record("run_tests")
        agent.config = MockConfig()
        agent.config.workspace = str(tmp_path)
        execute_command("/skill suggest", agent)
        out = capsys.readouterr().out
        assert "Repeated workflow" in out
        assert "run_tests" in out

    def test_save_writes_skill_file(self, agent, capsys, tmp_path):
        from wisp.skill_capture import get_capture
        from wisp.commands import dispatch as execute_command
        cap = get_capture()
        cap.record("read_file", {"path": "a.py"})
        agent.config = MockConfig()
        agent.config.workspace = str(tmp_path)
        execute_command("/skill save my-flow Does the flow", agent)
        out = capsys.readouterr().out
        assert "Skill saved" in out
        skill_file = tmp_path / ".agents" / "skills" / "my-flow" / "SKILL.md"
        assert skill_file.exists()
        assert "Does the flow" in skill_file.read_text(encoding="utf-8")

    def test_save_without_history_errors(self, agent, capsys, tmp_path):
        from wisp.commands import dispatch as execute_command
        agent.config = MockConfig()
        agent.config.workspace = str(tmp_path)
        execute_command("/skill save nothing", agent)
        assert "no recorded steps" in capsys.readouterr().out.lower()

    def test_save_twice_reports_merged(self, agent, capsys, tmp_path):
        from wisp.skill_capture import get_capture
        from wisp.commands import dispatch as execute_command
        cap = get_capture()
        cap.record("list_files")
        agent.config = MockConfig()
        agent.config.workspace = str(tmp_path)
        execute_command("/skill save flow d", agent)
        execute_command("/skill save flow d", agent)
        out = capsys.readouterr().out
        assert out.count("Skill saved") == 1
        assert "Skill merged" in out

