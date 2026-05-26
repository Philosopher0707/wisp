"""Trace and correlation IDs for observability.

UUID7 trace IDs (time-ordered, ms precision) for every turn.
Span IDs for per-event granularity. ContextVar propagation
so every log line and event carries the active trace context.
"""

from __future__ import annotations

import contextvars
import os
import time
import uuid


# ── UUID7 generator (RFC 9562) ──────────────────────────────────────

def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7.

    Layout (per RFC 9562):
        - 48-bit Unix timestamp in milliseconds (big-endian)
        - 4-bit version (0x7)
        - 12 bits random
        - 2-bit variant (10)
        - 62 bits random
    """
    now_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)

    # Timestamp: 48 bits (6 bytes, big-endian)
    ts_bytes = now_ms.to_bytes(6, "big")

    # Build 16 bytes:
    # bytes 0-5:  timestamp (48 bits)
    # bytes 6-7:  version (4 bits) + 12 bits random
    # bytes 8-9:  variant (2 bits) + 14 bits random
    # bytes 10-15: 48 bits random
    b = bytearray(16)
    b[0:6] = ts_bytes
    b[6:8] = rand_bytes[0:2]
    b[8:16] = rand_bytes[2:10]

    # Set version to 7
    b[6] = (b[6] & 0x0F) | 0x70
    # Set variant to 10xx
    b[8] = (b[8] & 0x3F) | 0x80

    return uuid.UUID(bytes=bytes(b))


# ── Context propagation ─────────────────────────────────────────────

_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "span_id", default=None
)
_current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "session_id", default=None
)


def new_trace(session_id: str | None = None) -> str:
    """Start a new trace and return the trace ID."""
    tid = str(uuid7())
    _current_trace_id.set(tid)
    if session_id:
        _current_session_id.set(session_id)
    return tid


def new_span() -> str:
    """Start a new span within the current trace. Returns span ID."""
    sid = str(uuid7())
    _current_span_id.set(sid)
    return sid


def current_trace_id() -> str | None:
    return _current_trace_id.get()


def current_span_id() -> str | None:
    return _current_span_id.get()


def current_session_id() -> str | None:
    return _current_session_id.get()


def set_session_id(sid: str) -> None:
    _current_session_id.set(sid)


class TraceContext:
    """Restore trace context on exit. Use as context manager::

        with TraceContext() as ctx:
            ...
        # ctx.trace_id, ctx.span_id still available after exit
    """

    def __init__(self, session_id: str | None = None):
        self.trace_id: str = ""
        self.span_id: str = ""
        self._prev_trace: str | None = None
        self._prev_span: str | None = None
        self._prev_session: str | None = None
        self._session_id = session_id

    def __enter__(self) -> TraceContext:
        self._prev_trace = _current_trace_id.get()
        self._prev_span = _current_span_id.get()
        self._prev_session = _current_session_id.get()
        self.trace_id = str(uuid7())
        _current_trace_id.set(self.trace_id)
        if self._session_id:
            _current_session_id.set(self._session_id)
        return self

    def __exit__(self, *args) -> None:
        _current_trace_id.set(self._prev_trace)
        _current_span_id.set(self._prev_span)
        _current_session_id.set(self._prev_session)

    def start_span(self) -> str:
        self.span_id = str(uuid7())
        _current_span_id.set(self.span_id)
        return self.span_id


# ── Logging filter ──────────────────────────────────────────────────

class TraceLogFilter:
    """Inject trace_id, span_id, session_id into all log records."""

    def filter(self, record):
        record.trace_id = current_trace_id() or ""
        record.span_id = current_span_id() or ""
        record.session_id = current_session_id() or ""
        return True
