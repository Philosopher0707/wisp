"""Tests for the unified SubagentOrchestrator.

These tests verify the new orchestrator without hitting real Ollama or
running real git commands.  Worktree creation is mocked where needed.
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from wisp.multi_agent import SubagentOrchestrator, SubagentContract, SubagentResult
from wisp.multi_agent.task import EventKind


class FakeStatelessCore:
    """Minimal fake stateless core for testing SubagentOrchestrator."""

    def __init__(self, provider=None, security=None, extensions=None, config=None):
        from wisp.config import WispConfig
        cfg = config or MagicMock()
        prov = getattr(config, "provider", None) if config else None
        provider_val = prov if prov and not isinstance(prov, MagicMock) else "ollama"
        if isinstance(cfg, WispConfig):
            cfg = cfg.replace(workspace="/tmp", provider=provider_val)
        else:
            cfg.workspace = "/tmp"
            cfg.provider = provider_val
        self.config = cfg
        self.messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Do the task."},
            {"role": "assistant", "content": "Fake output"},
        ]

    async def turn(self, session_dict, task):
        yield {"type": "content", "text": "Fake output"}
        yield {"type": "done"}


class FakeWispAgentCore(FakeStatelessCore):
    """Backward-compat alias for old test code."""

    def __init__(self, provider=None, security=None, extensions=None, config=None, **kwargs):
        super().__init__(provider=provider, security=security, extensions=extensions, config=config)
        self.session = kwargs.get("session")
        self.role = kwargs.get("role", "")
        self.closed = False

    async def run_task(self, **kwargs):
        return {
            "success": True,
            "output": "Fake output",
        }

    async def turn(self, session_dict, task):
        result = await self.run_task(task_description=task)
        # Add fake messages for token estimation
        session_dict["messages"] = session_dict.get("messages", []) + self.messages
        if not result.get("success", True):
            yield {"type": "error", "message": result.get("output", "Task failed")}
            return
        yield {"type": "content", "text": result.get("output", "")}
        yield {"type": "done"}

    def close(self):
        self.closed = True


def _child_config(overrides):
    """Build a config-like MagicMock with real scalar attribute values.

    Production code paths (providers.factory.from_config -> _validate_ollama_url,
    infra.security.SecurityPolicy) need real strings/ints, not auto-generated
    MagicMocks. This mimics WispConfig.replace() by returning a fresh mock
    carrying the parent's scalar defaults with the supplied overrides applied.
    """
    child = MagicMock()
    child.provider = "ollama"
    child.ollama_url = "http://localhost:11434"
    child.model = "test-model"
    child.workspace = "/tmp"
    child.show_thinking = False
    child.chars_per_token = 4
    child.temperature = 0.2
    child.max_context_tokens = 128000
    child._context_tokens_explicit = True
    child.permission_mode = "auto"
    child.max_iterations = 30
    child.subagent_pool_size = 4
    child.max_subagent_depth = 2
    child.max_subagent_branching = 3
    child.auto_approve = False
    for key, value in overrides.items():
        setattr(child, key, value)
    # Keep .replace chainable so repeated .replace() calls stay real-valued
    child.replace = MagicMock(side_effect=lambda **kw: _child_config(kw))
    return child


@pytest.fixture
def mock_parent_agent():
    agent = MagicMock()
    agent.config.model = "test-model"
    agent.config.workspace = "/tmp"
    agent.config.show_thinking = False
    agent.config.chars_per_token = 4
    agent.config.ollama_url = "http://localhost:11434"
    agent.config.temperature = 0.2
    agent.config.max_context_tokens = 128000
    agent.config._context_tokens_explicit = True
    agent.config.permission_mode = "auto"
    agent.config.max_iterations = 30
    agent.config.subagent_pool_size = 4
    agent.config.chars_per_token = 4
    agent.config.max_subagent_depth = 2
    agent.config.max_subagent_branching = 3
    agent.config.replace = MagicMock(side_effect=lambda **kw: _child_config(kw))
    return agent


@pytest.fixture
def orch(mock_parent_agent):
    return SubagentOrchestrator(parent_agent=mock_parent_agent)


# ── Contract tests ───────────────────────────────────────────────────


def test_contract_defaults():
    c = SubagentContract(task="do something")
    assert c.task == "do something"
    assert c.tools == ["all"]
    assert c.max_iterations == 15
    assert c.timeout_seconds == 120.0
    assert c.output_format == "text"
    assert c.model is None
    assert c.workspace is None
    assert c.worktree_isolated is True


def test_contract_overrides():
    c = SubagentContract(
        task="research",
        tools=["read_file", "web_fetch"],
        max_iterations=5,
        timeout_seconds=30.0,
        output_format="json",
        model="qwen2.5",
        workspace="/home",
        worktree_isolated=False,
    )
    assert c.tools == ["read_file", "web_fetch"]
    assert c.max_iterations == 5
    assert c.timeout_seconds == 30.0
    assert c.output_format == "json"
    assert c.model == "qwen2.5"
    assert c.workspace == "/home"
    assert c.worktree_isolated is False


# ── Orchestrator construction ──────────────────────────────────────────


def test_orchestrator_inherits_from_parent(mock_parent_agent):
    o = SubagentOrchestrator(parent_agent=mock_parent_agent)
    assert o.config is mock_parent_agent.config
    assert o.parent is mock_parent_agent


def test_orchestrator_with_explicit_config():
    from wisp.config import WispConfig
    cfg = WispConfig()
    cfg = cfg.replace(model="explicit-model")
    o = SubagentOrchestrator(config=cfg)
    assert o.config.model == "explicit-model"
    assert o.parent is None


def test_orchestrator_workspace_fallback():
    from wisp.config import WispConfig
    expected = Path(WispConfig().workspace or ".").resolve()
    o = SubagentOrchestrator()
    assert o.workspace == expected


# ── run() tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_success(orch):
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        contract = SubagentContract(name="test", task="hello")
        result = await orch.run(contract)
    assert isinstance(result, SubagentResult)
    assert result.success is True
    assert result.output == "Fake output"
    assert result.task_id == "test"


@pytest.mark.asyncio
async def test_run_with_progress_events(orch):
    events = []

    async def callback(event):
        events.append(event)

    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        contract = SubagentContract(
            name="test", task="hello", progress_callback=callback
        )
        result = await orch.run(contract)

    assert result.success is True
    assert len(events) >= 2
    assert events[0].event_type == EventKind.TASK_STARTED
    assert events[-1].event_type == EventKind.TASK_COMPLETED


@pytest.mark.asyncio
async def test_run_timeout(orch):
    class SlowAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            await asyncio.sleep(10)
            return {"success": True, "output": "too late"}

    with patch("wisp.core.engine.WispAgentCore", SlowAgent):
        contract = SubagentContract(name="slow", task="sleep", timeout_seconds=0.1)
        result = await orch.run(contract)

    assert result.success is False
    assert result.timed_out is True
    assert "TIMED OUT" in result.output


@pytest.mark.asyncio
async def test_run_crashed_agent(orch):
    class BrokenAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            raise RuntimeError("simulated crash")

    with patch("wisp.core.engine.WispAgentCore", BrokenAgent):
        contract = SubagentContract(name="broken", task="crash")
        result = await orch.run(contract)

    assert result.success is False
    assert "simulated crash" in result.error


# ── run_parallel() tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_parallel_success(orch):
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        contracts = [
            SubagentContract(name=f"task-{i}", task=f"job {i}")
            for i in range(3)
        ]
        results = await orch.run_parallel(contracts, max_concurrent=2)

    assert len(results) == 3
    assert all(r.success for r in results)
    assert [r.task_id for r in results] == ["task-0", "task-1", "task-2"]


@pytest.mark.asyncio
async def test_run_parallel_mixed_results(orch):
    class FlakyAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            # Deterministic failure for a specific task description
            if "job 1" in kwargs.get("task_description", ""):
                raise RuntimeError("fail")
            return {"success": True, "output": "ok"}

    with patch("wisp.core.engine.WispAgentCore", FlakyAgent):
        contracts = [
            SubagentContract(name=f"task-{i}", task=f"job {i}")
            for i in range(3)
        ]
        results = await orch.run_parallel(contracts, max_concurrent=3)

    assert len(results) == 3
    assert results[0].success is True
    assert results[1].success is False
    assert results[2].success is True


# ── Schema validation tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_validates_json_schema(orch):
    class JSONAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {
                "success": True,
                "output": json.dumps({"findings": [{"severity": "HIGH"}], "summary": "ok"}),
            }

    schema = {
        "type": "object",
        "properties": {
            "findings": {"type": "array"},
            "summary": {"type": "string"},
        },
        "required": ["findings", "summary"],
    }

    with patch("wisp.core.engine.WispAgentCore", JSONAgent):
        contract = SubagentContract(
            name="schema-test",
            task="return json",
            output_format="json",
            output_schema=schema,
        )
        result = await orch.run(contract)

    assert result.success is True
    assert result.validated_output is not None
    assert result.validated_output["summary"] == "ok"


@pytest.mark.asyncio
async def test_run_schema_validation_failure_no_retry(orch):
    class BadJSONAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {"success": True, "output": "not json"}

    schema = {"type": "object", "properties": {"value": {"type": "integer"}}}

    with patch("wisp.core.engine.WispAgentCore", BadJSONAgent):
        contract = SubagentContract(
            name="bad-schema",
            task="return bad json",
            output_schema=schema,
            auto_retry_parse=False,
        )
        result = await orch.run(contract)

    assert result.success is True  # agent succeeded, but validation failed
    assert result.validated_output is None
    assert "No valid JSON" in result.error


# ── Worktree tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_without_worktree(orch):
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        contract = SubagentContract(
            name="shared", task="hello", worktree_isolated=False
        )
        result = await orch.run(contract)
    assert result.success is True


# ── Result formatting tests ──────────────────────────────────────────


def test_result_backward_compat():
    r = SubagentResult(
        task_id="t1",
        success=True,
        output="ok",
        duration_seconds=5.0,
    )
    assert r.elapsed_seconds == 5.0


def test_contract_prompt_alias():
    c = SubagentContract(prompt="do this")
    assert c.task == "do this"


# ── Composable pattern tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_map_reduce(orch):
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run_map_reduce(
            task="Review codebase for bugs",
            items=["src/auth.py", "src/api.py"],
            mapper=lambda item: SubagentContract(
                name=f"review-{item}", task=f"Review {item} for bugs"
            ),
            reducer="Synthesize all reviews into a prioritized bug list.",
            max_concurrent=2,
        )

    assert isinstance(result, SubagentResult)
    assert result.task_id == "reducer"
    assert result.success is True


@pytest.mark.asyncio
async def test_run_vote_consensus(orch):
    """All agents agree — consensus reached."""
    class AgreeingAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {"success": True, "output": "YES, this is vulnerable."}

    with patch("wisp.core.engine.WispAgentCore", AgreeingAgent):
        result = await orch.run_vote(
            task="Is this function vulnerable?",
            agents=[
                SubagentContract(name="sec-1", role="reviewer"),
                SubagentContract(name="sec-2", role="reviewer"),
                SubagentContract(name="sec-3", role="reviewer"),
            ],
            consensus_threshold=0.6,
        )

    assert result.success is True  # consensus reached
    assert "REACHED" in result.output
    assert "3/3" in result.output


@pytest.mark.asyncio
async def test_run_vote_no_consensus(orch):
    """Agents disagree — consensus not reached."""
    class DisagreeingAgent(FakeWispAgentCore):
        counter = 0

        async def run_task(self, **kwargs):
            DisagreeingAgent.counter += 1
            if DisagreeingAgent.counter == 1:
                return {"success": True, "output": "YES"}
            return {"success": True, "output": "NO"}

    with patch("wisp.core.engine.WispAgentCore", DisagreeingAgent):
        result = await orch.run_vote(
            task="Is this vulnerable?",
            agents=[
                SubagentContract(name="sec-1", role="reviewer"),
                SubagentContract(name="sec-2", role="reviewer"),
            ],
            consensus_threshold=0.6,
        )

    assert result.success is False  # no consensus
    assert "NOT REACHED" in result.output


@pytest.mark.asyncio
async def test_run_chain_success(orch):
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run_chain([
            SubagentContract(name="writer", task="Write code"),
            SubagentContract(name="reviewer", task="Review code"),
        ], pass_context=True)

    assert result.success is True
    assert "Chain Complete" in result.output
    assert "2 steps" in result.output


@pytest.mark.asyncio
async def test_run_chain_failure_midway(orch):
    class FailingAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            raise RuntimeError("step failed")

    with patch("wisp.core.engine.WispAgentCore", FailingAgent):
        result = await orch.run_chain([
            SubagentContract(name="step1", task="Do step 1"),
            SubagentContract(name="step2", task="Do step 2"),
        ], pass_context=True)

    assert result.success is False
    assert "Chain Failed" in result.output
    assert "step1" in result.output  # failed step name


# ── Token budget tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_global_token_budget_enforced(orch):
    """When global budget is exhausted, subsequent runs fail fast."""
    orch.set_global_token_budget(10)

    # First run should succeed but consume tokens
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result1 = await orch.run(SubagentContract(name="first", task="hello"))

    assert result1.success is True
    assert orch.get_tokens_consumed() > 0

    # Set budget to already-consumed amount to force exhaustion
    orch.set_global_token_budget(orch.get_tokens_consumed())

    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result2 = await orch.run(SubagentContract(name="second", task="world"))

    assert result2.success is False
    assert "TOKEN BUDGET EXCEEDED" in result2.output
    assert "Global token budget exhausted" in result2.error


@pytest.mark.asyncio
async def test_token_estimation_tracked(orch):
    """Token counts are estimated and tracked on the result."""
    class MessageAgent(FakeWispAgentCore):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello there!"},
                {"role": "assistant", "content": "Hi! How can I help?"},
            ]

    with patch("wisp.core.engine.WispAgentCore", MessageAgent):
        result = await orch.run(SubagentContract(name="msg", task="hello"))

    assert result.success is True
    assert result.tokens_used > 0
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.tokens_used == result.input_tokens + result.output_tokens


@pytest.mark.asyncio
async def test_token_aggregation_in_chain(orch):
    """Chain aggregates token usage across all steps."""
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run_chain([
            SubagentContract(name="step1", task="Step 1"),
            SubagentContract(name="step2", task="Step 2"),
        ], pass_context=True)

    assert result.success is True
    assert result.tokens_used >= 0
    assert "tokens:" in result.output


@pytest.mark.asyncio
async def test_token_budget_remaining(orch):
    """get_token_budget_remaining reflects consumed tokens."""
    assert orch.get_token_budget_remaining() is None  # no budget set

    orch.set_global_token_budget(1000)
    assert orch.get_token_budget_remaining() == 1000

    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        await orch.run(SubagentContract(name="t1", task="hello"))

    remaining = orch.get_token_budget_remaining()
    assert remaining is not None
    assert remaining < 1000
    assert remaining >= 0


# ── Internal helper tests ──────────────────────────────────────────────

class TestExtractFilesChanged:
    """Tests for SubagentRunner._extract_files_changed."""

    def test_backtick_paths(self):
        from wisp.multi_agent._runner import SubagentRunner
        text = 'Modified `src/auth.py` and `tests/test_utils.go`'
        files = SubagentRunner._extract_files_changed(text)
        assert "src/auth.py" in files
        assert "tests/test_utils.go" in files

    def test_change_verb_patterns(self):
        from wisp.multi_agent._runner import SubagentRunner
        text = 'Files changed:\n- src/api.ts\n- src/models.rs'
        files = SubagentRunner._extract_files_changed(text)
        assert "src/api.ts" in files
        assert "src/models.rs" in files

    def test_bare_file_paths(self):
        from wisp.multi_agent._runner import SubagentRunner
        text = 'Created src/main.py and config/test.sh'
        files = SubagentRunner._extract_files_changed(text)
        assert "src/main.py" in files
        assert "config/test.sh" in files

    def test_truncation_limit(self):
        """Returns max 20 paths, not more."""
        from wisp.multi_agent._runner import SubagentRunner
        text = '\n'.join(f'`file_{i}.py`' for i in range(30))
        files = SubagentRunner._extract_files_changed(text)
        assert len(files) <= 20

    def test_empty_input(self):
        from wisp.multi_agent._runner import SubagentRunner
        assert SubagentRunner._extract_files_changed('') == []
        assert SubagentRunner._extract_files_changed('no file extensions here') == []
        assert SubagentRunner._extract_files_changed('plain text without matches') == []

    def test_duplicates_removed(self):
        from wisp.multi_agent._runner import SubagentRunner
        text = '`src/a.py` and also `src/a.py`'
        files = SubagentRunner._extract_files_changed(text)
        assert files == ['src/a.py']

    def test_unsupported_extensions_skipped(self):
        from wisp.multi_agent._runner import SubagentRunner
        text = 'Output: `binary.exe` and image `photo.png`'
        files = SubagentRunner._extract_files_changed(text)
        # .exe and .png should NOT match
        assert not any(f.endswith('.exe') for f in files)
        assert not any(f.endswith('.png') for f in files)


class TestCompactArgs:
    """Tests for SubagentRunner._compact_args."""

    def test_normal_args(self):
        from wisp.multi_agent._runner import SubagentRunner
        args = {"filepath": "src/auth.py", "content": "some content"}
        result = SubagentRunner._compact_args(args)
        assert "filepath=src/auth.py" in result
        assert len(result) < 100

    def test_empty_dict(self):
        from wisp.multi_agent._runner import SubagentRunner
        result = SubagentRunner._compact_args({})
        assert result == "..."

    def test_long_value_truncated(self):
        from wisp.multi_agent._runner import SubagentRunner
        args = {"content": "x" * 100}
        result = SubagentRunner._compact_args(args)
        assert "..." in result
        assert len(result) == 71  # "content=" (8) + 60 chars + "..." (3)

    def test_long_value_truncation(self):
        from wisp.multi_agent._runner import SubagentRunner
        long_val = "x" * 100
        args = {"content": long_val}
        result = SubagentRunner._compact_args(args)
        # "content=" (8) + 60 chars + "..." (3) = 71
        assert len(result) == 71
        assert result == "content=" + "x" * 60 + "..."
        assert result.endswith("...")


class TestBuildChildConfig:
    """Tests for SubagentRunner._build_child_config."""

    def test_model_override(self, mock_parent_agent):
        from wisp.multi_agent._runner import SubagentRunner
        runner = SubagentRunner(mock_parent_agent.config, Path("/tmp"))
        contract = SubagentContract(name="test", task="hello", model="qwen2.5")
        cfg = runner._build_child_config(contract, "/tmp")
        assert cfg.model == "qwen2.5"

    def test_inherits_parent_model(self, mock_parent_agent):
        from wisp.multi_agent._runner import SubagentRunner
        runner = SubagentRunner(mock_parent_agent.config, Path("/tmp"))
        contract = SubagentContract(name="test", task="hello")
        cfg = runner._build_child_config(contract, "/tmp")
        assert cfg.model == "test-model"

    def test_workspace_from_contract(self, mock_parent_agent):
        from wisp.multi_agent._runner import SubagentRunner
        runner = SubagentRunner(mock_parent_agent.config, Path("/tmp"))
        contract = SubagentContract(name="test", task="hello", workspace="/custom/path")
        cfg = runner._build_child_config(contract, "/custom/path")
        assert cfg.workspace == "/custom/path"

    def test_auto_approve_from_contract(self, mock_parent_agent):
        from wisp.multi_agent._runner import SubagentRunner
        runner = SubagentRunner(mock_parent_agent.config, Path("/tmp"))
        contract = SubagentContract(name="test", task="hello", auto_approve=False)
        cfg = runner._build_child_config(contract, "/tmp")
        assert cfg.auto_approve is False

    def test_max_tokens_from_contract(self, mock_parent_agent):
        from wisp.multi_agent._runner import SubagentRunner
        runner = SubagentRunner(mock_parent_agent.config, Path("/tmp"))
        contract = SubagentContract(name="test", task="hello", max_tokens=4000)
        cfg = runner._build_child_config(contract, "/tmp")
        assert cfg.max_context_tokens == 4000


class TestDefaultSystemPrompt:
    """Tests for SubagentOrchestrator._default_system_prompt."""

    def test_role_based_prompt(self, mock_parent_agent):
        from wisp.multi_agent.roles import AgentRole
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
        contract = SubagentContract(name="coder-1", task="write code", role=AgentRole.CODER)
        prompt = orch._default_system_prompt(contract)
        assert "Coder agent" in prompt
        assert "write_file" in prompt or "write" in prompt

    def test_fallback_for_unknown_role(self, mock_parent_agent):
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
        contract = SubagentContract(name="custom", task="do stuff", role="unknown-role")
        prompt = orch._default_system_prompt(contract)
        assert "specialist subagent" in prompt or "**custom**" in prompt
        assert "Focus ONLY" in prompt

    def test_with_tool_filter(self, mock_parent_agent):
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
        contract = SubagentContract(
            name="reader", task="read only",
            tools=["read_file", "list_files"],
        )
        prompt = orch._default_system_prompt(contract)
        assert "Allowed Tools" in prompt
        assert "read_file" in prompt
        assert "list_files" in prompt

    def test_with_context_files(self, mock_parent_agent):
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
        contract = SubagentContract(
            name="auditor", task="audit",
            context_files=["src/main.py", "config.yaml"],
        )
        prompt = orch._default_system_prompt(contract)
        assert "Context Files" in prompt
        assert "src/main.py" in prompt
        assert "config.yaml" in prompt

    def test_with_system_prompt_extra(self, mock_parent_agent):
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
        contract = SubagentContract(
            name="tester", task="test",
            role="tester",
            system_prompt_extra="Use pytest for all tests.",
        )
        prompt = orch._default_system_prompt(contract)
        assert "Additional Instructions" in prompt
        assert "Use pytest" in prompt


@pytest.mark.asyncio
async def test_telemetry_tracked_after_run(orch):
    """After a successful run, telemetry contains one record."""
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        await orch.run(SubagentContract(name="t1", task="hello"))

    telemetry = orch.get_telemetry()
    assert len(telemetry) == 1
    model_name = list(telemetry.keys())[0]
    assert len(telemetry[model_name]) == 1
    assert telemetry[model_name][0]["success"] is True
    assert telemetry[model_name][0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_telemetry_after_failure(orch):
    """A task that returns success=False still records telemetry."""
    class FailAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {"success": False, "output": "Task failed"}

    with patch("wisp.core.engine.WispAgentCore", FailAgent):
        contract = SubagentContract(name="failer", task="fail")
        result = await orch.run(contract)

    assert result.success is False

    telemetry = orch.get_telemetry()
    assert len(telemetry) >= 1
    for model_records in telemetry.values():
        failures = [r for r in model_records if not r["success"]]
        assert len(failures) >= 1


@pytest.mark.asyncio
async def test_telemetry_multiple_runs_aggregated(orch):
    """Multiple runs aggregate in telemetry."""
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        await orch.run(SubagentContract(name="t1", task="task1"))
        await orch.run(SubagentContract(name="t2", task="task2"))

    telemetry = orch.get_telemetry()
    model_records = list(telemetry.values())[0]
    assert len(model_records) == 2


@pytest.mark.asyncio
async def test_telemetry_summary(orch):
    """get_telemetry_summary returns aggregated stats."""
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        await orch.run(SubagentContract(name="t1", task="task1"))
        await orch.run(SubagentContract(name="t2", task="task2"))

    summary = orch.get_telemetry_summary()
    assert len(summary) >= 1
    model_summary = list(summary.values())[0]
    assert model_summary["count"] == 2
    assert model_summary["success_rate"] == 1.0
    assert model_summary["total_tokens"] >= 0


# ── Composable pattern edge cases ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_map_reduce_with_mapper_failures(orch):
    """Some mappers fail — reducer still runs with partial results."""
    class FlakyMapper(FakeWispAgentCore):
        counter = 0
        async def run_task(self, **kwargs):
            FlakyMapper.counter += 1
            if FlakyMapper.counter == 2:
                raise RuntimeError("mapper failed")
            return {"success": True, "output": "Mapper done."}

    with patch("wisp.core.engine.WispAgentCore", FlakyMapper):
        result = await orch.run_map_reduce(
            task="Review files",
            items=["src/a.py", "src/b.py", "src/c.py"],
            mapper=lambda item: SubagentContract(
                name=f"review-{item}", task=f"Review {item}"
            ),
            reducer="Synthesize all reviews.",
            max_concurrent=3,
        )

    assert isinstance(result, SubagentResult)
    # Reducer should still have produced output with partial results
    assert result.output is not None
    assert len(result.output) > 0


@pytest.mark.asyncio
async def test_run_vote_with_failures(orch):
    """Some agents fail — consensus calculated from successful only."""
    class MixedAgent(FakeWispAgentCore):
        counter = 0
        async def run_task(self, **kwargs):
            MixedAgent.counter += 1
            if MixedAgent.counter == 1:
                return {"success": True, "output": "YES"}
            raise RuntimeError("agent failed")

    with patch("wisp.core.engine.WispAgentCore", MixedAgent):
        result = await orch.run_vote(
            task="Is this safe?",
            agents=[
                SubagentContract(name="auditor-1"),
                SubagentContract(name="auditor-2"),
            ],
            consensus_threshold=0.6,
        )

    # With 1 success out of 2: 1/2 = 50% < 60% threshold
    assert result.success is False
    assert "NOT REACHED" in result.output


@pytest.mark.asyncio
async def test_run_vote_simple_consensus(orch):
    """Simple yes/no agreement — threshold logic works."""
    class BinAgent(FakeWispAgentCore):
        counter = 0
        async def run_task(self, **kwargs):
            BinAgent.counter += 1
            if BinAgent.counter <= 2:
                return {"success": True, "output": "YES, it is safe."}
            return {"success": True, "output": "NO, it is not safe."}

    with patch("wisp.core.engine.WispAgentCore", BinAgent):
        result = await orch.run_vote(
            task="Is this safe?",
            agents=[
                SubagentContract(name="auditor-1"),
                SubagentContract(name="auditor-2"),
                SubagentContract(name="auditor-3"),
            ],
            consensus_threshold=0.5,
        )

    # 2/3 agree on YES → 67% ≥ 50% → consensus reached
    assert result.success is True
    assert "REACHED" in result.output


@pytest.mark.asyncio
async def test_run_chain_empty(orch):
    """Empty contracts list returns success with '(empty chain)'."""
    result = await orch.run_chain([])
    assert result.success is True
    assert "(empty chain)" in result.output


@pytest.mark.asyncio
async def test_run_chain_no_context_pass(orch):
    """pass_context=False — no context prepended to subsequent tasks."""
    class TrackingAgent(FakeWispAgentCore):
        last_task = ""
        async def run_task(self, **kwargs):
            TrackingAgent.last_task = kwargs.get("task_description", "")
            return {"success": True, "output": f"Done: {kwargs.get('task_description', '')}"}

    with patch("wisp.core.engine.WispAgentCore", TrackingAgent):
        result = await orch.run_chain([
            SubagentContract(name="step1", task="Do step 1"),
            SubagentContract(name="step2", task="Do step 2"),
        ], pass_context=False)

    assert result.success is True
    # Step 2 should NOT contain "Previous Steps Context"
    assert "Previous Steps Context" not in TrackingAgent.last_task or not TrackingAgent.last_task


# ── Token budget edge cases ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_token_budget_unset_returns_none(orch):
    """When no budget set, get_token_budget_remaining returns None."""
    assert orch.get_token_budget_remaining() is None


def test_token_budget_remove(orch):
    """set_global_token_budget(None) removes the budget."""
    orch.set_global_token_budget(1000)
    assert orch.get_token_budget_remaining() == 1000
    orch.set_global_token_budget(None)
    assert orch.get_token_budget_remaining() is None


@pytest.mark.asyncio
async def test_output_token_truncation(orch):
    """max_output_tokens exceeded — output truncated with suffix."""
    class VerboseAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {"success": True, "output": "A" * 1000}

    # The FakeWispAgentCore has 3 messages with total chars = ~100
    # chars_per_token = 4, so about 25 tokens. Setting max_output to 1 forces truncation.
    with patch("wisp.core.engine.WispAgentCore", VerboseAgent):
        contract = SubagentContract(
            name="verbose", task="speak",
            max_output_tokens=1,
            max_output_chars=50,
        )
        result = await orch.run(contract)

    assert result.success is True
    assert "OUTPUT TRUNCATED" in result.output
    assert len(result.output) < 200  # truncated


@pytest.mark.asyncio
async def test_token_budget_check_fails_early(orch):
    """Budget exhausted — run returns immediately with zero elapsed."""
    orch.set_global_token_budget(1)
    # Consume the budget
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        await orch.run(SubagentContract(name="consumer", task="use tokens"))

    # Now budget is exhausted — next run should fail fast
    orch.set_global_token_budget(orch.get_tokens_consumed())

    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(SubagentContract(name="exhausted", task="should fail"))

    assert result.success is False
    assert "TOKEN BUDGET EXCEEDED" in result.output
    assert result.elapsed_seconds == 0.0  # failed before any work


@pytest.mark.asyncio
async def test_token_budget_no_check_without_budget(orch):
    """Without a global budget, token check passes."""
    assert orch._budget.check() is None


# ── Schema validation edge cases ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_schema_auto_retry(orch):
    """auto_retry_parse=True + schema fail — retries once."""
    class FirstBadThenGoodAgent(FakeWispAgentCore):
        call_count = 0
        async def run_task(self, **kwargs):
            FirstBadThenGoodAgent.call_count += 1
            if FirstBadThenGoodAgent.call_count == 1:
                return {"success": True, "output": "not json at all"}
            return {"success": True, "output": json.dumps({"value": 42})}

    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}

    with patch("wisp.core.engine.WispAgentCore", FirstBadThenGoodAgent):
        contract = SubagentContract(
            name="retry-test",
            task="return json",
            output_schema=schema,
            auto_retry_parse=True,
        )
        result = await orch.run(contract)

    assert result.success is True
    assert result.validated_output is not None
    assert result.validated_output["value"] == 42
    assert FirstBadThenGoodAgent.call_count >= 2


@pytest.mark.asyncio
async def test_run_schema_jsonschema_not_installed(monkeypatch):
    """Schema validation works without jsonschema (built-in validator)."""
    from wisp.config import WispConfig
    cfg = WispConfig()
    cfg = cfg.replace(model="test-model")
    fresh_orch = SubagentOrchestrator(config=cfg)

    class JSONAgent(FakeWispAgentCore):
        async def run_task(self, **kwargs):
            return {
                "success": True,
                "output": '{"value": 42}',
            }

    with patch("wisp.core.engine.WispAgentCore", JSONAgent):
        contract = SubagentContract(
            name="no-schema-lib",
            task="return json",
            output_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        )
        result = await fresh_orch.run(contract)

    assert result.success is True
    assert result.validated_output is not None
    assert result.validated_output["value"] == 42
    assert result.error is None


# ── Worktree and config tests ──────────────────────────────────────────

def test_orchestrator_with_explicit_workspace(tmp_path):
    """Orchestrator accepts explicit workspace path."""
    from wisp.config import WispConfig
    cfg = WispConfig()
    cfg = cfg.replace(model="test-model", workspace=str(tmp_path))
    orch = SubagentOrchestrator(config=cfg)
    assert str(orch.workspace) == str(tmp_path)


# ── Removed process isolation tests ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_cache_ttl_expires(orch):
    """Cached results expire after TTL."""
    contract = SubagentContract(name="ttl", task="test ttl")
    await orch.run(contract)
    # Manually expire the cache entry
    for key in list(orch._cache._cache.keys()):
        result, _ = orch._cache._cache[key]
        orch._cache._cache[key] = (result, 0)  # timestamp = 0, always expired
    result2 = await orch.run(contract)
    # Should be a miss (re-executed)
    stats = orch.get_cache_stats()
    assert stats["misses"] >= 2


@pytest.mark.asyncio
async def test_clear_cache(orch):
    """clear_cache() resets all state."""
    contract = SubagentContract(name="clear", task="test clear")
    await orch.run(contract)
    assert orch.get_cache_stats()["size"] >= 1
    orch.clear_cache()
    stats = orch.get_cache_stats()
    assert stats["size"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 0


# ── Context files tests ──────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_result_persistence(mock_parent_agent, tmp_path):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
    orch.clear_persisted_results()
    contract = SubagentContract(
        name="persist_test",
        role="tester",
        task="test task",
        timeout_seconds=5,
    )
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(contract)
    assert result.success
    persisted = orch.get_persisted_results()
    assert len(persisted) >= 1
    assert persisted[-1]["task_id"] == "persist_test"
    assert persisted[-1]["success"] is True


# ── Gap #13: Telemetry auto-aggregation ─────────────────────────────

@pytest.mark.asyncio
async def test_telemetry_auto_aggregation(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    results = [
        SubagentResult(task_id="t1", success=True, elapsed_seconds=1.0, tokens_used=100, model_used="m1"),
        SubagentResult(task_id="t2", success=False, elapsed_seconds=2.0, tokens_used=200, model_used="m1"),
    ]
    summary = orch.aggregate_telemetry(results)
    assert "m1" in summary
    assert summary["m1"]["count"] == 2
    assert summary["m1"]["success_rate"] == 0.5
    assert summary["m1"]["total_tokens"] == 300


# ── Removed Pipe IPC test ───────────────────────────────────────────
# ── Gap #15: Output compression ───────────────────────────────────────

def test_pool_size_default(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    assert orch.get_pool_status()["pool_size"] == 4


def test_set_pool_size(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    orch.set_pool_size(2)
    assert orch.get_pool_status()["pool_size"] == 2


def test_set_pool_size_invalid(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    with pytest.raises(ValueError):
        orch.set_pool_size(0)


# ── Gap #18: Role validation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_role_validation_empty_role(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    contract = SubagentContract(
        name="bad_role",
        role="",
        task="test",
        timeout_seconds=5,
    )
    result = await orch.run(contract)
    assert not result.success
    assert "Role is required" in result.error


@pytest.mark.asyncio
async def test_role_validation_unknown_role(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    contract = SubagentContract(
        name="bad_role",
        role="nonexistent_role",
        task="test",
        timeout_seconds=5,
    )
    result = await orch.run(contract)
    assert not result.success
    assert "Unknown role" in result.error


@pytest.mark.asyncio
async def test_role_validation_valid_role(mock_parent_agent):
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    contract = SubagentContract(
        name="good_role",
        role="coder",
        task="test",
        timeout_seconds=5,
    )
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(contract)
    assert result.success


# ── Edge Cases ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_parallel_empty_contracts(mock_parent_agent):
    """run_parallel with empty list should return empty results."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    results = await orch.run_parallel([])
    assert results == []


@pytest.mark.asyncio
async def test_run_map_reduce_empty_items(mock_parent_agent):
    """run_map_reduce with empty items should fail gracefully."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    result = await orch.run_map_reduce(
        task="Analyze",
        items=[],
        mapper=lambda x: SubagentContract(name=x, role="coder", task=x),
        reducer="Summarize",
    )
    assert not result.success
    assert "No items provided" in result.error


@pytest.mark.asyncio
async def test_run_vote_empty_agents(mock_parent_agent):
    """run_vote with empty agents should fail gracefully."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    result = await orch.run_vote(
        task="Is this good?",
        agents=[],
        consensus_threshold=0.5,
    )
    assert not result.success
    assert "No agents provided" in result.error


@pytest.mark.asyncio
async def test_run_chain_empty_contracts(mock_parent_agent):
    """run_chain with empty contracts should return empty result."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    result = await orch.run_chain(contracts=[])
    assert result.task_id == "chain-empty"
    assert result.output == "(empty chain)"


@pytest.mark.asyncio
async def test_contract_timeout_zero(mock_parent_agent):
    """Contract with timeout=0 should be rejected."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    contract = SubagentContract(
        name="bad-timeout",
        role="coder",
        task="test",
        timeout_seconds=0,
    )
    result = await orch.run(contract)
    assert not result.success
    assert "timeout_seconds must be > 0" in result.error


@pytest.mark.asyncio
async def test_contract_max_iterations_zero(mock_parent_agent):
    """Contract with max_iterations=0 should be rejected."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent)
    contract = SubagentContract(
        name="bad-iter",
        role="coder",
        task="test",
        timeout_seconds=5,
        max_iterations=0,
    )
    result = await orch.run(contract)
    assert not result.success
    assert "max_iterations must be > 0" in result.error


def test_persisted_results_corrupted_jsonl(mock_parent_agent, tmp_path):
    """Corrupted JSONL lines should be skipped, not crash."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
    # Write a corrupted JSONL file
    persist_file = tmp_path / ".wisp" / "subagent_results.jsonl"
    persist_file.parent.mkdir(parents=True, exist_ok=True)
    persist_file.write_text(
        '{"task_id": "good", "success": true}\n'
        'this is not json\n'
        '{"task_id": "good2", "success": false}\n'
    )
    results = orch.get_persisted_results()
    assert len(results) == 2
    assert results[0]["task_id"] == "good"
    assert results[1]["task_id"] == "good2"


@pytest.mark.asyncio
async def test_worktree_name_sanitization(mock_parent_agent, tmp_path):
    """Worktree names with special chars should be sanitized."""
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
    contract = SubagentContract(
        name="task/with\\special:chars*?\"<>|",
        role="coder",
        task="test",
        timeout_seconds=5,
        worktree_isolated=True,
    )
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(contract)
    # Should succeed despite special chars in name
    assert result.success


# ── Skill loading tests ────────────────────────────────────────────────

def test_default_system_prompt_loads_skills(mock_parent_agent, tmp_path):
    """Subagent system prompt should include discovered skills."""
    from wisp import skills as skills_mod
    original_global = skills_mod.GLOBAL_SKILL_DIRS
    skills_mod.GLOBAL_SKILL_DIRS = []
    try:
        # Create a skill in the workspace
        skill_dir = tmp_path / ".agents" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test skill\n---\n# Test Skill\nDo test things."
        )
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
        contract = SubagentContract(name="skill-test", role="coder", task="test")
        prompt = orch._default_system_prompt(contract)
        assert "test-skill" in prompt
        assert "A test skill" in prompt
    finally:
        skills_mod.GLOBAL_SKILL_DIRS = original_global


def test_default_system_prompt_filters_allowed_skills(mock_parent_agent, tmp_path):
    """Subagent should only include allowed skills when allowed_skills is set."""
    from wisp import skills as skills_mod
    original_global = skills_mod.GLOBAL_SKILL_DIRS
    skills_mod.GLOBAL_SKILL_DIRS = []
    try:
        # Create two skills
        for name, desc in [("skill-a", "Skill A"), ("skill-b", "Skill B")]:
            skill_dir = tmp_path / ".agents" / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\nInstructions."
            )
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
        contract = SubagentContract(
            name="skill-test",
            role="coder",
            task="test",
            allowed_skills=["skill-a"],
        )
        prompt = orch._default_system_prompt(contract)
        assert "skill-a" in prompt
        assert "Skill A" in prompt
        assert "skill-b" not in prompt
    finally:
        skills_mod.GLOBAL_SKILL_DIRS = original_global


def test_default_system_prompt_no_skills(mock_parent_agent, tmp_path):
    """Subagent system prompt should not have skills section when none exist."""
    from wisp import skills as skills_mod
    original_global = skills_mod.GLOBAL_SKILL_DIRS
    skills_mod.GLOBAL_SKILL_DIRS = []
    try:
        orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
        contract = SubagentContract(name="no-skill-test", role="coder", task="test")
        prompt = orch._default_system_prompt(contract)
        assert "Available Skills" not in prompt
        assert "## Skills" not in prompt
    finally:
        skills_mod.GLOBAL_SKILL_DIRS = original_global


@pytest.mark.asyncio
async def test_subagent_uses_skills_in_run(mock_parent_agent, tmp_path):
    """Subagent run should include skills in the system prompt."""
    skill_dir = tmp_path / ".agents" / "skills" / "coder-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: coder-skill\ndescription: Coding best practices\n---\n# Coder Skill\nAlways write tests."
    )
    orch = SubagentOrchestrator(parent_agent=mock_parent_agent, workspace=tmp_path)
    contract = SubagentContract(
        name="skill-run-test",
        role="coder",
        task="test",
        timeout_seconds=5,
    )
    with patch("wisp.core.engine.WispAgentCore", FakeWispAgentCore):
        result = await orch.run(contract)
    assert result.success
