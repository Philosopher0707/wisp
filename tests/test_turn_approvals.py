"""A child started by a turn asks the person who is running THAT turn: the desktop app's socket, the Textual TUI.

The CLI hands one orchestrator-wide approver to the orchestrator (`set_approval_handler`, `test_subagent_cli_approval.py`). That cannot work for the server: one
orchestrator serves every WebSocket connection, so a single global handler would send one person's child prompt to whoever connected last. The turn's own approver
is the right one, and the turn knows it: `turn_approver(host)` publishes it in a context variable for the length of the turn, the child's task inherits it, and the
runner reads it when it starts the child.

These tests drive the production wiring (real runtime, tool executor, `spawn`, orchestrator, runner, real `edit_file`; only the model is scripted), plus the real
`WebSocketTransport` and `TUIApprovalController`.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace


from wisp.multi_agent.approval_relay import current_turn_broker, turn_approver
import pytest


@pytest.fixture(autouse=True)
def _offer_the_tools_under_test(monkeypatch):
    """These tests drive tools outside the default basic profile (run_bash/read_file/edit_file/write_file), and a
    tool that is not offered is refused since 2026-10-14; offer every built-in."""
    monkeypatch.setenv("WISP_TOOL_PROFILE", "full")

BUG = "def total(xs):\n    return sum(xs) - 1\n"
FIXED = "def total(xs):\n    return sum(xs)\n"
SPAWN = {"function": {"name": "spawn", "arguments": {"task": "fix totals.py", "role": "coder", "tools": ["read_file", "edit_file"]}}}
EDIT = {"function": {"name": "edit_file", "arguments": {"path": "totals.py", "old_text": "sum(xs) - 1", "new_text": "sum(xs)"}}}


def _root(tmp_path, monkeypatch):
    """A real CompositionRoot whose model is scripted: the parent spawns a coder, the coder edits the file, the coder reports, the parent reports."""
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig
    from wisp.providers.factory import ProviderFactory
    from wisp.providers.mock import MockProvider
    import wisp.provider_catalog as pc

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "totals.py").write_text(BUG)
    monkeypatch.setattr(pc, "resolve_selection", lambda cfg: SimpleNamespace(status="ok", suggested=None, provider="mock", detail="", model="mock-model",
                                                                               alternatives=[]))
    provider = MockProvider(responses=["", "", "coder done", "all done"], tool_calls=[[SPAWN], [EDIT]])
    monkeypatch.setattr(ProviderFactory, "from_config", lambda self, cfg: provider)
    config = WispConfig().replace(workspace=str(ws), provider="mock", model="mock-model", permission_mode="auto_edit", auto_approve=False)
    return CompositionRoot(config), ws


async def _parent_yes(event):
    return True


async def _run_parent_turn(root, ws, host=None, parent=_parent_yes):
    session = await root.runtime.get_or_create_session("s1", "mock-model", str(ws))
    with turn_approver(host) if host is not None else contextlib.nullcontext():
        async for _ in root.runtime.run_turn(session, "fix totals.py with a coder", approval_handler=parent):
            pass


class TestTheTurnsApproverReachesItsChildren:
    def test_a_yes_from_the_turns_approver_lets_the_child_edit(self, tmp_path, monkeypatch):
        root, ws = _root(tmp_path, monkeypatch)
        asked: list[dict] = []

        async def host(event):
            asked.append(event)
            return True

        asyncio.run(_run_parent_turn(root, ws, host))
        assert (ws / "totals.py").read_text() == FIXED
        assert [e.get("name") for e in asked] == ["edit_file"]
        assert asked[0].get("subagent") == "spawn-coder"  # the person sees whose request it is

    def test_a_no_leaves_the_file_alone(self, tmp_path, monkeypatch):
        root, ws = _root(tmp_path, monkeypatch)

        async def host(event):
            return False

        asyncio.run(_run_parent_turn(root, ws, host))
        assert (ws / "totals.py").read_text() == BUG

    def test_without_one_the_child_is_refused_as_before(self, tmp_path, monkeypatch):
        root, ws = _root(tmp_path, monkeypatch)
        asyncio.run(_run_parent_turn(root, ws, None))
        assert (ws / "totals.py").read_text() == BUG

    def test_a_host_that_raises_is_a_no(self, tmp_path, monkeypatch):
        root, ws = _root(tmp_path, monkeypatch)

        async def host(event):
            raise RuntimeError("socket went away")

        asyncio.run(_run_parent_turn(root, ws, host))
        assert (ws / "totals.py").read_text() == BUG

    def test_the_orchestrators_own_handler_wins_when_both_exist(self, tmp_path, monkeypatch):
        """The CLI's wiring is unchanged by this: an explicit orchestrator-wide handler is the one asked."""
        root, ws = _root(tmp_path, monkeypatch)
        turn_asked, global_asked = [], []

        async def turn_host(event):
            turn_asked.append(event)
            return True

        async def global_host(event):
            global_asked.append(event)
            return True

        root.subagent_orchestrator.set_approval_handler(global_host)
        asyncio.run(_run_parent_turn(root, ws, turn_host))
        assert global_asked and not turn_asked


class TestTheApproverDoesNotLeak:
    def test_it_is_set_inside_the_turn_and_gone_after(self):
        async def go():
            assert current_turn_broker() is None
            with turn_approver(lambda event: True):
                assert current_turn_broker() is not None
            assert current_turn_broker() is None

        asyncio.run(go())

    def test_it_is_gone_after_the_turn_raises(self):
        async def go():
            with contextlib.suppress(RuntimeError):
                with turn_approver(lambda event: True):
                    raise RuntimeError("turn failed")
            assert current_turn_broker() is None

        asyncio.run(go())

    def test_a_task_started_before_it_does_not_see_it(self):
        async def go():
            seen = []

            async def early():
                await gate.wait()
                seen.append(current_turn_broker())

            gate = asyncio.Event()
            task = asyncio.create_task(early())
            with turn_approver(lambda event: True):
                gate.set()
                await task
            assert seen == [None]

        asyncio.run(go())

    def test_none_publishes_nothing(self):
        async def go():
            with turn_approver(None):
                assert current_turn_broker() is None

        asyncio.run(go())

    def test_two_turns_at_once_ask_their_own_hosts_only(self, tmp_path, monkeypatch):
        """The property that makes a shared orchestrator safe: a child asks the host of the turn that started it, never another connection's."""
        from wisp.multi_agent.task import SubagentContract

        root, _ = _root(tmp_path, monkeypatch)
        runner = root.subagent_orchestrator._runner
        asked: dict[str, list[str]] = {"a": [], "b": []}

        def host(label):
            async def ask(event):
                asked[label].append(event.get("subagent", ""))
                await asyncio.sleep(0.01)
                return True
            return ask

        async def one_turn(label):
            with turn_approver(host(label)):
                await asyncio.sleep(0.005)  # let the other turn begin: both are inside their `with` now
                handler = runner._approval_kwargs(SubagentContract(name=f"child-{label}", task="t"))["approval_handler"]
                return [await handler({"name": "edit_file", "arguments": {}}) for _ in range(3)]

        async def go():
            return await asyncio.gather(one_turn("a"), one_turn("b"))

        assert asyncio.run(go()) == [[True] * 3, [True] * 3]
        assert asked == {"a": ["child-a"] * 3, "b": ["child-b"] * 3}

    def test_a_background_child_never_asks_even_inside_a_turn(self, tmp_path, monkeypatch):
        from wisp.multi_agent.task import SubagentContract

        root, _ = _root(tmp_path, monkeypatch)
        runner = root.subagent_orchestrator._runner

        async def go():
            with turn_approver(lambda event: True):
                return runner._approval_kwargs(SubagentContract(name="bg", task="t", background=True))

        assert asyncio.run(go()) == {}

    def test_children_of_one_turn_are_asked_one_at_a_time(self, tmp_path, monkeypatch):
        from wisp.multi_agent.task import SubagentContract

        root, _ = _root(tmp_path, monkeypatch)
        runner = root.subagent_orchestrator._runner
        active = {"now": 0, "max": 0}

        async def host(event):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.02)
            active["now"] -= 1
            return True

        async def go():
            with turn_approver(host):
                handlers = [runner._approval_kwargs(SubagentContract(name=f"c{i}", task="t"))["approval_handler"] for i in range(3)]
                return await asyncio.gather(*(h({"name": "edit_file", "arguments": {}}) for h in handlers))

        assert asyncio.run(go()) == [True, True, True] and active["max"] == 1

    def test_a_child_is_bounded_in_how_often_it_may_ask(self, tmp_path, monkeypatch):
        from wisp.multi_agent.approval_relay import MAX_REQUESTS_PER_CHILD
        from wisp.multi_agent.task import SubagentContract

        root, _ = _root(tmp_path, monkeypatch)
        runner = root.subagent_orchestrator._runner
        calls = []

        async def host(event):
            calls.append(1)
            return True

        async def go():
            with turn_approver(host):
                handler = runner._approval_kwargs(SubagentContract(name="loud", task="t"))["approval_handler"]
                return [await handler({"name": "edit_file", "arguments": {}}) for _ in range(MAX_REQUESTS_PER_CHILD + 3)]

        results = asyncio.run(go())
        assert results[MAX_REQUESTS_PER_CHILD:] == [False] * 3 and len(calls) == MAX_REQUESTS_PER_CHILD


class TestOnlyTheTerminalGetsTheStderrLine:
    def test_a_turns_children_write_nothing_to_stderr(self, tmp_path, monkeypatch, capsys):
        """stderr is the server's log and the TUI's screen; the host's own prompt already says whose request it is."""
        root, ws = _root(tmp_path, monkeypatch)

        async def host(event):
            return True

        asyncio.run(_run_parent_turn(root, ws, host))
        assert (ws / "totals.py").read_text() == FIXED
        assert capsys.readouterr().err == ""

    def test_the_cli_wiring_still_says_it(self, capsys):
        from wisp.multi_agent.approval_relay import ApprovalBroker

        async def host(event):
            return True

        asyncio.run(ApprovalBroker(host).for_child("kid")({"name": "edit_file", "arguments": {}}))
        assert "subagent 'kid' is asking" in capsys.readouterr().err
