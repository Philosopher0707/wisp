"""Syntax-highlighted code preview panel."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class CodePreview(Widget):
    """Displays file contents with line numbers."""

    def __init__(self, file_path: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.file_path = file_path

    def compose(self) -> ComposeResult:
        if self.file_path:
            try:
                text = Path(self.file_path).read_text()
            except Exception:
                text = "(could not read file)"
            yield Static(text[:5000], classes="info-text")
        else:
            yield Static("(no file selected)", classes="info-text")
