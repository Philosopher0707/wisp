"""Rendered-output witnesses for ToolCallCard.

`tests/test_tui_widgets.py::TestToolCallCard` asserts attribute state only, so it
passes even when the widget never repaints. A card that stores the right result
but shows a blank one is still a broken card: the run looks like it did nothing.

`AssistantMessage` solves this with `reactive` + `watch_*`. `ToolCallCard` declares
`is_complete` as reactive but had no watcher, so `set_result()` after mount updated
Python attributes and left the rendered Statics stale.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from wisp.tui.widgets.chat.tool_call_card import ToolCallCard


def _rendered(card: ToolCallCard) -> str:
    """Concatenated text of the card's child Statics as actually rendered.

    Textual 8.x exposes the Static payload as `.content`; the old `.renderable`
    attribute no longer exists, so reading it would AttributeError rather than
    tell us anything about the widget.
    """
    return " ".join(str(w.content) for w in card.query(Static))


class _Host(App):
    def compose(self) -> ComposeResult:
        yield ToolCallCard("run_bash", {"command": "ls -la"})


@pytest.mark.asyncio
async def test_result_is_rendered_after_set_result():
    """set_result() must repaint the mounted widget, not just its attributes."""
    app = _Host()
    async with app.run_test() as pilot:
        card = app.query_one(ToolCallCard)
        card.set_result("5 passed", 1200)
        await pilot.pause()

        rendered = _rendered(card)
        assert "5 passed" in rendered, (
            "ToolCallCard stored the result but never displayed it; "
            f"rendered text was {rendered!r}"
        )


@pytest.mark.asyncio
async def test_duration_is_rendered_after_set_result():
    """Duration is shown to the user; it must survive the post-mount update.

    Asserts the formatted value the widget actually renders (`1.2s`), not the
    raw millisecond count, because formatting is the intended behaviour.
    """
    app = _Host()
    async with app.run_test() as pilot:
        card = app.query_one(ToolCallCard)
        card.set_result("ok", 1200)
        await pilot.pause()

        rendered = _rendered(card)
        assert "1.2s" in rendered, (
            f"duration missing from rendered output; was {rendered!r}"
        )


@pytest.mark.asyncio
async def test_duration_under_one_second_renders_as_ms():
    """Sub-second tools read as `340ms`, not `0.3s`."""
    app = _Host()
    async with app.run_test() as pilot:
        card = app.query_one(ToolCallCard)
        card.set_result("ok", 340)
        await pilot.pause()

        assert "340ms" in _rendered(card)


@pytest.mark.asyncio
async def test_tool_name_and_args_render_before_any_result():
    """The in-flight state is what the user sees while a tool is still running."""
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        card = app.query_one(ToolCallCard)

        rendered = _rendered(card)
        assert "run_bash" in rendered
        assert "ls -la" in rendered