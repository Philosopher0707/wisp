"""Buffered token stream renderer — coherent Markdown via Rich Live.

Fixes the ``I`` / ``'ll use…`` split: raw provider tokens are single
characters or partial words. Painting each token immediately causes
line-break jitter and markdown flicker (a lone ``#`` renders as a
heading before `` Title`` arrives). This module buffers chunks until a
coherent boundary — word, punctuation or newline — and then updates a
single ``rich.live.Live`` region, so the terminal shows stable,
markdown-styled output.

Design
------
* :class:`StreamRenderer` owns one ``Live`` display and two buffers:
  ``_buffer`` (already painted) and ``_pending`` (held for coherence).
  ``feed()`` appends to pending; pending is flushed to buffer only when
  :meth:`_should_flush` is True (trailing space / newline / punctuation,
  fence completion, or length cap). ``flush()`` forces the final hold at
  turn end.
* Markdown awareness: partial lines that start a block construct
  (``# Heading``, ``- list``, ````` fence``) are held until their
  newline arrives, then rendered styled. Flowing prose paints as soon as
  a word boundary appears — zero perceived latency.
* Falls back to plain stdout when Rich is unavailable (CI) or when
  ``console`` is not a TTY.

Example
-------
>>> from agent.ui.stream_renderer import StreamRenderer
>>> with StreamRenderer() as r:
...     for token in ["I", "'ll", " use", " the", " tool", " —", " done."]:
...         r.feed(token)
...     r.flush()
"""

from __future__ import annotations

import re
import time
from typing import Final, Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text

    _RICH: Final[bool] = True
except Exception:  # pragma: no cover — CI without rich
    Console = Live = Markdown = Panel = Text = None  # type: ignore[assignment]
    _RICH = False

__all__ = ["StreamRenderer", "BufferedMarkdownRenderer", "should_flush_token"]

# ── Token-boundary heuristics ────────────────────────────────────────

# Characters that constitute a safe flush boundary. A pending buffer ending
# with any of these is coherent enough to paint — the next token cannot be
# a mid-word continuation that would cause a split like "I" / "'ll".
_FLUSH_TRAILING: Final[frozenset[str]] = frozenset(
    {
        " ",
        "\n",
        "\t",
        ".",
        ",",
        "!",
        "?",
        ";",
        ":",
        ")",
        "]",
        "}",
        '"',
        "'",
        "`",
        "*",
        "_",
        "-",
        "—",
        "–",
    }
)

# Inline markdown that should NOT cause a flush mid-word: e.g. "``" inside
# an unfinished fence should hold.
_FENCE_START: Final[str] = "```"

# Minimum and maximum hold sizes. Pending shorter than MIN_HOLD_CHARS is
# held unless it ends with a boundary; pending longer than MAX_HOLD_CHARS
# is forced to flush to bound latency.
MIN_HOLD_CHARS: Final[int] = 4
MAX_HOLD_CHARS: Final[int] = 80

# ── Markdown block detection (mirrors wisp.transport.renderer) ───────

_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^\s*#{1,6}\s+")
_BULLET_RE: Final[re.Pattern[str]] = re.compile(r"^\s*[-*]\s+")
_NUMBERED_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\d+\.\s+")
_HR_RE: Final[re.Pattern[str]] = re.compile(r"^\s*---+\s*$")


def _is_block_start(line: str) -> bool:
    """Return True when *line* starts a markdown block construct.

    Block constructs must be held until their terminating newline arrives
    so the renderer can style the whole line at once. Flowing paragraphs
    are not block starts and should paint immediately.
    """
    s = line.lstrip()
    if s.startswith(_FENCE_START):
        return True
    if _HEADING_RE.match(s):
        return True
    if _BULLET_RE.match(s):
        return True
    if _NUMBERED_RE.match(s):
        return True
    if _HR_RE.match(s):
        return True
    return False


def should_flush_token(pending: str) -> bool:
    """Pure predicate — should *pending* be flushed now?

    Flushing is safe when the buffer ends at a word boundary or has grown
    large enough that holding would add visible latency. This function is
    exposed for unit tests.
    """
    if not pending:
        return False
    # Markdown block-start without newline — hold until line completes so
    # "# " or "- " does not paint as a lone marker before its text arrives.
    if _is_block_start(pending) and "\n" not in pending:
        return False
    # Always flush complete lines — the line can be styled as a markdown block.
    if "\n" in pending:
        # If the last line is an incomplete block start, hold it.
        # E.g. pending = "text\n# Hea" — the heading is partial.
        last_nl = pending.rfind("\n")
        tail = pending[last_nl + 1 :]
        if tail and _is_block_start(tail) and not tail.endswith("\n"):
            # Hold the incomplete block-start tail until its newline arrives
            return False
        return True
    # Fence body: hold until closing fence arrives or newline
    if pending.count(_FENCE_START) % 2 == 1:
        # Inside an open fence — hold partial lines inside the fence
        # but flush if we have a complete fenced line (contains newline handled above)
        return False
    # Trailing boundary → safe to flush (word complete) — but not if the
    # whole pending is itself a block-start prefix like "# " which we already
    # handled above; otherwise "I " etc. should flush.
    if pending[-1] in _FLUSH_TRAILING:
        return True
    # Length caps — never hold more than MAX_HOLD_CHARS
    if len(pending) >= MAX_HOLD_CHARS:
        return True
    # Very short pending without boundary → hold to coalesce "I" + "'ll"
    if len(pending) < MIN_HOLD_CHARS:
        return False
    # Medium pending without boundary but contains a space → word boundary inside
    # e.g. pending = "Hello w" — safer to hold until space, but if it already
    # has a space, the last word is complete enough to show.
    if " " in pending and pending[-1].isalnum():
        # Hold — next chunk may complete the word ("w" + "orld")
        return False
    return False


# ── Core renderer ────────────────────────────────────────────────────


class StreamRenderer:
    """Buffered Live renderer for streaming LLM tokens.

    Buffers single-character / partial-word chunks until a coherent
    boundary, then updates a single ``rich.live.Live`` region with
    markdown-styled content. Prevents the ``I`` / ``'ll use…`` jitter
    and markdown flicker described in §1.2.

    Args:
        console: Rich Console to render into. When None, a default
            ``Console()`` is created. When Rich is unavailable the
            renderer degrades to plain ``print`` buffering.
        refresh_per_second: Live refresh rate. 12–15 is smooth without
            excessive CPU.
        transient: When True, the Live display is cleared on ``stop()``.
            False (default) leaves the final markdown on screen as
            scrollback history.
        width: Optional terminal width override (for tests). When None,
            the console's width is used.

    Lifecycle::

        renderer = StreamRenderer()
        renderer.start()
        for chunk in provider_stream:
            renderer.feed(chunk)
        renderer.flush()
        renderer.stop()

    Or as a context manager::

        with StreamRenderer() as r:
            for chunk in stream:
                r.feed(chunk)
            r.flush()
    """

    def __init__(
        self,
        console: Optional[Console] = None,  # type: ignore[type-arg]
        *,
        refresh_per_second: float = 14.0,
        transient: bool = False,
        width: Optional[int] = None,
        _force_plain: bool = False,
    ) -> None:
        self._console: Optional[Console] = console  # type: ignore[assignment]
        self._refresh_per_second: float = float(refresh_per_second)
        self._transient: bool = bool(transient)
        self._width: Optional[int] = width
        self._force_plain: bool = bool(_force_plain)

        self._live: Optional[Live] = None  # type: ignore[type-arg]
        self._buffer: str = ""
        self._pending: str = ""
        self._fence_open: bool = False
        self._fence_body: list[str] = []
        self._started: bool = False
        self._last_flush_ts: float = time.monotonic()

    # ── Properties ───────────────────────────────────────────────

    @property
    def buffer(self) -> str:
        """Full painted buffer plus pending (what the user would read)."""
        return self._buffer + self._pending

    @property
    def painted(self) -> str:
        """Only the already-painted buffer (excludes pending hold)."""
        return self._buffer

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Start the Live display. Idempotent."""
        if self._started:
            return
        self._started = True
        self._buffer = ""
        self._pending = ""
        self._fence_open = False
        self._fence_body = []

        if not _RICH or self._force_plain:
            return

        if self._console is None:
            try:
                self._console = Console()  # type: ignore[call-arg]
            except Exception:
                return

        # Non-TTY (piped) — do not use Live; fall back to incremental writes
        try:
            if hasattr(self._console, "is_terminal") and not self._console.is_terminal:  # type: ignore[union-attr]
                # Some Console versions expose is_terminal, others file.isatty
                pass
            elif hasattr(self._console, "file") and self._console.file is not None:  # type: ignore[union-attr]
                try:
                    if not self._console.file.isatty():  # type: ignore[union-attr]
                        return
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._live = Live(  # type: ignore[call-arg]
                "",
                console=self._console,
                refresh_per_second=self._refresh_per_second,
                transient=self._transient,
                auto_refresh=True,
            )
            self._live.__enter__()  # type: ignore[union-attr]
        except Exception:
            self._live = None

    def stop(self) -> None:
        """Stop Live and flush any pending hold. Idempotent."""
        if not self._started:
            return
        # Flush remaining pending so nothing is lost
        if self._pending:
            self._buffer += self._pending
            self._pending = ""
            self._render_buffer()
        if self._live is not None:
            try:
                self._live.__exit__(None, None, None)  # type: ignore[union-attr]
            except Exception:
                pass
            self._live = None
        self._started = False

    def __enter__(self) -> StreamRenderer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ── Feeding ──────────────────────────────────────────────────

    def feed(self, chunk: str) -> None:
        """Feed one provider chunk (token) into the buffer.

        The chunk is appended to pending and flushed to the display only
        when :func:`should_flush_token` deems it coherent. This prevents
        single-character line splits and markdown flicker.
        """
        if not isinstance(chunk, str):
            chunk = str(chunk)
        if not chunk:
            return

        # Lazy start if used without explicit start()/context manager
        if not self._started:
            self.start()

        self._pending += chunk

        # Track fence state for markdown-aware holding
        # We count fence markers in the combined buffer+pending
        combined = self._buffer + self._pending
        fence_count = combined.count(_FENCE_START)
        self._fence_open = (fence_count % 2 == 1)

        if should_flush_token(self._pending):
            self._flush_pending()
        elif self._fence_open and "\n" not in self._pending and len(self._pending) < MAX_HOLD_CHARS:
            # Inside a fence without newline — hold
            pass
        else:
            # Check time-based forced flush to bound latency (e.g., provider pause)
            # Pending held longer than 120ms should be shown even without boundary
            now = time.monotonic()
            if now - self._last_flush_ts > 0.12 and len(self._pending) >= 2:
                self._flush_pending()

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        self._buffer += self._pending
        self._pending = ""
        self._last_flush_ts = time.monotonic()
        self._render_buffer()

    def flush(self) -> None:
        """Force-flush pending and repaint. Call at turn end."""
        if self._pending:
            self._buffer += self._pending
            self._pending = ""
        self._render_buffer()

    def clear(self) -> None:
        """Clear all buffers and repaint empty display."""
        self._buffer = ""
        self._pending = ""
        self._fence_open = False
        self._fence_body = []
        self._render_buffer()

    # ── Rendering ────────────────────────────────────────────────

    def _render_buffer(self) -> None:
        """Push current buffer to Live (or plain fallback)."""
        if not _RICH or self._force_plain or self._live is None:
            # Plain fallback — no Live; caller will read buffer directly
            return

        text = self._buffer
        if not text.strip():
            try:
                self._live.update("")  # type: ignore[union-attr]
            except Exception:
                pass
            return

        # Try markdown rendering; fall back to plain text on failure
        try:
            # Markdown() handles headings, fences, lists, bold, inline code
            md = Markdown(text)  # type: ignore[call-arg]
            self._live.update(md)  # type: ignore[union-attr]
        except Exception:
            try:
                self._live.update(text)  # type: ignore[union-attr]
            except Exception:
                pass

    # ── Convenience ──────────────────────────────────────────────

    def render_static(self, text: str) -> str:
        """Render *text* to a string (no Live) — for tests / fallback.

        Returns the markdown-styled string as it would appear in the terminal,
        or plain text when Rich is unavailable.
        """
        if not _RICH or self._force_plain:
            return text
        try:
            console = self._console or Console(highlight=False, force_terminal=False, width=self._width or 80)  # type: ignore[call-arg]
            # Capture rendered markdown to string
            with console.capture() as cap:  # type: ignore[union-attr]
                console.print(Markdown(text))  # type: ignore[union-attr]
            return cap.get()  # type: ignore[union-attr]
        except Exception:
            return text


# Legacy alias for older import paths
BufferedMarkdownRenderer = StreamRenderer
