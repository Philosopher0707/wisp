"""Syntax-highlighted code preview panel."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class CodePreview(Widget):
    """Displays file contents with line numbers; updates on selection."""

    MAX_CHARS = 20_000

    def __init__(self, file_path: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.file_path = file_path

    def compose(self) -> ComposeResult:
        yield Static(self._render(self.file_path), classes="info-text",
                     id="preview-body")

    def show_file(self, file_path: str | None) -> None:
        self.file_path = file_path
        try:
            body = self.query_one("#preview-body", Static)
            body.update(self._render(file_path))
        except Exception:
            pass  # DOM not composed yet; compose() renders current path

    def _render(self, file_path: str | None) -> str:
        if not file_path:
            return "(no file selected)"
        try:
            text = Path(file_path).read_text(errors="replace")
        except Exception as exc:
            return f"(could not read file: {exc})"
        lines = text[: self.MAX_CHARS].splitlines()
        numbered = [f"{i + 1:>4} │ {ln}" for i, ln in enumerate(lines)]
        return "\n".join(numbered) or "(empty file)"
