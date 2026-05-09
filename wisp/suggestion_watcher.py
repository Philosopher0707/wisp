"""SuggestionWatcher — polls workspace for recently changed files and surfaces LSP diagnostics."""

from __future__ import annotations

import os
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FileSuggestion:
    path: str
    mtime: float
    diagnostic_count: int
    severities: dict[str, int] = field(default_factory=dict)  # error, warning, info, hint


class SuggestionWatcher:
    """Polls workspace for recently modified files with LSP diagnostics.

    Keeps a simple mtime-based record — does not use inotify/FSEvents.
    """

    def __init__(self, workspace: str):
        self.workspace = os.path.abspath(workspace)
        self._known_mtimes: dict[str, float] = {}
        self._ignore_patterns = {'.git', '__pycache__', 'node_modules', '.venv',
                                 '.wisp', 'venv', '.tox', '.mypy_cache', '.pytest_cache',
                                 'dist', 'build', '.next', '.nuxt', 'target'}

    def scan(self) -> list[str]:
        """Return list of file paths that have changed since last scan."""
        changed = []
        for root, dirs, files in os.walk(self.workspace, followlinks=False):
            dirs[:] = [d for d in dirs if d not in self._ignore_patterns and not d.startswith('.')]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    stat = os.stat(fpath)
                except OSError:
                    continue
                mtime = stat.st_mtime
                if self._known_mtimes.get(fpath) != mtime:
                    self._known_mtimes[fpath] = mtime
                    changed.append(fpath)
        return changed

    def get_suggestions(self, lsp_manager=None) -> list[FileSuggestion]:
        """Scan for changes and return files with diagnostic counts."""
        changed = self.scan()
        suggestions = []
        for fpath in changed:
            diag_count = 0
            severities: dict[str, int] = {}
            if lsp_manager is not None:
                try:
                    diags = lsp_manager.get_diagnostics(fpath)
                    diag_count = len(diags)
                    for d in diags:
                        sev = {1: "error", 2: "warning", 3: "info", 4: "hint"}.get(
                            d.get("severity", 0), "info"
                        )
                        severities[sev] = severities.get(sev, 0) + 1
                except Exception:
                    pass
            try:
                mtime = os.stat(fpath).st_mtime
            except OSError:
                mtime = 0
            suggestions.append(FileSuggestion(
                path=os.path.relpath(fpath, self.workspace),
                mtime=mtime,
                diagnostic_count=diag_count,
                severities=severities,
            ))
        suggestions.sort(key=lambda s: (-s.diagnostic_count, -s.mtime))
        return suggestions
