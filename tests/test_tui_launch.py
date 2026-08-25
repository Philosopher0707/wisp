"""Headless launch regression: `wisp tui` mounts and answers end to end.

Guards the two launch bugs this caught: cmd_tui bypassing run_mode
(no runtime => dead prompts), and un-awaited chat.mount swallowing
every token before routing.
"""

import asyncio

import pytest



class FakeRuntime:
    def __init__(self):
        self.session = {"id": "sess-smoke", "model": "stub", "workspace": "/tmp",
                        "messages": [], "title": "smoke"}
        self.turns = []

    async def get_or_create_session(self, session_id, model, workspace):
        return dict(self.session)

    async def run_turn(self, session, prompt, approval_handler=None):
        self.turns.append(prompt)
        yield {"type": "thinking", "data": {"text": "pondering"}}
        yield {"type": "content", "data": {"text": f"echo:{prompt}"}}
        yield {"type": "done", "data": {"turns": 1}}

    def inject_steering(self, sid, text):
        pass

    def drain_steering(self, sid):
        return []


@pytest.mark.asyncio
async def test_launch_mounts_and_renders():
    from wisp.config import WispConfig
    from wisp.tui.widgets.chat.assistant_message import AssistantMessage
    from wisp.tui.app import WispTUIApp

    runtime = FakeRuntime()
    base = WispConfig()
    app = WispTUIApp(config=base.replace(workspace="/tmp"), runtime=runtime)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.screen is app._splash, f"expected splash, got {app.screen}"

        # any key advances splash → picker
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is app._session_picker, (
            f"splash advance failed: {app.screen}")

        app.navigate("workspace")
        await pilot.pause()
        assert app.screen is app._workspace

        # type a prompt and submit
        inp = app._workspace.query_one("#prompt-input")
        inp.focus()
        await pilot.press(*"hello tui")
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()

        assert runtime.turns == ["hello tui"], f"turn never ran: {runtime.turns}"
        chat = app._workspace.query_one("#chat-pane")
        msgs = [c for c in chat.children if isinstance(c, AssistantMessage)]
        assert msgs, "no AssistantMessage mounted"
        blob = "".join(m.content_text for m in msgs)
        assert "echo:hello tui" in blob, (
            f"reply not rendered; content_text={msgs[-1].content_text!r}")

        # status bar back to idle after done
        status = app._workspace.query_one("#status-bar")
        assert status.is_streaming is False
        print("LAUNCH SMOKE: PASS — mount, navigate, prompt, echo rendered")


@pytest.mark.asyncio
async def test_launch_renders_visible_pixels():
    """The splash must actually paint, not just mount.

    test_launch_mounts_and_renders asserts widget identity and text state,
    which stayed green while a circular CSS rule (auto-width container with
    fr-width children) collapsed every region to 0x0 — the 'blank TUI'
    bug. This guards at the compositor level: non-whitespace cells exist.
    """
    from wisp.config import WispConfig
    from wisp.tui.app import WispTUIApp

    app = WispTUIApp(config=WispConfig(), transport=None, runtime=None)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        chunks: list[str] = []
        for strip in app.screen._compositor.render_strips():
            for seg in strip:
                try:
                    chunks.append(seg.text)
                except AttributeError:
                    pass
        joined = "".join(chunks)
        non_blank = sum(1 for ch in joined if not ch.isspace())
        assert non_blank > 10, (
            f"screen renders blank: {non_blank} non-blank cells in "
            f"{len(joined)}; sample={joined.strip()[:80]!r}"
        )
