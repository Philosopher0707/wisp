"""Agent grid showing all active sub-agents."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class AgentCard(Widget):
    """Single agent card in the grid."""

    STATUS_ICONS = {
        "running": "●",
        "idle": "○",
        "completed": "✓",
        "error": "✗",
    }

    def __init__(self, agent_name: str, role: str = "worker", status: str = "idle", task: str = "", **kwargs):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.agent_role = role
        self.agent_status = status
        self.agent_task = task

    def render(self) -> str:
        icon = self.STATUS_ICONS.get(self.agent_status, "?")
        lines = [
            f"{icon} {self.agent_name} ({self.agent_role})",
        ]
        if self.agent_task:
            lines.append(f"  {self.agent_task}")
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        yield Static(self.render())


class AgentGrid(Widget):
    """Grid layout of agent cards."""

    agents = reactive[list[dict]]([], recompose=True)

    def compose(self) -> ComposeResult:
        if not self.agents:
            yield Static("No active agents.", classes="info-text")
        else:
            for a in self.agents:
                yield AgentCard(
                    agent_name=a.get("name", "?"),
                    role=a.get("role", "worker"),
                    status=a.get("status", "idle"),
                    task=a.get("task", ""),
                )
