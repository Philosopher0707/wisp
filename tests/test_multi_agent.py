"""Tests for the multi-agent swarm system."""

import pytest
import threading
import time
import asyncio

from wisp.multi_agent.protocol import AgentEvent, EventType, TaskAssignment, TaskResult
from wisp.multi_agent.registry import AgentRegistry, AgentRecord, AgentStatus
from wisp.multi_agent.bus import MessageBus
from wisp.multi_agent.roles import AgentRole, ROLE_CONFIGS
from wisp.multi_agent.workspace_lock import WorkspaceLock


# ── Protocol tests ───────────────────────────────────────────────────

class TestProtocol:
    def test_event_creation(self):
        event = AgentEvent(
            event_type=EventType.BROADCAST,
            source_agent="agent-1",
            payload={"msg": "hello"},
        )
        assert event.source_agent == "agent-1"
        assert event.event_type == EventType.BROADCAST
        assert event.target_agent is None

    def test_event_roundtrip(self):
        event = AgentEvent(
            event_type=EventType.TASK_ASSIGNED,
            source_agent="orch",
            target_agent="coder-1",
            payload={"task_id": "t1"},
        )
        d = event.to_dict()
        restored = AgentEvent.from_dict(d)
        assert restored.event_type == EventType.TASK_ASSIGNED
        assert restored.source_agent == "orch"
        assert restored.target_agent == "coder-1"
        assert restored.payload["task_id"] == "t1"

    def test_task_assignment_roundtrip(self):
        ta = TaskAssignment(
            task_id="t1",
            description="fix bug",
            expected_output="working code",
            max_iterations=5,
            timeout_seconds=60,
        )
        event = ta.to_event(source="orch", target="coder-1")
        restored = TaskAssignment.from_event(event)
        assert restored.task_id == "t1"
        assert restored.description == "fix bug"
        assert restored.max_iterations == 5

    def test_task_result_roundtrip(self):
        tr = TaskResult(
            task_id="t1",
            success=True,
            output="done",
            files_changed=["foo.py"],
            elapsed_seconds=1.5,
            iterations_used=3,
        )
        event = tr.to_event(source="coder-1", target="orch")
        restored = TaskResult.from_event(event)
        assert restored.success is True
        assert restored.files_changed == ["foo.py"]
        assert restored.iterations_used == 3


# ── Registry tests ───────────────────────────────────────────────────

class TestRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        rec = AgentRecord(agent_id="a1", role="coder")
        reg.register(rec)
        assert reg.get("a1").role == "coder"

    def test_unregister(self):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        reg.unregister("a1")
        assert reg.get("a1") is None

    def test_update_status(self):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        reg.update_status("a1", AgentStatus.WORKING, task="fix bug")
        rec = reg.get("a1")
        assert rec.status == AgentStatus.WORKING
        assert rec.current_task == "fix bug"

    def test_claim_and_release_file(self):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        reg.register(AgentRecord(agent_id="a2", role="coder"))

        assert reg.claim_file("a1", "foo.py") is True
        assert reg.claim_file("a2", "foo.py") is False  # Already locked
        assert reg.claim_file("a1", "foo.py") is True  # Same agent can re-claim

        reg.release_file("a1", "foo.py")
        assert reg.claim_file("a2", "foo.py") is True

    def test_list_by_role(self):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        reg.register(AgentRecord(agent_id="a2", role="tester"))
        assert len(reg.list_by_role("coder")) == 1
        assert len(reg.list_by_role("tester")) == 1

    def test_count_active(self):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder", status=AgentStatus.IDLE))
        reg.register(AgentRecord(agent_id="a2", role="tester", status=AgentStatus.STOPPED))
        assert reg.count_active() == 1


# ── MessageBus tests ─────────────────────────────────────────────────

class TestMessageBus:
    def test_emit_and_subscribe(self):
        bus = MessageBus()
        received = []

        def handler(event):
            received.append(event)

        unsub = bus.subscribe(handler)
        event = AgentEvent(event_type=EventType.BROADCAST, source_agent="a1")
        bus.emit(event)

        assert len(received) == 1
        assert received[0].source_agent == "a1"
        unsub()

    def test_filter_by_event_type(self):
        bus = MessageBus()
        received = []

        bus.subscribe(lambda e: received.append(e), event_type=EventType.TASK_RESULT)
        bus.emit(AgentEvent(event_type=EventType.TASK_RESULT, source_agent="a1"))
        bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="a2"))

        assert len(received) == 1
        assert received[0].source_agent == "a1"

    def test_filter_by_agent_id(self):
        bus = MessageBus()
        received = []

        bus.subscribe(lambda e: received.append(e), agent_id="target-1")
        bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="a1", target_agent="target-1"))
        bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="a2", target_agent="target-2"))

        assert len(received) == 1
        assert received[0].source_agent == "a1"

    def test_history(self):
        bus = MessageBus()
        for i in range(5):
            bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent=f"a{i}"))

        hist = bus.history(limit=3)
        assert len(hist) == 3
        assert hist[0].source_agent == "a2"

    def test_unsubscribe(self):
        bus = MessageBus()
        received = []

        unsub = bus.subscribe(lambda e: received.append(e))
        bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="a1"))
        unsub()
        bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="a2"))

        assert len(received) == 1

    def test_thread_safety(self):
        bus = MessageBus()
        count = [0]

        def handler(_):
            count[0] += 1

        bus.subscribe(handler)

        def emitter():
            for _ in range(50):
                bus.emit(AgentEvent(event_type=EventType.BROADCAST, source_agent="t1"))

        threads = [threading.Thread(target=emitter) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert count[0] == 200


# ── WorkspaceLock tests ──────────────────────────────────────────────

class TestWorkspaceLock:
    def test_acquire_and_release(self, tmp_path):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        lock = WorkspaceLock(str(tmp_path), reg)

        assert lock.acquire("a1", "foo.py") is True
        assert lock.is_locked("foo.py") is True
        assert lock.owner("foo.py") == "a1"

        lock.release("a1", "foo.py")
        assert lock.is_locked("foo.py") is False

    def test_conflict(self, tmp_path):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        reg.register(AgentRecord(agent_id="a2", role="coder"))
        lock = WorkspaceLock(str(tmp_path), reg)

        assert lock.acquire("a1", "foo.py") is True
        assert lock.acquire("a2", "foo.py") is False

    def test_release_all(self, tmp_path):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        lock = WorkspaceLock(str(tmp_path), reg)

        lock.acquire("a1", "foo.py")
        lock.acquire("a1", "bar.py")
        lock.release_all("a1")
        assert lock.is_locked("foo.py") is False
        assert lock.is_locked("bar.py") is False

    def test_path_outside_workspace(self, tmp_path):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder"))
        lock = WorkspaceLock(str(tmp_path), reg)

        with pytest.raises(ValueError):
            lock.acquire("a1", "/etc/passwd")

    def test_cleanup_stale(self, tmp_path):
        reg = AgentRegistry()
        reg.register(AgentRecord(agent_id="a1", role="coder", status=AgentStatus.STOPPED))
        lock = WorkspaceLock(str(tmp_path), reg)

        # Manually create a stale lock file
        lock_file = tmp_path / "foo.py.wisp_lock"
        lock_file.write_text("a1")
        assert lock.is_locked("foo.py") is True

        removed = lock.cleanup_stale()
        assert removed == 1
        assert lock.is_locked("foo.py") is False


# ── Role tests ───────────────────────────────────────────────────────

class TestRoles:
    def test_all_roles_have_configs(self):
        for role in [AgentRole.CODER, AgentRole.REVIEWER, AgentRole.TESTER, AgentRole.RESEARCHER, AgentRole.PLANNER, AgentRole.DEBUGGER]:
            assert role in ROLE_CONFIGS

    def test_coder_tools(self):
        cfg = ROLE_CONFIGS[AgentRole.CODER]
        assert "write_file" in cfg.allowed_tools
        assert "edit_file" in cfg.allowed_tools

    def test_reviewer_readonly(self):
        cfg = ROLE_CONFIGS[AgentRole.REVIEWER]
        assert "write_file" not in cfg.allowed_tools
        assert "read_file" in cfg.allowed_tools

    def test_researcher_no_writes(self):
        cfg = ROLE_CONFIGS[AgentRole.RESEARCHER]
        assert "write_file" not in cfg.allowed_tools
        assert "edit_file" not in cfg.allowed_tools


# ── Orchestrator tests ───────────────────────────────────────────────

class MockOllamaClient:
    def __init__(self, config):
        self.config = config
        self._session = None

    def generate(self, system_prompt, messages, tools=None):
        # Return the last user message as content (echo mode for testing)
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        return {"message": {"content": last_user}}

    def check_health(self):
        return True


class MockFileLock:
    def __init__(self, workspace, agent_id):
        self.agent_id = agent_id
        self._locks = set()

    def acquire(self, path, timeout_sec=300):
        self._locks.add(path)
        return True

    def release(self, path):
        self._locks.discard(path)

    def release_all(self):
        self._locks.clear()

    def list_active_locks(self):
        return []


class MockChangeTracker:
    def __init__(self, workspace, agent_id):
        self.agent_id = agent_id
        self._files = []

    def get_changed_files(self):
        return list(self._files)

    def record_write(self, filepath, content, description=""):
        self._files.append(filepath)

    def record_edit(self, filepath, old_text, new_text, description=""):
        self._files.append(filepath)


class MockWispAgent:
    """A lightweight fake agent for orchestrator testing."""

    def __init__(self, config=None, session=None, agent_id=None, role=None):
        self.config = config
        self.session = session
        self.agent_id = agent_id or "mock-agent"
        self.role = role or "coder"
        self.messages = []
        self.max_iterations = 10
        self._interrupted = False
        self._active_skill = None
        self._system_prompt_cache = {}
        self._role_system_extra = ""
        self._allowed_tools = None
        self.client = MockOllamaClient(config)
        self.file_lock = MockFileLock(".", self.agent_id)
        self.change_tracker = MockChangeTracker(".", self.agent_id)

    def _build_system_prompt(self, workspace=None):
        return "You are a test agent."

    def _get_tool_schemas(self):
        return []

    async def run_task(self, task_description, workspace=".", max_iterations=10, timeout_seconds=120.0):
        self.messages.append({"role": "user", "content": task_description})
        return {"success": True, "output": f"Completed: {task_description}"}


@pytest.fixture
def mock_orchestrator_deps(monkeypatch, tmp_path):
    """Patch WispAgent and related dependencies for orchestrator tests."""
    monkeypatch.setattr("wisp.multi_agent.agent_factory.WispAgent", MockWispAgent)
    monkeypatch.setattr("wisp.agent.WispAgent", MockWispAgent)
    # Prevent real context detection from failing in empty temp dirs
    monkeypatch.setattr("wisp.skills.discover_skills", lambda ws: [])
    monkeypatch.setattr("wisp.project_context.detect_project_context", lambda ws: {})
    monkeypatch.setattr("wisp.code_index.build_index", lambda ws: {})
    monkeypatch.setattr("wisp.tree_sitter_index.build_index", lambda ws: {})
    monkeypatch.setattr("wisp.tree_sitter_index.is_tree_sitter_available", lambda: False)
    monkeypatch.setattr("wisp.memory.format_memory_block", lambda ws: "")
    monkeypatch.setattr("wisp.git_context.format_git_context", lambda ws: "")
    monkeypatch.setattr("wisp.code_index.format_index_summary", lambda idx: "")
    monkeypatch.setattr("wisp.agent_memory.AgentMemory", lambda: type("FakeMem", (), {
        "load_recent": lambda *a, **k: [],
        "format_for_prompt": lambda *a, **k: "",
    })())
    monkeypatch.setattr("wisp.planner.PlanStore", lambda: type("FakePlan", (), {
        "load_active": lambda *a, **k: None,
    })())


class TestSwarmOrchestrator:
    def test_spawn_agents(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config, max_parallel=2)
        ids = orch.spawn_agents([AgentRole.CODER, AgentRole.TESTER])

        assert len(ids) == 2
        assert orch.registry.count_active() == 2
        assert len(orch.registry.list_by_role(AgentRole.CODER)) == 1
        assert len(orch.registry.list_by_role(AgentRole.TESTER)) == 1

    def test_stop_all(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config)
        orch.spawn_agents([AgentRole.CODER])
        orch.stop_all()

        assert orch.registry.count_active() == 0

    def test_plan_with_json_response(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config)

        # Mock the planner agent to return structured JSON
        planner = MockWispAgent(config, agent_id="planner-test", role=AgentRole.PLANNER)
        planner.messages = []
        planner.run_task = lambda **kw: asyncio.sleep(0, result={"success": True, "output": "plan done"})
        planner._run_turn_streaming = lambda system: {
            "message": {
                "content": '{"plan": "Write auth module", "subtasks": [{"role": "coder", "description": "Implement login", "expected_output": "login.py", "dependencies": []}]}'
            }
        }
        orch.factory.create = lambda role, agent_id, model=None: planner if role == AgentRole.PLANNER else MockWispAgent(config, agent_id=agent_id, role=role)

        plan, subtasks = orch._plan_sync("Implement user auth")
        assert plan == "Write auth module"
        assert len(subtasks) == 1
        assert subtasks[0]["role"] == "coder"

    def test_plan_with_markdown_fences(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config)

        planner = MockWispAgent(config, agent_id="planner-test", role=AgentRole.PLANNER)
        planner._run_turn_streaming = lambda system: {
            "message": {
                "content": '```json\n{"plan": "Fix bug", "subtasks": [{"role": "debugger", "description": "Find crash", "expected_output": "diagnosis", "dependencies": []}]}\n```'
            }
        }
        orch.factory.create = lambda role, agent_id, model=None: planner if role == AgentRole.PLANNER else MockWispAgent(config, agent_id=agent_id, role=role)

        plan, subtasks = orch._plan_sync("Fix crash")
        assert plan == "Fix bug"
        assert subtasks[0]["role"] == "debugger"

    def test_plan_fallback_on_invalid_json(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config)

        planner = MockWispAgent(config, agent_id="planner-test", role=AgentRole.PLANNER)
        planner._run_turn_streaming = lambda system: {"message": {"content": "Just some plain text without JSON"}}
        orch.factory.create = lambda role, agent_id, model=None: planner if role == AgentRole.PLANNER else MockWispAgent(config, agent_id=agent_id, role=role)

        plan, subtasks = orch._plan_sync("Do something vague")
        assert subtasks[0]["role"] == "coder"
        assert subtasks[0]["description"] == "Do something vague"

    def test_run_single_task(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config, max_parallel=1)

        # Override factory to return mock agents
        def make_agent(role, agent_id, model=None):
            a = MockWispAgent(config, agent_id=agent_id, role=role)
            # Simulate file changes for coder
            if role == AgentRole.CODER:
                a.run_task = lambda **kw: asyncio.sleep(0, result={"success": True, "output": "Created auth.py"})
            elif role == AgentRole.REVIEWER:
                a.run_task = lambda **kw: asyncio.sleep(0, result={"success": True, "output": "Looks good"})
            elif role == AgentRole.TESTER:
                a.run_task = lambda **kw: asyncio.sleep(0, result={"success": True, "output": "Tests pass"})
            return a

        orch.factory.create = make_agent

        # Override _plan_sync to return a simple single-task plan
        orch._plan_sync = lambda goal, available_roles=None: ("Simple plan", [{"role": "coder", "description": goal, "expected_output": "code", "dependencies": []}])

        result = orch.run("Write a hello world script")
        assert result.success is True
        assert "hello world" in result.goal.lower()

    def test_run_with_dependencies(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config, max_parallel=2)

        execution_order = []

        def make_agent(role, agent_id, model=None):
            a = MockWispAgent(config, agent_id=agent_id, role=role)
            async def _run_task(**kw):
                execution_order.append(agent_id)
                return {"success": True, "output": f"Done by {agent_id}"}
            a.run_task = _run_task
            return a

        orch.factory.create = make_agent
        orch._plan_sync = lambda goal, available_roles=None: (
            "Two-step plan",
            [
                {"role": "coder", "description": "Step 1", "expected_output": "code", "dependencies": []},
                {"role": "tester", "description": "Step 2", "expected_output": "tests", "dependencies": [0]},
            ],
        )

        result = orch.run("Build and test")
        assert result.success is True
        assert len(result.agent_results) == 2
        # Step 2 should have executed after step 1
        coder_idx = next(i for i, aid in enumerate(execution_order) if aid.startswith("coder-"))
        tester_idx = next(i for i, aid in enumerate(execution_order) if aid.startswith("tester-"))
        assert tester_idx > coder_idx

    def test_run_task_failure_handled(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.roles import AgentRole

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config, max_parallel=1)

        def make_agent(role, agent_id, model=None):
            a = MockWispAgent(config, agent_id=agent_id, role=role)
            a.run_task = lambda **kw: asyncio.sleep(0, result={"success": False, "output": "Error occurred"})
            return a

        orch.factory.create = make_agent
        orch._plan_sync = lambda goal, available_roles=None: ("Plan", [{"role": "coder", "description": goal, "expected_output": "code", "dependencies": []}])

        result = orch.run("Fail me")
        assert result.success is False
        assert len(result.agent_results) == 1
        assert result.agent_results[0].success is False

    def test_extract_file_changes(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config)

        content = 'Created file "src/auth.py" and edited "tests/test_auth.py"'
        files = orch._extract_file_changes(content)
        assert "src/auth.py" in files
        assert "tests/test_auth.py" in files

    def test_synthesize_output(self, mock_orchestrator_deps, tmp_path):
        from wisp.config import WispConfig
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
        from wisp.multi_agent.protocol import TaskResult

        config = WispConfig()
        config.workspace = str(tmp_path)
        config.model = "test-model"
        orch = SwarmOrchestrator(config)

        results = [
            TaskResult(task_id="task-0", success=True, output="Done", files_changed=["a.py"]),
            TaskResult(task_id="task-1", success=False, output="Oops", error="bug"),
        ]
        out = orch._synthesize("Goal", "Plan", results)
        assert "Swarm Result: Goal" in out
        assert "a.py" in out
        assert "bug" in out


def test_file_lock_prevents_collision(tmp_path):
    """Two agents cannot claim the same file simultaneously."""
    from wisp.file_lock import FileLock

    workspace = str(tmp_path)
    lock1 = FileLock(workspace, agent_id="agent-1")
    lock2 = FileLock(workspace, agent_id="agent-2")

    assert lock1.acquire("shared.py") is True
    assert lock2.acquire("shared.py") is False
    lock_info = lock2.lock_info("shared.py")
    assert lock_info is not None
    assert lock_info["agent"] == "agent-1"

    lock1.release("shared.py")
    assert lock2.acquire("shared.py") is True
    lock2.release("shared.py")
