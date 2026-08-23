"""Typed read-only view over the canonical session dict.

The canonical session IS a plain dict: ``runtime.get_or_create_session()``
returns one and ``AgentAdapter`` carries it directly (see ROADMAP.md D2).
This view exists so consumers read well-known keys through declared
attributes instead of stringly-typed ``.get()`` calls scattered across the
codebase. It holds a reference, not a copy — mutations belong to the owner
of the underlying dict, never to the view.
"""

from __future__ import annotations

from typing import Any


class SessionView:
    """Read-only, typed access to the session contract."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError(
                f"SessionView wraps the session dict, got {type(data).__name__}"
            )
        self._data = data

    @classmethod
    def coerce(cls, candidate: Any) -> SessionView | None:
        """Return a view for dict-like sessions, None otherwise.

        Accepts the Any-typed session slots found at adapter/command
        boundaries without forcing callers to isinstance-check first.
        """
        if isinstance(candidate, dict):
            return cls(candidate)
        return None

    @property
    def raw(self) -> dict[str, Any]:
        """The underlying dict — escape hatch for write paths."""
        return self._data

    @property
    def id(self) -> str:
        return str(self._data.get("id", ""))

    @property
    def title(self) -> str:
        return str(self._data.get("title", ""))

    @property
    def model(self) -> str:
        return str(self._data.get("model", ""))

    @property
    def workspace(self) -> str:
        return str(self._data.get("workspace", ""))

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Live message-list reference from the underlying dict."""
        messages = self._data.setdefault("messages", [])
        if not isinstance(messages, list):
            raise TypeError(
                f"session['messages'] must be a list, got {type(messages).__name__}"
            )
        return messages

    def display_title(self, fallback: str = "(untitled)") -> str:
        """Title as shown in UI, falling back when unset."""
        return self.title or fallback
