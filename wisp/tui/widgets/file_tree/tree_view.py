"""Interactive file-system tree widget backed by Textual's DirectoryTree."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DirectoryTree


class WispDirectoryTree(DirectoryTree):
    """DirectoryTree that hides dotfiles and pyc noise."""

    def filter_paths(self, paths):
        return [
            p for p in paths
            if not p.name.startswith((".", "__pycache__"))
        ]


class FileTree(Widget):
    """Scrollable tree view of the workspace directory."""

    def __init__(self, root_path: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.root = Path(root_path).resolve() if root_path else Path.cwd()

    def compose(self) -> ComposeResult:
        yield WispDirectoryTree(str(self.root), id="workspace-tree")