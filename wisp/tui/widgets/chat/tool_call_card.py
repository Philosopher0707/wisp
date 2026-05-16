"""Card displaying a tool call with its arguments and result."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class ToolCallCard(Widget):
    """Displays one tool invocation with name, duration, and result."""

    tool_name = reactive("")
    is_complete = reactive(False)

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
