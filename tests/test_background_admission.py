"""Admission symmetry: every entry point into the running set obeys the bound.

Context (see PHASE_BOUNDARY_FORENSIC.md / F9):

`BackgroundAgentManager.launch()` enforced `max_running` (via the durable
scheduler when present, else an in-memory head-count). `send()` — which
resumes a finished agent as a NEW run of the same id — spawned
`asyncio.create_task` unconditionally. So resuming N finished agents ran N
concurrently, past the configured bound. A bound enforced at one entry point
and assumed at another is not a bound.

These tests pin the rule: one `_admit()` consulted by both paths.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from wisp.multi_agent.background import BackgroundAgentManager, STATUS_RUNNING
from wisp.multi_agent.task import SubagentContract, SubagentResult

REPO = pathlib.Path(__file__).resolve().parent.parent


class FakeOrchestrator:
    def __init__(self, delay: float = 0.05, session_id: str = "sess-fake"):
        self.delay = delay
        self.session_id = session_id

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        await asyncio.sleep(self.delay)
        return SubagentResult(
            task_id=contract.name, success=True, output="done",
            files_changed=[], elapsed_seconds=self.delay, error=None,
            session_id=self.session_id,
        )


def _contract(task: str = "audit auth.py") -> SubagentContract:
    return SubagentContract(name="bg-test", role="coder", task=task)


# ── The rule itself ──────────────────────────────────────────────────

def test_admit_allows_below_the_bound():
    mgr = BackgroundAgentManager(FakeOrchestrator(), max_running=2)
    assert mgr._admit("bg-a") is None


def test_admit_refuses_at_the_bound():
    mgr = BackgroundAgentManager(FakeOrchestrator(), max_running=1)
    # Simulate one running entry.
    from wisp.multi_agent.background import BackgroundAgentEntry
    entry = BackgroundAgentEntry(id="bg-x", label="coder-1", contract=_contract())
    entry.status = STATUS_RUNNING
    mgr._entries["bg-x"] = entry

    reason = mgr._admit("bg-y")
    assert reason is not None and "limit reached" in reason


@pytest.mark.asyncio
async def test_launch_refuses_at_the_bound():
    mgr = BackgroundAgentManager(FakeOrchestrator(delay=5.0), max_running=1)
    first = await mgr.launch(_contract())
    assert first["ok"] is True

    second = await mgr.launch(_contract())
    assert second["ok"] is False
    assert "limit reached" in second["error"]

    mgr.cancel(first["agent_id"])


@pytest.mark.asyncio
async def test_send_refuses_at_the_bound():
    """The regression: send() used to spawn unconditionally."""
    orch = FakeOrchestrator(delay=0.01)
    mgr = BackgroundAgentManager(orch, max_running=1)

    # A finishes, so it becomes resumable.
    a = await mgr.launch(_contract(task="first"))
    await mgr.result(a["agent_id"], wait_seconds=3.0)
    assert mgr.get(a["agent_id"]).status != STATUS_RUNNING

    # B occupies the single running slot.
    orch.delay = 5.0
    b = await mgr.launch(_contract(task="second"))
    assert b["ok"] is True

    # Resuming A must now be refused, exactly as a fresh launch would be.
    sent = await mgr.send(a["agent_id"], "continue please")
    assert sent["ok"] is False, (
        "send() admitted a continuation while the running bound was full"
    )
    assert "limit reached" in sent["error"]

    mgr.cancel(b["agent_id"])


@pytest.mark.asyncio
async def test_send_allowed_when_a_slot_is_free():
    """Symmetry must not break the legitimate path."""
    orch = FakeOrchestrator(delay=0.01)
    mgr = BackgroundAgentManager(orch, max_running=2)

    a = await mgr.launch(_contract(task="first"))
    await mgr.result(a["agent_id"], wait_seconds=3.0)

    sent = await mgr.send(a["agent_id"], "continue please")
    assert sent["ok"] is True, sent

    await mgr.result(a["agent_id"], wait_seconds=3.0)


# ── Structural pin: a third entry point must consult the same rule ────

def test_both_entry_points_consult_admit():
    src = (REPO / "wisp/multi_agent/background.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    consulted: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in ("launch", "send"):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "_admit"):
                    consulted[node.name] = True

    assert consulted.get("launch") is True, "launch() must consult _admit()"
    assert consulted.get("send") is True, (
        "send() must consult _admit(); spawning without admission is how the "
        "running bound was exceeded"
    )


def test_admission_rule_is_defined_once():
    """The head-count/scheduler choice must not be duplicated per entry point."""
    src = (REPO / "wisp/multi_agent/background.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    definers = [n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and "admit" in n.name]
    assert definers == ["_admit"], (
        f"admission logic must live in exactly one method; found {definers}"
    )
