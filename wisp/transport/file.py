"""File transport — writes events to a log file.

Implements Transport ABC for persistent event logging.
Useful for:
  - Background run audit trails
  - CI/CD artifact generation
  - Session replay debugging
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class FileTransport(Transport):
    """Transport that appends events to a JSON Lines file.

    Each event is written as a single JSON line with a timestamp.
    No user interaction — auto-approves all tool calls.
    """

    def __init__(self, path: str | Path, mode: str = "a"):
        self.path = Path(path)
        self.mode = mode
        self._file: Any = None
        self._started = False

    def start(self) -> None:
        """Open the log file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, self.mode, encoding="utf-8")
        self._started = True
        # Write header
        self._file.write(json.dumps({
            "type": "file_transport_start",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
        self._file.flush()
        logger.debug("FileTransport started: %s", self.path)

    def stop(self) -> None:
        """Close the log file."""
        if self._file is not None:
            self._file.write(json.dumps({
                "type": "file_transport_stop",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            self._file.flush()
            self._file.close()
            self._file = None
        self._started = False
        logger.debug("FileTransport stopped: %s", self.path)

    async def send(self, event: dict) -> None:
        """Append event to the log file."""
        if self._file is None:
            return
        line = json.dumps({
            **event,
            "_logged_at": datetime.now(timezone.utc).isoformat(),
        }, default=str)
        self._file.write(line + "\n")
        self._file.flush()

    async def recv(self) -> str | None:
        """File transport does not receive user input."""
        return None

    async def approve(self, tool_call: dict) -> bool:
        """Auto-approve all tool calls in file logging mode."""
        return True

    def read_events(self) -> list[dict]:
        """Read all logged events from the file.

        Returns:
            List of event dicts (excluding transport control events).
        """
        if not self.path.exists():
            return []
        events = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("type") not in ("file_transport_start", "file_transport_stop"):
                        events.append(event)
                except json.JSONDecodeError:
                    continue
        return events
