"""Phase 2.1 seam — ProviderTransport protocol + owned session registry.

Pure seam module (no backend imports at module level) so providers can
depend on the interface without importing requests/httpx. Existing
factories (get_hardened_session/hardened_post) remain as thin facades;
new code should acquire sessions via SessionRegistry owned by
CompositionRoot, which closes them in shutdown().

Backward compatibility: purely additive. No existing behavior changed by
importing this module.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderTransport(Protocol):
    """Minimal HTTP surface providers need. Satisfied by requests.Session,
    httpx.Client, and test fakes — no concrete import required."""

    def post(self, url: str, **kwargs: Any) -> Any: ...
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def close(self) -> None: ...


class SessionRegistry:
    """Explicit ownership for pooled HTTP sessions (D4).

    - track(session): register a session this root owns. Idempotent:
      tracking the same object twice closes it once.
    - untrack(session): release without closing (ownership transfer).
    - close_all(): close each tracked session once, suppressing per-session
      errors so one broken pool cannot block the rest. Safe to call twice.
    """

    def __init__(self) -> None:
        self._sessions: list[Any] = []

    def track(self, session: Any) -> Any:
        if session is None:
            return session
        if not any(s is session for s in self._sessions):
            self._sessions.append(session)
        return session

    def untrack(self, session: Any) -> None:
        self._sessions = [s for s in self._sessions if s is not session]

    def __len__(self) -> int:
        return len(self._sessions)

    def close_all(self) -> None:
        sessions, self._sessions = list(self._sessions), []
        for session in sessions:
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception:
                continue
