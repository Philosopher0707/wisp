"""A subagent started from the CLI can ask the person at the keyboard, instead of being refused for having no approver.

Before: a child's authority was its contract's `auto_approve` and nothing else. In `auto_edit` mode `edit_file`, `write_file` and `run_tests` need a yes, a child
had nobody to ask, so every one of them answered `NO_APPROVER` and graph workers, `/spawn` and `/swarm` children could only read. Observed with the real model:
"all attempts to fix it (via edit_file, write_file ...) were denied".

Now the CLI hands the orchestrator the transport's approver. These tests drive the production wiring (CompositionRoot -> orchestrator -> runner -> runtime -> core ->
ToolExecutor -> real `edit_file`), with only the model scripted.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from wisp.composition import CompositionRoot
from wisp.config import WispConfig
from wisp.multi_agent.task import SubagentContract
from wisp.providers.mock import MockProvider

BUG = "def total(xs):\n    return sum(xs) - 1\n"
FIXED = "def total(xs):\n    return sum(xs)\n"
EDIT = {"function": {"name": "edit_file", "arguments": {"path": "totals.py", "old_text": "sum(xs) - 1", "new_text": "sum(xs)"}}}


def _root(tmp_path, monkeypatch, rounds: int = 1):
    """A real CompositionRoot whose model is scripted: `rounds` edit calls, then a final answer."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "totals.py").write_text(BUG)
    provider = MockProvider(responses=[""] * rounds + ["done"], tool_calls=[[EDIT]] * rounds)
    import wisp.provider_catalog as pc
    monkeypatch.setattr(pc, "resolve_selection", lambda cfg: SimpleNamespace(status="ok", suggested=None, provider="mock", detail="", model="mock-model",
                                                                               alternatives=[]))
    from wisp.providers.factory import ProviderFactory
    monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: provider)
    config = WispConfig().replace(workspace=str(ws), provider="mock", model="mock-model", permission_mode="auto_edit", auto_approve=False)
    root = CompositionRoot(config)
    return root, ws


def _child(name: str = "fix") -> SubagentContract:
    return SubagentContract(name=name, task="fix totals.py", tools=["read_file", "edit_file"], max_iterations=6, timeout_seconds=60)


def _run(root, contract):
    return asyncio.run(root.subagent_orchestrator.run(contract))


def test_without_an_approver_the_child_is_refused_and_the_file_is_untouched(tmp_path, monkeypatch):
    root, ws = _root(tmp_path, monkeypatch)
    _run(root, _child())
    assert (ws / "totals.py").read_text() == BUG


def test_with_the_cli_approver_a_yes_lets_the_child_edit(tmp_path, monkeypatch):
    root, ws = _root(tmp_path, monkeypatch)
    asked: list[dict] = []

    async def approve(event):
        asked.append(event)
        return True

    root.subagent_orchestrator.set_approval_handler(approve)
    _run(root, _child())
    assert (ws / "totals.py").read_text() == FIXED
    assert [e.get("name") for e in asked] == ["edit_file"]
    assert asked[0].get("subagent") == "fix"  # the person sees which child is asking


def test_a_no_leaves_the_file_untouched(tmp_path, monkeypatch):
    root, ws = _root(tmp_path, monkeypatch)

    async def approve(event):
        return False

    root.subagent_orchestrator.set_approval_handler(approve)
    _run(root, _child())
    assert (ws / "totals.py").read_text() == BUG


def test_an_approver_that_raises_is_a_no_and_does_not_end_the_run(tmp_path, monkeypatch):
    root, ws = _root(tmp_path, monkeypatch)

    async def approve(event):
        raise RuntimeError("terminal went away")

    root.subagent_orchestrator.set_approval_handler(approve)
    result = _run(root, _child())
    assert (ws / "totals.py").read_text() == BUG
    assert result is not None


def test_a_synchronous_approver_works_too(tmp_path, monkeypatch):
    root, ws = _root(tmp_path, monkeypatch)
    root.subagent_orchestrator.set_approval_handler(lambda event: True)
    _run(root, _child())
    assert (ws / "totals.py").read_text() == FIXED


def test_removing_the_approver_restores_the_refusal(tmp_path, monkeypatch):
    root, ws = _root(tmp_path, monkeypatch)
    root.subagent_orchestrator.set_approval_handler(lambda event: True)
    root.subagent_orchestrator.set_approval_handler(None)
    _run(root, _child())
    assert (ws / "totals.py").read_text() == BUG


def test_a_background_child_never_prompts_the_person(tmp_path, monkeypatch):
    """A background agent runs while the REPL sits at its own prompt: asking then would fight the input line for the keyboard. It stays refused."""
    root, ws = _root(tmp_path, monkeypatch)
    asked: list[dict] = []

    async def approve(event):
        asked.append(event)
        return True

    root.subagent_orchestrator.set_approval_handler(approve)
    contract = _child()
    contract.background = True
    _run(root, contract)
    assert asked == [] and (ws / "totals.py").read_text() == BUG


def test_the_background_manager_runs_its_children_without_the_prompt(tmp_path, monkeypatch):
    """Through the real manager (`spawn_background` and `/agents`), not by setting the flag by hand."""
    root, ws = _root(tmp_path, monkeypatch)
    asked: list[dict] = []

    async def approve(event):
        asked.append(event)
        return True

    root.subagent_orchestrator.set_approval_handler(approve)

    async def go():
        launched = await root.background_agents.launch(_child("bg"), label="bg")
        return await root.background_agents.result(launched["agent_id"], wait_seconds=30)

    asyncio.run(go())
    assert asked == [] and (ws / "totals.py").read_text() == BUG


def test_a_child_cannot_ask_without_limit(tmp_path, monkeypatch):
    """Everything that loops has a bound: after the cap every further request is a no, without bothering the person."""
    from wisp.multi_agent.approval_relay import ApprovalBroker, MAX_REQUESTS_PER_CHILD
    calls: list[int] = []

    async def host(event):
        calls.append(1)
        return True

    relay = ApprovalBroker(host).for_child("loud")
    results = asyncio.run(_ask_many(relay, MAX_REQUESTS_PER_CHILD + 5))
    assert results[:MAX_REQUESTS_PER_CHILD] == [True] * MAX_REQUESTS_PER_CHILD
    assert results[MAX_REQUESTS_PER_CHILD:] == [False] * 5
    assert len(calls) == MAX_REQUESTS_PER_CHILD


async def _ask_many(relay, n):
    return [await relay({"name": "edit_file", "arguments": {"path": f"f{i}.py"}}) for i in range(n)]


def test_a_host_handler_that_raises_is_a_no_at_the_relay_itself():
    """The approval gate also catches a failing handler, so only a call at the relay shows that the relay fails closed on its own."""
    from wisp.multi_agent.approval_relay import ApprovalBroker

    async def host(event):
        raise RuntimeError("terminal went away")

    assert asyncio.run(ApprovalBroker(host).for_child("x")({"name": "edit_file", "arguments": {}})) is False


def test_an_answer_that_is_not_a_plain_yes_is_a_no():
    from wisp.multi_agent.approval_relay import ApprovalBroker

    async def host(event):
        return "yes"  # truthy, but not the boolean the gate defines

    assert asyncio.run(ApprovalBroker(host).for_child("x")({"name": "edit_file", "arguments": {}})) is False


def test_two_children_never_prompt_at_the_same_time():
    """The terminal has one prompt: concurrent children are asked one after the other."""
    from wisp.multi_agent.approval_relay import ApprovalBroker
    active = {"now": 0, "max": 0}

    async def host(event):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return True

    broker = ApprovalBroker(host)

    async def go():
        a, b, c = broker.for_child("a"), broker.for_child("b"), broker.for_child("c")
        return await asyncio.gather(*(r({"name": "edit_file", "arguments": {}}) for r in (a, b, c)))

    assert asyncio.run(go()) == [True, True, True]
    assert active["max"] == 1


def test_the_prompt_is_not_shared_across_event_loops():
    """The graph runs on its own event loop, the REPL on another: a lock made in one must not break the other."""
    from wisp.multi_agent.approval_relay import ApprovalBroker

    async def host(event):
        return True

    broker = ApprovalBroker(host)
    for _ in range(2):
        assert asyncio.run(broker.for_child("x")({"name": "edit_file", "arguments": {}})) is True


def test_a_cancel_from_the_person_is_passed_on_as_a_verdict_not_swallowed():
    """`ApprovalCancelled` is how the person says "stop": the executor records it as a denial, so the relay must not turn it into a plain exception."""
    from wisp.exceptions import ApprovalCancelled
    from wisp.multi_agent.approval_relay import ApprovalBroker

    async def host(event):
        raise ApprovalCancelled()

    with pytest.raises(ApprovalCancelled):
        asyncio.run(ApprovalBroker(host).for_child("x")({"name": "edit_file", "arguments": {}}))


def test_the_cli_transport_stops_the_spinner_its_approval_started_for_a_subagent():
    """Unit seam of the pty test `test_nothing_keeps_drawing_after_a_child_was_approved`."""
    from wisp.transport.cli import CLITransport

    class Spinner:
        stopped = 0

        def stop(self):
            Spinner.stopped += 1

    transport = CLITransport.__new__(CLITransport)
    transport._spinner = Spinner()

    async def approved(tool_call):
        return True

    transport.approve = approved  # type: ignore[method-assign]
    assert asyncio.run(transport.approve_for_subagent({"name": "edit_file"})) is True
    assert Spinner.stopped == 1

    async def cancelled(tool_call):
        from wisp.exceptions import ApprovalCancelled
        raise ApprovalCancelled("edit_file")

    transport.approve = cancelled  # type: ignore[method-assign]
    with pytest.raises(Exception):
        asyncio.run(transport.approve_for_subagent({"name": "edit_file"}))
    assert Spinner.stopped == 2  # also when the person cancels
