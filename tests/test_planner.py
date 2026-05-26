"""Tests for wisp.planner — structured planning and task decomposition."""

import tempfile
from pathlib import Path


from wisp.planner import (
    PLANS_DIR,
    Plan,
    PlanStore,
    Task,
    _generate_plan_id,
    parse_plan_from_text,
)


class TestTask:
    """Unit tests for Task."""

    def test_is_ready_no_deps(self):
        t = Task(id="t1", description="Do something")
        assert t.is_ready(set())

    def test_is_ready_with_deps(self):
        t = Task(id="t2", description="Do later", dependencies=["t1"])
        assert not t.is_ready(set())
        assert t.is_ready({"t1"})

    def test_to_dict_roundtrip(self):
        t = Task(id="t1", description="Test", estimated_complexity="high")
        d = t.to_dict()
        restored = Task.from_dict(d)
        assert restored.id == "t1"
        assert restored.estimated_complexity == "high"


class TestPlan:
    """Unit tests for Plan."""

    def test_next_task_simple(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [
            Task(id="t1", description="First"),
            Task(id="t2", description="Second", dependencies=["t1"]),
        ]
        next_t = plan.next_task()
        assert next_t is not None
        assert next_t.id == "t1"

    def test_next_task_blocked(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [
            Task(id="t1", description="First"),
            Task(id="t2", description="Second", dependencies=["t1"]),
        ]
        plan.start_task("t1")
        next_t = plan.next_task()
        assert next_t is None  # t1 in progress, t2 blocked

    def test_progress(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [
            Task(id="t1", description="First"),
            Task(id="t2", description="Second"),
        ]
        assert plan.progress() == (0, 2)
        plan.complete_task("t1")
        assert plan.progress() == (1, 2)

    def test_is_complete(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        assert not plan.is_complete()
        plan.complete_task("t1")
        assert plan.is_complete()

    def test_start_and_complete(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        assert plan.start_task("t1")
        assert plan.tasks[0].status == "in_progress"
        assert plan.complete_task("t1", notes="Done")
        assert plan.tasks[0].status == "done"
        assert plan.tasks[0].notes == "Done"

    def test_skip_task(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        plan.skip_task("t1", reason="Not needed")
        assert plan.tasks[0].status == "skipped"
        assert plan.tasks[0].notes == "Not needed"

    def test_format_for_prompt(self):
        plan = Plan(goal="Build feature", workspace="/tmp")
        plan.tasks = [
            Task(id="t1", description="Setup"),
            Task(id="t2", description="Implement", dependencies=["t1"]),
        ]
        text = plan.format_for_prompt()
        assert "Build feature" in text
        assert "Setup" in text
        assert "Implement" in text
        assert "Progress: 0/2" in text

    def test_to_dict_roundtrip(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        d = plan.to_dict()
        restored = Plan.from_dict(d)
        assert restored.goal == "Test"
        assert len(restored.tasks) == 1


class TestPlanStore:
    """Unit tests for PlanStore persistence."""

    def setup_method(self):
        self._orig_dir = PLANS_DIR
        self.tmp = tempfile.TemporaryDirectory()
        import wisp.planner as planner
        planner.PLANS_DIR = Path(self.tmp.name)
        self.store = PlanStore()

    def teardown_method(self):
        import wisp.planner as planner
        planner.PLANS_DIR = self._orig_dir
        self.tmp.cleanup()

    def test_save_and_load(self):
        plan = Plan(goal="Test", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        self.store.save(plan)
        loaded = self.store.load(plan.id)
        assert loaded is not None
        assert loaded.goal == "Test"
        assert len(loaded.tasks) == 1

    def test_load_active(self):
        plan = Plan(goal="Active", workspace="/tmp/ws")
        plan.tasks = [Task(id="t1", description="First")]
        self.store.save(plan)
        active = self.store.load_active("/tmp/ws")
        assert active is not None
        assert active.goal == "Active"

    def test_load_active_not_found(self):
        active = self.store.load_active("/nonexistent")
        assert active is None

    def test_delete(self):
        plan = Plan(goal="Delete me", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        self.store.save(plan)
        assert self.store.delete(plan.id)
        assert self.store.load(plan.id) is None

    def test_list_all(self):
        plan = Plan(goal="List", workspace="/tmp")
        plan.tasks = [Task(id="t1", description="First")]
        self.store.save(plan)
        plans = self.store.list_all()
        assert len(plans) == 1
        assert plans[0]["goal"] == "List"

    def test_rotation(self):
        import wisp.planner as planner
        orig_max = planner._MAX_PLANS
        planner._MAX_PLANS = 2
        try:
            for i in range(3):
                p = Plan(goal=f"Plan {i}", workspace="/tmp")
                p.tasks = [Task(id="t1", description="First")]
                self.store.save(p)
            plans = self.store.list_all()
            assert len(plans) <= 2
        finally:
            planner._MAX_PLANS = orig_max


class TestParsePlanFromText:
    """Unit tests for parse_plan_from_text."""

    def test_parse_simple(self):
        text = """
1. [low] Setup project — files: config.py
2. [medium] Implement core — deps: 1 — files: core.py
3. [high] Add tests — deps: 1, 2 — files: test_core.py
"""
        plan = parse_plan_from_text(text, goal="Build app", workspace="/tmp")
        assert len(plan.tasks) == 3
        assert plan.tasks[0].estimated_complexity == "low"
        assert plan.tasks[0].files_to_touch == ["config.py"]
        assert plan.tasks[1].dependencies == ["task-1"]
        assert plan.tasks[2].dependencies == ["task-1", "task-2"]

    def test_parse_no_files(self):
        text = "1. [medium] Just a task"
        plan = parse_plan_from_text(text, goal="Simple", workspace="/tmp")
        assert len(plan.tasks) == 1
        assert plan.tasks[0].files_to_touch == []

    def test_parse_empty(self):
        plan = parse_plan_from_text("", goal="Empty", workspace="/tmp")
        assert len(plan.tasks) == 0

    def test_generate_plan_id(self):
        pid = _generate_plan_id()
        assert pid.startswith("plan-")
