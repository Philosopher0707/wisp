"""Blocking approval modal — the TUI twin of the CLI's approval prompt."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import ModalScreen
from textual.widgets import Static

_KEYS_HINT = "y approve · Y always · n deny · N always-deny · a all · d block · c cancel"


class ApprovalModal(ModalScreen[str]):
    """Dismisses with the pressed key: y/Y/n/N/a/d/c."""

    BINDINGS = [
        ("y", "answer('y')", "Approve"),
        ("Y", "answer('Y')", "Always allow"),
        ("n", "answer('n')", "Deny"),
        ("N", "answer('N')", "Always deny"),
        ("a", "answer('a')", "Allow all"),
        ("d", "answer('d')", "Block all"),
        ("c", "answer('c')", "Cancel turn"),
        ("escape", "answer('n')", "Deny"),
    ]

    def __init__(self, tool_name: str, args_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.args_text = args_text

    def compose(self) -> ComposeResult:
        with Middle():
            with Center(classes="approval-box"):
                yield Static(
                    f"⚠ Approve {self.tool_name}?\n"
                    f"{self.args_text}\n\n{_KEYS_HINT}",
                    classes="approval-text",
                )

    def action_answer(self, key: str) -> None:
        self.dismiss(key)
