"""Interactive file-system tree widget."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class FileTree(Widget):
    """Scrollable tree view of the workspace directory."""

    def __init__(self, root_path: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root_path).resolve() if root_path else Path.cwd()

    def compose(self) -> ComposeResult:
        yield Static(f"📁 {self.root.name}", classes="pane-title")
        yield Static(str(self.root), classes="info-text")
