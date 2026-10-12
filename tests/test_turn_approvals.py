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

import pytest

from wisp.multi_agent.approval_relay import current_turn_broker, turn_approver

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


# ── the server: the real WebSocketTransport ──────────────────────────────────────────────────────────────────────────────────────────────────────────


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed = True


async def _serve_turn(root, ws_dir, answer, socket=None):
    """Run one prompt through the real WebSocketTransport. `answer(frame) -> bool | None` is the person at the other end of the socket: it is called for every
    `tool_approval_request` and its result is sent back the way the clients do (resolve by `call_id`). Returns the socket."""
    from wisp.transport.websocket import WebSocketTransport

    transport = WebSocketTransport(root.runtime)
    socket = socket or FakeSocket()
    await transport.handle(ws=socket, session_id="s1", model="mock-model", workspace=str(ws_dir))
    session = transport.session_for(socket)
    turn = asyncio.create_task(transport.stream_turn(socket, session, "fix totals.py with a coder"))
    answered = 0
    while not turn.done():
        frames = [f for f in socket.sent if f.get("type") == "tool_approval_request"]
        if len(frames) > answered:
            frame = frames[answered]
            answered += 1
            decision = answer(frame)
            if decision is not None:
                transport.resolve_approval(bool(decision), approval_id=frame["call_id"])
        await asyncio.sleep(0.01)
    await turn
    return socket, transport


class TestTheDesktopServerAsksForItsChildren:
    def test_a_child_edit_reaches_the_person_with_the_childs_name_and_a_yes_applies_it(self, tmp_path, monkeypatch):
        root, ws = _root(tmp_path, monkeypatch)

        socket, _ = asyncio.run(_serve_turn(root, ws, lambda frame: True))
        asked = [f for f in socket.sent if f.get("type") == "tool_approval_request"]
        child = [f for f in asked if f.get("name") == "edit_file"]
        assert len(child) == 1
        assert child[0]["subagent"] == "spawn-coder"
        assert "spawn-coder" in child[0]["reason"]  # clients that predate `subagent` still show whose request it is
        assert (ws / "totals.py").read_text() == FIXED

    def test_a_no_leaves_the_file_alone(self, tmp_path, monkeypatch):
        root, ws = _root(tmp_path, monkeypatch)
        asyncio.run(_serve_turn(root, ws, lambda frame: frame.get("name") != "edit_file"))
        assert (ws / "totals.py").read_text() == BUG

    def test_no_answer_is_a_no_and_the_turn_ends(self, tmp_path, monkeypatch):
        import wisp.transport.websocket as W

        monkeypatch.setattr(W, "_APPROVAL_TIMEOUT", 0.3)
        root, ws = _root(tmp_path, monkeypatch)
        asyncio.run(_serve_turn(root, ws, lambda frame: True if frame.get("name") == "spawn" else None))
        assert (ws / "totals.py").read_text() == BUG

    def test_a_disconnect_denies_a_pending_child_request(self, tmp_path, monkeypatch):
        from wisp.transport.websocket import WebSocketTransport

        root, ws = _root(tmp_path, monkeypatch)

        async def go():
            transport = WebSocketTransport(root.runtime)
            socket = FakeSocket()
            await transport.handle(ws=socket, session_id="s1", model="mock-model", workspace=str(ws))
            turn = asyncio.create_task(transport.stream_turn(socket, transport.session_for(socket), "fix totals.py with a coder"))
            seen = 0
            while not turn.done():
                frames = [f for f in socket.sent if f.get("type") == "tool_approval_request"]
                if len(frames) > seen:
                    frame = frames[seen]
                    seen += 1
                    if frame["name"] == "spawn":
                        transport.resolve_approval(True, approval_id=frame["call_id"])
                    else:
                        await transport.disconnect(socket)
                await asyncio.sleep(0.01)
            await turn

        asyncio.run(asyncio.wait_for(go(), 30))
        assert (ws / "totals.py").read_text() == BUG

    def test_a_childs_own_call_id_cannot_collide_with_the_parents(self, tmp_path, monkeypatch):
        """Approvals are keyed by session and call id. A child's tool-call id is whatever its model wrote, so the transport must not use it."""
        from wisp.transport.websocket import WebSocketTransport

        async def go():
            transport = WebSocketTransport(SimpleNamespace(approval_state=lambda sid: SimpleNamespace(
                session_policy=None, allowed_tools=set(), denied_tools=set())))
            socket = FakeSocket()
            transport._session_id, transport._current_ws = "s1", socket
            parent = asyncio.create_task(transport.approve({"name": "spawn", "id": "call_1", "arguments": {}}))
            await asyncio.sleep(0.01)
            child = asyncio.create_task(transport.approve_for_subagent({"name": "edit_file", "id": "call_1", "arguments": {}, "subagent": "kid"}))
            await asyncio.sleep(0.01)
            frames = [f for f in socket.sent if f["type"] == "tool_approval_request"]
            assert len(frames) == 2 and frames[0]["call_id"] != frames[1]["call_id"]
            transport.resolve_approval(False, approval_id=frames[1]["call_id"])
            transport.resolve_approval(True, approval_id=frames[0]["call_id"])
            return await parent, await child

        assert asyncio.run(go()) == (True, False)

    def test_a_late_answer_to_an_old_prompt_cannot_answer_a_new_one(self, monkeypatch):
        """Call ids are never reused. If they were, a client's late `yes` to a prompt that already timed out would answer the next child's different request."""
        import wisp.transport.websocket as W
        from wisp.transport.websocket import WebSocketTransport

        monkeypatch.setattr(W, "_APPROVAL_TIMEOUT", 0.05)

        async def go():
            transport = WebSocketTransport(SimpleNamespace(approval_state=lambda sid: SimpleNamespace(
                session_policy=None, allowed_tools=set(), denied_tools=set())))
            socket = FakeSocket()
            transport._session_id, transport._current_ws = "s1", socket
            assert await transport.approve_for_subagent({"name": "edit_file", "arguments": {"path": "a.py"}, "subagent": "kid"}) is False  # nobody answered: timed out
            monkeypatch.setattr(W, "_APPROVAL_TIMEOUT", 5.0)
            second = asyncio.create_task(transport.approve_for_subagent({"name": "edit_file", "arguments": {"path": "b.py"}, "subagent": "kid"}))
            await asyncio.sleep(0.02)
            first_id, second_id = (f["call_id"] for f in socket.sent if f["type"] == "tool_approval_request")
            assert first_id != second_id
            assert transport.resolve_approval(True, approval_id=first_id) is False  # nothing is waiting under the old id
            assert not second.done()
            transport.resolve_approval(False, approval_id=second_id)
            return await second

        assert asyncio.run(go()) is False

    def test_with_no_client_a_child_is_denied(self):
        from wisp.transport.websocket import WebSocketTransport

        transport = WebSocketTransport(SimpleNamespace())
        assert asyncio.run(transport.approve_for_subagent({"name": "edit_file", "arguments": {}, "subagent": "kid"})) is False

    def test_a_child_can_not_pass_through_a_reason_that_hides_who_it_is(self):
        """The text shown to the person is built by the transport from the child's NAME; a `reason` the child supplied does not replace it."""
        from wisp.transport.websocket import WebSocketTransport

        async def go():
            transport = WebSocketTransport(SimpleNamespace(approval_state=lambda sid: SimpleNamespace(
                session_policy=None, allowed_tools=set(), denied_tools=set())))
            socket = FakeSocket()
            transport._session_id, transport._current_ws = "s1", socket
            task = asyncio.create_task(transport.approve_for_subagent(
                {"name": "edit_file", "arguments": {}, "subagent": "kid", "reason": "routine maintenance from the user's own assistant"}))
            await asyncio.sleep(0.01)
            frame = socket.sent[0]
            transport.resolve_approval(False, approval_id=frame["call_id"])
            await task
            return frame

        frame = asyncio.run(go())
        assert frame["reason"].startswith("subagent 'kid'") and frame["subagent"] == "kid"


# ── the Textual TUI ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


class TestTheTuiAsksForItsChildren:
    def _controller(self):
        from wisp.transport.tui import ApprovalRequested, TUIApprovalController

        notes: list[ApprovalRequested] = []
        controller = TUIApprovalController(notify=notes.append)
        return controller, notes

    def test_the_prompt_names_the_child_and_a_key_answers_it(self):
        controller, notes = self._controller()

        async def go():
            task = asyncio.create_task(controller.approve_for_subagent({"name": "edit_file", "arguments": {"path": "a.py"}, "subagent": "kid"}))
            await asyncio.sleep(0.01)
            assert len(notes) == 1 and "kid" in notes[0].tool_name and "edit_file" in notes[0].tool_name
            controller.resolve("y")
            return await task

        assert asyncio.run(go()) is True

    def test_n_is_a_no(self):
        controller, _ = self._controller()

        async def go():
            task = asyncio.create_task(controller.approve_for_subagent({"name": "edit_file", "arguments": {}, "subagent": "kid"}))
            await asyncio.sleep(0.01)
            controller.resolve("n")
            return await task

        assert asyncio.run(go()) is False

    def test_remembering_a_tool_is_by_the_real_tool_name(self):
        """`Y` ("always this tool") must remember `edit_file`, not the label shown to the person."""
        controller, notes = self._controller()

        async def go():
            first = asyncio.create_task(controller.approve_for_subagent({"name": "edit_file", "arguments": {}, "subagent": "kid"}))
            await asyncio.sleep(0.01)
            controller.resolve("Y")
            assert await first is True
            return await controller.approve({"name": "edit_file", "arguments": {}})  # the parent's own call: remembered, no prompt

        assert asyncio.run(go()) is True
        assert len(notes) == 1

    def test_a_cancel_unwinds_as_it_does_for_the_parent(self):
        controller, _ = self._controller()

        async def go():
            task = asyncio.create_task(controller.approve_for_subagent({"name": "edit_file", "arguments": {}, "subagent": "kid"}))
            await asyncio.sleep(0.01)
            controller.resolve("c")
            return await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(go())


@pytest.mark.asyncio
class TestTheTuiScreenEndToEnd:
    """The Textual screen's local turn, driven by Textual's pilot: the real WorkspaceScreen, the real runtime and tool executor, a scripted model, and the keys the
    person presses. Before this, no prompt of any kind appeared in the local TUI (the screen's controller was built without its `notify`), so a turn that needed a
    yes waited forever."""

    async def _turn(self, tmp_path, monkeypatch, key_for):
        from wisp.tui.app import WispTUIApp
        from wisp.tui.screens.approval_modal import ApprovalModal

        root, ws = _root(tmp_path, monkeypatch)
        app = WispTUIApp(config=root.config, runtime=root.runtime)
        modals: list[str] = []
        async with app.run_test() as pilot:
            await pilot.pause()
            app.navigate("workspace")
            await pilot.pause()
            turn = asyncio.create_task(app.screen._run_local_turn("fix totals.py with a coder"))
            for _ in range(300):
                if turn.done():
                    break
                if isinstance(app.screen, ApprovalModal):
                    modals.append(app.screen.tool_name)
                    await pilot.press(key_for(app.screen.tool_name))
                await pilot.pause(0.05)
            finished = turn.done()
            if not finished:
                turn.cancel()
        return modals, finished, ws

    async def test_the_parent_and_then_the_child_are_asked_and_a_yes_applies_the_edit(self, tmp_path, monkeypatch):
        modals, finished, ws = await self._turn(tmp_path, monkeypatch, lambda tool: "y")
        assert finished
        assert modals == ["spawn", "edit_file (subagent 'spawn-coder')"]
        assert (ws / "totals.py").read_text() == FIXED

    async def test_a_no_to_the_child_leaves_the_file_alone(self, tmp_path, monkeypatch):
        modals, finished, ws = await self._turn(tmp_path, monkeypatch, lambda tool: "n" if "subagent" in tool else "y")
        assert finished and any("subagent" in m for m in modals)
        assert (ws / "totals.py").read_text() == BUG

    async def test_cancel_at_the_childs_prompt_ends_the_turn_and_changes_nothing(self, tmp_path, monkeypatch):
        modals, finished, ws = await self._turn(tmp_path, monkeypatch, lambda tool: "c" if "subagent" in tool else "y")
        assert finished
        assert (ws / "totals.py").read_text() == BUG


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


@pytest.mark.asyncio
class TestTheTuiAsAClientOfTheServer:
    """The TUI also runs as a client of the server (`ws_client`): the request arrives as a frame, and `reason` is where the server names the child."""

    async def _modal_for(self, reason):
        from wisp.config import WispConfig
        from wisp.tui.app import WispTUIApp
        from wisp.tui.screens.approval_modal import ApprovalModal

        app = WispTUIApp(config=WispConfig())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.navigate("workspace")
            await pilot.pause()
            await app.screen._on_approval_request("s1:subagent-1", "edit_file", {"path": "a.py"}, reason)
            await pilot.pause()
            assert isinstance(app.screen, ApprovalModal)
            return app.screen.tool_name

    async def test_a_childs_request_names_the_child(self):
        shown = await self._modal_for("subagent 'kid' asks to use edit_file")
        assert "kid" in shown and shown.startswith("edit_file")

    async def test_the_parents_request_is_shown_as_before(self):
        assert await self._modal_for("") == "edit_file"

    async def test_a_reason_that_only_mentions_a_subagent_is_not_taken_for_provenance(self):
        assert await self._modal_for("the subagent said this is fine") == "edit_file"


class TestAnAnswerWithAnUnknownIdNeverReachesAChild:
    """`resolve_approval`/`resolve_decision` fall back to "the single pending request" for a client that does not echo the id. That stays for a parent's request
    (old clients depend on it) and never applies to a child's: nobody has seen the child's prompt except through the id it carries."""

    def _transport(self):
        from wisp.transport.websocket import WebSocketTransport

        runtime = SimpleNamespace(approval_state=lambda sid: SimpleNamespace(session_policy=None, allowed_tools=set(), denied_tools=set()),
                                  apply_approval_decision=lambda sid, tool, key: key in ("y", "Y", "a"))
        transport = WebSocketTransport(runtime)
        socket = FakeSocket()
        transport._session_id, transport._current_ws = "s1", socket
        return transport, socket

    async def _pending(self, transport, socket, kind):
        if kind == "child":
            task = asyncio.create_task(transport.approve_for_subagent({"name": "edit_file", "arguments": {}, "subagent": "kid"}))
        else:
            task = asyncio.create_task(transport.approve({"name": "spawn", "id": "call_9", "arguments": {}}))
        await asyncio.sleep(0.01)
        return task, socket.sent[-1]["call_id"]

    def test_an_unknown_id_does_not_answer_a_pending_child(self):
        async def go():
            transport, socket = self._transport()
            task, _ = await self._pending(transport, socket, "child")
            assert transport.resolve_approval(True, approval_id="s1:call_stale") is False
            assert transport.resolve_decision("y", approval_id="s1:call_stale") is False
            assert not task.done()
            task.cancel()

        asyncio.run(go())

    def test_no_id_at_all_does_not_answer_a_pending_child(self):
        async def go():
            transport, socket = self._transport()
            task, _ = await self._pending(transport, socket, "child")
            assert transport.resolve_approval(True) is False
            assert transport.resolve_decision("y") is False
            assert not task.done()
            task.cancel()

        asyncio.run(go())

    def test_an_unknown_child_id_does_not_answer_a_pending_parent_either(self):
        async def go():
            transport, socket = self._transport()
            task, _ = await self._pending(transport, socket, "parent")
            assert transport.resolve_approval(True, approval_id="s1:subagent-77") is False
            assert transport.resolve_decision("y", approval_id="s1:subagent-77") is False
            assert not task.done()
            task.cancel()

        asyncio.run(go())

    def test_the_old_fallback_still_serves_a_parents_request(self):
        async def go():
            transport, socket = self._transport()
            task, _ = await self._pending(transport, socket, "parent")
            assert transport.resolve_approval(True, approval_id="s1:not-echoed") is True
            return await task

        assert asyncio.run(go()) is True

    def test_the_childs_own_id_answers_it_both_ways(self):
        async def go():
            transport, socket = self._transport()
            first, first_id = await self._pending(transport, socket, "child")
            assert transport.resolve_approval(True, approval_id=first_id) is True
            second, second_id = await self._pending(transport, socket, "child")
            assert transport.resolve_decision("y", approval_id=second_id) is True
            return await first, await second

        assert asyncio.run(go()) == (True, True)

    def test_a_child_is_asked_even_while_a_parent_prompt_is_pending_and_each_id_answers_its_own(self):
        async def go():
            transport, socket = self._transport()
            parent, parent_id = await self._pending(transport, socket, "parent")
            child, child_id = await self._pending(transport, socket, "child")
            assert transport.resolve_approval(False, approval_id=child_id) is True
            assert transport.resolve_approval(True, approval_id=parent_id) is True
            return await parent, await child

        assert asyncio.run(go()) == (True, False)
