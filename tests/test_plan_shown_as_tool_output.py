"""F47 / ADR-0063: the plan the model wrote is shown back to it as tool output, never as a
system-prompt section.

`PHASE_F47_PLANSTORE.md` §2 measured the following:

* The plan was in no round's system prompt.
* `plan_task`'s creation-time listing stayed in the history.
* After `mark_step_done` the model received only *"Progress: 1/3"*, never the plan's state.
* The only system-prompt slot, `active_plan`, is tagged `OPERATOR`. Feeding it model-authored text
  would launder the tag T1's predicate trusts, from a cached prompt, across sessions.

What this file holds:

* **R1 (driven).** After `mark_step_done` / `update_plan`, the model's next round carries the
  plan's **current** state in a `role: "tool"` message: what is done, and which task is next. The
  same holds on the failure paths that loaded a plan.
* **R2 (driven and static).** The plan's text is in **no** round's system prompt. No production
  code feeds `ContextAssembler`'s plan slot or constructs a `PlanState`. The prompt-building modules
  never import `wisp.planner`.
* **Unchanged.** With no active plan, both tools return exactly what they returned before.

**The observation point is the production stack** (F96): `CompositionRoot → AgentRuntime.run_turn →
WispAgentCore → ToolExecutor → registry → wisp/tools/plan.py`. Only the LLM is scripted, and it
records what it is sent.

**Floors** (F81):

* The driven test asserts the plan exists in the store and that the expected rounds happened, so
  "the plan is not in the system prompt" cannot pass on a run that never planned.
* The static test asserts it found `ContextAssembler`'s own `PlanState(...)`, so it cannot pass by
  scanning nothing.

**Silent on a legitimate change** (F92): the tests assert what the model receives (the status
markers and the next task), not how the tool builds its string.
"""
from __future__ import annotations

import ast
import json
import pathlib
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

GOAL = "add a --json flag to the report command"
TASKS = ("1. [low] Read report.py and its tests — files: report.py\n"
         "2. [medium] Add the flag and the serializer — deps: 1 — files: report.py\n"
         "3. [low] Document the flag — deps: 2")


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    import wisp.planner as planner

    d = tmp_path / "plans"
    monkeypatch.setattr(planner, "PLANS_DIR", d)
    monkeypatch.setattr(planner, "_LOCK_PATH", d / "plans.lock")
    return d


def _recording_provider(tool_calls, responses):
    from wisp.providers.mock import MockProvider

    class Recording(MockProvider):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.seen: list[dict] = []

        def generate_stream_events(self, system_prompt, messages, tools=None, **kw):
            self.seen.append({"system": system_prompt,
                              "messages": json.loads(json.dumps(messages, default=str))})
            return super().generate_stream_events(system_prompt, messages, tools, **kw)

    return Recording(responses=responses, tool_calls=tool_calls)


async def _drive(tmp_path, monkeypatch, provider) -> str:
    import wisp.provider_catalog as pc
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig
    from wisp.providers.factory import ProviderFactory

    monkeypatch.setattr(pc, "resolve_selection", lambda cfg: SimpleNamespace(
        status="ok", suggested=None, provider="mock", detail="", model="mock-model",
        alternatives=[]))
    monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: provider)
    ws = tmp_path / "ws"
    ws.mkdir()
    root = CompositionRoot(WispConfig().replace(workspace=str(ws), provider="mock",
                                                model="mock-model", auto_approve=True))

    async def approve(_call):
        return True

    session = await root.runtime.get_or_create_session("f47", model="mock-model",
                                                       workspace=str(ws))
    [ev async for ev in root.runtime.run_turn(session, "add a --json flag",
                                              approval_handler=approve)]
    return str(ws)


def _last_tool_message(round_: dict) -> str:
    tools = [m for m in round_["messages"] if m.get("role") == "tool"]
    assert tools, "floor: the round must carry the tool result"
    return str(tools[-1].get("content", ""))


class TestTheModelSeesThePlansCurrentState:
    @pytest.mark.asyncio
    async def test_mark_step_done_shows_what_is_done_and_what_is_next(
            self, tmp_path, monkeypatch, plans_dir) -> None:
        provider = _recording_provider(
            tool_calls=[
                [{"function": {"name": "plan_task",
                               "arguments": {"goal": GOAL, "tasks": TASKS}}}],
                [{"function": {"name": "mark_step_done",
                               "arguments": {"task_id": "task-1", "notes": "read it"}}}],
            ],
            responses=["", "", "Step 1 done."])
        ws = await _drive(tmp_path, monkeypatch, provider)

        from wisp.planner import PlanStore
        assert PlanStore().load_active(ws) is not None, "floor: the plan must exist"
        assert len(provider.seen) == 3, "floor: plan, mark, answer"

        seen = _last_tool_message(provider.seen[2])
        assert "✓ 1. Read report.py and its tests" in seen
        assert "○ 2. Add the flag and the serializer" in seen
        assert "Next task: Add the flag and the serializer" in seen
        assert "1/3" in seen

    @pytest.mark.asyncio
    async def test_the_plan_is_in_no_system_prompt(self, tmp_path, monkeypatch,
                                                   plans_dir) -> None:
        provider = _recording_provider(
            tool_calls=[
                [{"function": {"name": "plan_task",
                               "arguments": {"goal": GOAL, "tasks": TASKS}}}],
                [{"function": {"name": "update_plan",
                               "arguments": {"task_id": "task-1", "status": "in_progress"}}}],
            ],
            responses=["", "", "Working."])
        ws = await _drive(tmp_path, monkeypatch, provider)

        from wisp.planner import PlanStore
        assert PlanStore().load_active(ws) is not None, "floor: the plan must exist"
        assert len(provider.seen) == 3
        for r in provider.seen:
            assert GOAL not in r["system"]
            assert "Read report.py and its tests" not in r["system"]
        assert "→ 1. Read report.py and its tests" in _last_tool_message(provider.seen[2])


def _plan(store_ws: str):
    from wisp.planner import PlanStore, parse_plan_from_text

    plan = parse_plan_from_text(TASKS, goal=GOAL, workspace=store_ws)
    PlanStore().save(plan)
    return plan


class TestEveryPathThatLoadedAPlanShowsIt:
    def test_update_plan_to_done(self, plans_dir) -> None:
        from wisp.tools.plan import tool_update_plan

        _plan("w")
        out = tool_update_plan("task-1", "done", workspace="w")
        assert out.startswith("✓ Updated task task-1 to 'done'.")
        assert "✓ 1." in out and "Next task: Add the flag" in out

    def test_mark_step_done_on_a_finished_task_shows_the_plan(self, plans_dir) -> None:
        from wisp.tools.plan import tool_mark_step_done

        _plan("w")
        tool_mark_step_done("task-1", workspace="w")
        out = tool_mark_step_done("task-1", workspace="w")
        assert out.startswith("⚠ Could not complete task task-1.")
        assert "✓ 1." in out and "Next task: Add the flag" in out

    def test_update_plan_on_an_unknown_task_shows_the_plan(self, plans_dir) -> None:
        from wisp.tools.plan import tool_update_plan

        _plan("w")
        out = tool_update_plan("task-9", "done", workspace="w")
        assert out.startswith("⚠ Task task-9 not found.")
        assert "○ 1. Read report.py" in out


class TestWithNoPlanNothingChanges:
    def test_both_tools_return_exactly_the_old_message(self, plans_dir) -> None:
        from wisp.tools.plan import tool_mark_step_done, tool_update_plan

        assert tool_mark_step_done("task-1", workspace="w") == "⚠ No active plan for this workspace."
        assert tool_update_plan("task-1", "done", workspace="w") == \
            "⚠ No active plan for this workspace."


_PROMPT_MODULES = ("wisp/context_assembler.py", "wisp/core/stateless.py")


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            yield node, (fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", ""))


class TestThePlanNeverEntersTheSystemPrompt:
    """R2, statically: the slot exists, and no production code fills it."""

    def test_no_production_code_feeds_the_plan_slot(self) -> None:
        offenders, own = [], 0
        for path in (REPO / "wisp").rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            for node, name in _calls(ast.parse(path.read_text(encoding="utf-8"))):
                if rel == "wisp/context_assembler.py":
                    own += name == "PlanState"
                    continue
                kws = {k.arg for k in node.keywords}
                if name == "PlanState" or kws & {"active_plan", "plan_context"} or (
                        name == "PromptContext" and "plan" in kws):
                    offenders.append(f"{rel}:{node.lineno}")
        assert own, "floor: the scan must find ContextAssembler's own PlanState(...)"
        assert offenders == [], (
            "ADR-0063 R2: the system prompt's plan slot is fed from production code — "
            "a model-authored plan in an OPERATOR-tagged section: " + ", ".join(offenders))

    def test_the_prompt_builders_do_not_import_the_store(self) -> None:
        for rel in _PROMPT_MODULES:
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
                a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            assert "wisp.planner" not in imported, f"ADR-0063 R2: {rel} imports wisp.planner"
