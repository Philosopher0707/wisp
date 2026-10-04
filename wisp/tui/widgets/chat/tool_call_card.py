"""Card displaying a tool call with its arguments and result."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class ToolCallCard(Widget):
    """Displays one tool invocation with name, duration, and result."""

    tool_name = reactive("")
    is_complete = reactive(False)
    result_text = reactive("")
    duration_ms = reactive(0)

    def __init__(self, tool_name: str = "", args: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.tool_args = self._format_args(args or {})
        self.result_text = ""
        self.duration_ms = 0

    def compose(self) -> ComposeResult:
        yield Static(f"🔧 {self.tool_name}", classes="tool-name")
        yield Static(self.tool_args, classes="tool-duration")
        yield Static(self.result_text, classes="tool-result")

    def watch_tool_name(self, name: str) -> None:
        self._update(".tool-name", f"🔧 {name}")

    def watch_result_text(self, text: str) -> None:
        self._update(".tool-result", text)

    def watch_duration_ms(self, duration_ms: int) -> None:
        unit = f"{duration_ms}ms" if duration_ms < 1000 else f"{duration_ms / 1000:.1f}s"
        self._update(".tool-duration", f"{self.tool_args}  ({unit})")

    def _update(self, selector: str, text: str) -> None:
        """Push new text into an already-mounted child.

        `compose()` has already run by the time a tool result arrives, so
        assigning to the parent's attributes leaves the child Statics showing
        whatever they were constructed with. Textual's `Static.content` is a
        reactive that repaints on assignment.

        Reactives also fire from `__init__`, before any child exists, so a
        missing child is the normal "not mounted yet" case rather than an error.
        """
        if not self.is_mounted:
            return
        try:
            self.query_one(selector, Static).content = text
        except NoMatches:
            return

    def set_result(self, result_text: str, duration_ms: float = 0) -> None:
        self.result_text = str(result_text)
        self.duration_ms = int(duration_ms)
        self.is_complete = True

    @staticmethod
    def _format_args(args: dict) -> str:
        if not args:
            return ""
        parts = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 80:
                v_str = v_str[:77] + "..."
            parts.append(f"{k}={v_str}")
        return ", ".join(parts)
