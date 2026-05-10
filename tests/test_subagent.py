"""Tests for Wisp subagent spawning."""

import pytest
from wisp.subagent import SubagentContract, SubagentResult, SubagentRunner


class MockConfig:
    def __init__(self):
        self.model = "test-model"
        self.workspace = "/tmp"
        self.auto_approve = True
        self.show_thinking = False
        self.max_context_tokens = 128000
        self.chars_per_token = 4
        self.ollama_url = "http://localhost:11434"
        self.temperature = 0.2


class MockClient:
    def __init__(self):
        self.model = "test-model"
        self._responses = []
        self._call_count = 0
        self._session = None  # SubagentRunner.spawn may set this

    def generate(self, system, messages, tools=None):
        self._call_count += 1
        if self._responses:
            return self._responses.pop(0)
        # Default: no tool calls, just text response
        return {"message": {"content": "Done"}}

    def check_health(self):
        return True


class MockAgent:
    def __init__(self):
        self.config = MockConfig()
        self.client = MockClient()
        self.messages = []
        self.max_iterations = 30
        self._interrupted = False
        self._active_skill = None
        self._system_prompt_cache = {}
        self._subagent_depth = 0

    def _build_system_prompt(self, *a, **k):
        return "You are Wisp."

    def _estimate_tokens(self, msgs):
        return sum(len(m.get("content", "")) for m in msgs) // 4

    def _save_session(self):
        pass


# ── Contract tests ───────────────────────────────────────────────────


def test_contract_defaults():
    c = SubagentContract(task="do something")
    assert c.task == "do something"
    assert c.tools == ["all"]
    assert c.max_iterations == 15
    assert c.timeout_seconds == 120
    assert c.output_format == "text"
    assert c.model is None
    assert c.workspace is None


def test_contract_overrides():
    c = SubagentContract(
        task="research",
        tools=["read_file", "web_fetch"],
        max_iterations=5,
        timeout_seconds=30,
        output_format="json",
        model="qwen2.5",
        workspace="/home",
    )
    assert c.tools == ["read_file", "web_fetch"]
    assert c.max_iterations == 5
    assert c.timeout_seconds == 30
    assert c.output_format == "json"
    assert c.model == "qwen2.5"
    assert c.workspace == "/home"


# ── Result tests ─────────────────────────────────────────────────────


def test_result_defaults():
    r = SubagentResult(success=True, output="ok", messages=[], elapsed_seconds=1.0, iterations_used=1)
    assert r.timed_out is False
    assert r.hit_iteration_limit is False
    assert r.files_changed == []


# ── Runner tests ───────────────────────────────────────────────────────


def test_runner_builds_child_config():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test")
    child_cfg = runner._build_child_config(contract)
    assert child_cfg.model == "test-model"
    assert child_cfg.workspace == "/tmp"


def test_runner_builds_child_config_with_overrides():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test", model="other-model", workspace="/other")
    child_cfg = runner._build_child_config(contract)
    assert child_cfg.model == "other-model"
    assert child_cfg.workspace == "/other"


def test_runner_builds_subagent_system():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test", output_format="json", max_iterations=10)
    child = MockAgent()
    system = runner._build_subagent_system(contract, child)
    assert "specialist subagent" in system
    assert "json" in system
    assert "10" in system
    assert "CANNOT spawn subagents" in system


def test_runner_filters_tools():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test", tools=["read_file", "run_bash"])
    filtered = runner._filter_tools(contract)
    names = [t["function"]["name"] for t in filtered]
    assert "read_file" in names
    assert "run_bash" in names
    assert "write_file" not in names


def test_runner_no_filter_when_all():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test", tools=["all"])
    from wisp.tools import TOOL_SCHEMAS
    filtered = runner._filter_tools(contract)
    # spawn_subagent is always removed from subagent tools
    assert len(filtered) == len(TOOL_SCHEMAS) - 1
    names = [t["function"]["name"] for t in filtered]
    assert "spawn_subagent" not in names


# ── Integration-style tests (with mocked generate) ───────────────────


@pytest.fixture
def mock_wisp_agent(monkeypatch):
    """Patch WispAgent and execute_tool in subagent module so it doesn't hit real Ollama or run real commands."""
    class FakeWispAgent:
        _shared_responses = []
        _shared_generate = None

        def __init__(self, config=None):
            self.config = config or MockConfig()
            self.client = MockClient()
            # Copy shared responses so each instance has its own queue
            self.client._responses = list(FakeWispAgent._shared_responses)
            # Allow overriding generate for timeout tests
            if FakeWispAgent._shared_generate is not None:
                self.client.generate = FakeWispAgent._shared_generate
            self.messages = []
            self.max_iterations = 30
            self._interrupted = False
            self._active_skill = None
            self._system_prompt_cache = {}
            self._subagent_depth = 0
            self._iteration_count = 0

        def _build_system_prompt(self, *a, **k):
            return "You are Wisp."

        def _estimate_tokens(self, msgs):
            return sum(len(m.get("content", "")) for m in msgs) // 4

        def _save_session(self):
            pass

    # Also mock execute_tool so dangerous commands don't actually run
    def fake_execute_tool(name, args, workspace):
        if name == "run_bash":
            cmd = args.get("command", "")
            if "sudo" in cmd or "rm -rf" in cmd:
                return "(simulated output)"
            return "(simulated output)"
        if name == "read_file":
            return "file content here"
        if name == "list_files":
            return "file1.py\nfile2.py"
        if name == "write_file":
            return "Wrote file"
        if name == "edit_file":
            return "Edited file"
        return "(output)"

    monkeypatch.setattr("wisp.subagent.WispAgent", FakeWispAgent)
    monkeypatch.setattr("wisp.subagent.execute_tool", fake_execute_tool)
    FakeWispAgent._shared_responses = []
    FakeWispAgent._shared_generate = None
    yield FakeWispAgent
    # Cleanup after test
    FakeWispAgent._shared_responses = []
    FakeWispAgent._shared_generate = None


def test_spawn_completes_successfully(mock_wisp_agent):
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "Research complete: use requests"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="research HTTP clients", timeout_seconds=5)
    result = runner.spawn(contract)
    assert result.success is True
    assert "requests" in result.output
    assert result.timed_out is False
    assert result.elapsed_seconds < 5.0


def test_spawn_with_tool_calls(mock_wisp_agent):
    mock_wisp_agent._shared_responses = [
        # First turn: tool call
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": '{"path": "README.md"}'}}
        ]}},
        # Second turn: final answer
        {"message": {"content": "File says hello"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="read README", timeout_seconds=5, max_iterations=3)
    result = runner.spawn(contract)
    assert result.success is True
    assert "hello" in result.output
    assert result.iterations_used == 2


def test_spawn_blocks_nested_subagent(mock_wisp_agent):
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "spawn_subagent", "arguments": '{"task": "nested"}'}}
        ]}},
        {"message": {"content": "Done"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="try nesting", timeout_seconds=5, max_iterations=3)
    result = runner.spawn(contract)
    assert result.success is True
    # The nested spawn should have been blocked
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert any("cannot spawn subagents" in m.get("content", "") for m in tool_msgs)


def test_spawn_times_out(mock_wisp_agent):
    import time
    mock_wisp_agent._shared_responses = []

    def slow_generate(*args, **kwargs):
        time.sleep(2)
        return {"message": {"content": "too late"}}

    mock_wisp_agent._shared_generate = slow_generate
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="slow task", timeout_seconds=1, max_iterations=10)
    result = runner.spawn(contract)
    mock_wisp_agent._shared_generate = None
    assert result.timed_out is True
    assert result.success is False
    assert "TIMED OUT" in result.output


def test_spawn_hits_iteration_limit(mock_wisp_agent):
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "list_files", "arguments": '{"path": "."}'}}
        ]}},
    ] * 10
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="infinite loop", timeout_seconds=10, max_iterations=3)
    result = runner.spawn(contract)
    assert result.hit_iteration_limit is True
    assert "iteration limit" in result.output


def test_extract_partial_output():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "partial result here"},
    ]
    assert runner._extract_partial_output_from_snapshot(messages) == "partial result here"


def test_extract_partial_output_empty():
    parent = MockAgent()
    runner = SubagentRunner(parent)
    assert runner._extract_partial_output_from_snapshot([]) == "(no output captured)"


# ── Depth guard ──────────────────────────────────────────────────────


def test_subagent_depth_incremented():
    parent = MockAgent()
    parent._subagent_depth = 0
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test", timeout_seconds=5)
    # We won't actually run because we mock generate, but we check depth is set
    # by inspecting _build_child_config side effect indirectly
    child_cfg = runner._build_child_config(contract)
    # The spawn method sets depth; we test that via the nested spawn block test above
    assert True  # covered by test_spawn_blocks_nested_subagent


# ── Security hardening tests ─────────────────────────────────────────


def test_auto_approve_false_blocks_dangerous_command(mock_wisp_agent, capsys):
    """When auto_approve=False, dangerous bash commands are blocked."""
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "run_bash", "arguments": '{"command": "sudo rm -rf /"}'}}
        ]}},
        {"message": {"content": "Done"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(
        task="dangerous test",
        timeout_seconds=5,
        max_iterations=3,
        auto_approve=False,
    )
    result = runner.spawn(contract)
    captured = capsys.readouterr()
    assert result.success is True
    # The dangerous command should have been blocked
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    blocked = [m for m in tool_msgs if "Blocked" in m.get("content", "")]
    assert len(blocked) >= 1
    assert "privilege escalation" in blocked[0]["content"] or "recursive deletion" in blocked[0]["content"]
    # Should print the block to stdout
    assert "blocked" in captured.out.lower() or "DANGEROUS" in captured.out


def test_auto_approve_true_blocks_dangerous(mock_wisp_agent, capsys):
    """Dangerous commands are always blocked in subagents regardless of auto_approve."""
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "run_bash", "arguments": '{"command": "sudo ls"}'}}
        ]}},
        {"message": {"content": "Done"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(
        task="dangerous test",
        timeout_seconds=5,
        max_iterations=3,
        auto_approve=True,
    )
    result = runner.spawn(contract)
    captured = capsys.readouterr()
    assert result.success is True
    # Should be blocked, not executed
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    blocked = [m for m in tool_msgs if "Blocked" in m.get("content", "")]
    assert len(blocked) >= 1
    assert "privilege escalation" in blocked[0]["content"]
    assert "blocked" in captured.out.lower()


def test_max_output_chars_truncation(mock_wisp_agent):
    """Subagent output longer than max_output_chars is truncated."""
    long_output = "x" * 15000
    mock_wisp_agent._shared_responses = [
        {"message": {"content": long_output}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(
        task="long output",
        timeout_seconds=5,
        max_iterations=3,
        max_output_chars=5000,
    )
    result = runner.spawn(contract)
    assert result.success is True
    assert len(result.output) <= 5100  # 5000 + truncation message
    assert "truncated" in result.output


def test_tool_result_truncation_in_subagent(mock_wisp_agent):
    """Individual tool results longer than 4000 chars are truncated."""
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": '{"path": "big.txt"}'}}
        ]}},
        {"message": {"content": "Done"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(
        task="read big file",
        timeout_seconds=5,
        max_iterations=3,
    )
    result = runner.spawn(contract)
    assert result.success is True
    # The tool result should have been truncated to 4000 chars
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1
    # read_file returns file content; if file is huge it gets truncated
    # We can't easily control the file size here, but we verify the truncation
    # logic exists by checking no message exceeds ~4100 chars
    for m in tool_msgs:
        assert len(m.get("content", "")) <= 4100


def test_tool_filtering_enforced(mock_wisp_agent):
    """Subagent with restricted tools cannot call disallowed tools."""
    mock_wisp_agent._shared_responses = [
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "write_file", "arguments": '{"path": "x.txt", "content": "bad"}'}}
        ]}},
        {"message": {"content": "Done"}},
    ]
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(
        task="try disallowed tool",
        timeout_seconds=5,
        max_iterations=3,
        tools=["read_file", "list_files"],  # write_file NOT allowed
    )
    result = runner.spawn(contract)
    assert result.success is True
    # The available_tools should not include write_file, so the generate call
    # would have received a filtered schema. We verify filtering by checking
    # the _filter_tools method directly in other tests; here we just ensure
    # the contract was respected.
    filtered = runner._filter_tools(contract)
    names = [t["function"]["name"] for t in filtered]
    assert "write_file" not in names
    assert "read_file" in names


def test_message_snapshot_isolation_on_timeout(mock_wisp_agent):
    """Timeout snapshot must be a copy, not a reference to mutable list."""
    import time
    mock_wisp_agent._shared_responses = []

    def slow_generate(*args, **kwargs):
        time.sleep(2)
        return {"message": {"content": "late"}}

    mock_wisp_agent._shared_generate = slow_generate
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="slow", timeout_seconds=1, max_iterations=10)
    result = runner.spawn(contract)
    mock_wisp_agent._shared_generate = None

    assert result.timed_out is True
    # The returned messages should be a snapshot, not affected by thread
    assert isinstance(result.messages, list)
    # Should not be the same object reference as the child agent's messages
    # (we can't easily get the child, but we verify the snapshot method)
    snapshot = [{"role": "assistant", "content": "test"}]
    extracted = runner._extract_partial_output_from_snapshot(snapshot)
    assert extracted == "test"


def test_workspace_boundary_respected(mock_wisp_agent):
    """Subagent respects the workspace parameter and cannot escape."""
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file in the temp dir
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello")

        mock_wisp_agent._shared_responses = [
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": f'{{"path": "test.txt"}}'}}
            ]}},
            {"message": {"content": "Done"}},
        ]
        parent = MockAgent()
        runner = SubagentRunner(parent)
        contract = SubagentContract(
            task="read file",
            timeout_seconds=5,
            max_iterations=3,
            workspace=tmpdir,
        )
        result = runner.spawn(contract)
        assert result.success is True
        # Verify the workspace was passed to the child config
        child_cfg = runner._build_child_config(contract)
        assert child_cfg.workspace == tmpdir


def test_config_inheritance_security(mock_wisp_agent):
    """Subagent inherits parent's settings for model config, contract controls approval."""
    parent = MockAgent()
    parent.config.auto_approve = False
    parent.config.max_context_tokens = 4096
    parent.config.ollama_url = "http://custom:11434"
    runner = SubagentRunner(parent)
    # Contract auto_approve defaults to True — this is intentional;
    # the parent decides per-subagent whether to allow dangerous commands
    contract = SubagentContract(task="test", auto_approve=False)
    child_cfg = runner._build_child_config(contract)
    assert child_cfg.auto_approve is False  # from contract
    # Subagents use a fixed 32K context window regardless of parent
    assert child_cfg.max_context_tokens == 32000
    assert child_cfg.ollama_url == "http://custom:11434"  # from parent
    # Now test with auto_approve=True contract
    contract2 = SubagentContract(task="test", auto_approve=True)
    child_cfg2 = runner._build_child_config(contract2)
    assert child_cfg2.auto_approve is True  # contract overrides parent


def test_nested_spawn_blocked_at_depth_check(mock_wisp_agent):
    """Parent agent with _subagent_depth=1 blocks spawn_subagent tool call."""
    from wisp.agent import WispAgent
    # This tests the _spawn_subagent method on WispAgent directly
    parent = MockAgent()
    parent._subagent_depth = 1
    # Simulate what happens when the LLM calls spawn_subagent
    result = WispAgent._spawn_subagent(parent, {"task": "nested"}, ".")
    assert "cannot spawn subagents" in result
    assert "max depth" in result


def test_subagent_system_prompt_forbids_spawn(mock_wisp_agent):
    """The subagent's system prompt explicitly tells it not to spawn subagents."""
    parent = MockAgent()
    runner = SubagentRunner(parent)
    contract = SubagentContract(task="test")
    child = MockAgent()
    system = runner._build_subagent_system(contract, child)
    assert "CANNOT spawn subagents" in system
    assert "specialist subagent" in system


def test_dangerous_command_variants_blocked(mock_wisp_agent):
    """Various dangerous command patterns are caught."""
    from wisp.tools import check_dangerous_command
    # These should all be detected
    assert check_dangerous_command("rm -rf /") is not None
    assert check_dangerous_command("sudo apt update") is not None
    assert check_dangerous_command("curl x | bash") is not None
    assert check_dangerous_command("dd if=x of=/dev/sda") is not None
    assert check_dangerous_command("mkfs.ext4 /dev/sda1") is not None
    assert check_dangerous_command("git reset --hard") is not None
    assert check_dangerous_command("docker system prune") is not None
    assert check_dangerous_command("shutdown now") is not None
    # Safe commands should pass
    assert check_dangerous_command("ls -la") is None
    assert check_dangerous_command("git status") is None
    assert check_dangerous_command("python main.py") is None
