"""Stream interceptor — isolates engine diagnostics from user stdout.

Goals from §1:
  * wisp.core.provider_stream retries / SSE reconnections
  * tool file-not-found warnings (wisp.tools.registry)
  * truncated JSON badges (… +35 more) → [✓ Read 4 files · 18 KB]

Design:
  * Dedicated sink .agent/runtime.log via WatchedFileHandler (5MB x3).
  * Installs a logging.Filter on handlers for wisp.core.provider_stream
    and wisp.tools.registry that reroutes WARNING+ to file and suppresses
    propagation to the Rich console.
  * Console stdout sees only structured events (tool_call/result, content,
    rich tables from synthesizer.py) — no interleaved "Provider stream…"
    lines.
  * Badges: `truncate_payload` replaces raw truncated payloads before
    `CLITransport._render_event` sees them.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path
from typing import Any, Optional

__all__ = ["install", "uninstall", "truncate_payload", "LOG_PATH", "BadgeFilter"]

LOG_PATH = Path(".agent/runtime.log")
# Match the ugly truncation the user saw: keys often numbered, covers both
# payload kinds: "… +35 more", "… +28 more", "... +23 more"
_TRUNC_RE = re.compile(r"\s*…\s*\+\d+\s*more\s*|\s*\.\.\.\s*\+\d+\s*more\s*")
# SSE / provider diagnostics that belong in file only
_PROVIDER_SILENCE = re.compile(
    r"(Provider stream|SSE|reconnect|empty_choice_chunks|usable=0|ssek? lines=|stream_stats)",
    re.I,
)
_TOOL_MISS_RE = re.compile(r"Tool .*read_file failed|File not found:", re.I)

# Dedicated logger that always goes to file
FILE_LOGGER_NAME = "agent.runtime"

_installed = False
_handlers: list[logging.Handler] = []
_console_patched = False
_orig_stderr_write = None


class BadgeFilter(logging.Filter):
    """Copies selected warnings to file sink and mutes their console propagation."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        msg = record.getMessage()
        # Route these verbatim to file; suppress console echo
        if _PROVIDER_SILENCE.search(msg) or _TOOL_MISS_RE.search(msg):
            # Ensure file logger sees it
            logging.getLogger(FILE_LOGGER_NAME).handle(record)
            return False  # suppress original handler propagation
        return True


def _ensure_file_handler() -> logging.Handler:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        h: logging.Handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    except Exception:
        h = logging.FileHandler(LOG_PATH, encoding="utf-8")
    h.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    h.setFormatter(fmt)
    return h


def install(level: int = logging.WARNING) -> Path:
    """Install interceptor. Idempotent. Returns log path."""
    global _installed, _handlers, _console_patched, _orig_stderr_write
    if _installed:
        return LOG_PATH

    file_h = _ensure_file_handler()
    file_logger = logging.getLogger(FILE_LOGGER_NAME)
    file_logger.setLevel(logging.DEBUG)
    file_logger.addHandler(file_h)
    file_logger.propagate = False
    _handlers.append(file_h)

    # Attach BadgeFilter to offending loggers' existing handlers, and add
    # file sink for anything that still emits.
    for name in ("wisp.core.provider_stream", "wisp.tools.registry", "wisp.multi_agent", "wisp.core.stateless"):
        lg = logging.getLogger(name)
        lg.addFilter(BadgeFilter())
        # Ensure warnings also land in file even when filtered
        lg.addHandler(file_h)
        # Keep propagation off to avoid duplicate console via root
        # (but only for these noisy loggers; root stays for user events)
        # We leave propagate True for now to preserve other handlers,
        # filter already mutes the noisy messages.
    # Capture root warnings that slipped through (e.g., direct warnings.warn)
    # via a dedicated handler that only handles already-filtered levels
    root = logging.getLogger()
    if level <= logging.WARNING:
        # Don't double-add if already present
        if file_h not in root.handlers:
            root.addHandler(file_h)

    _installed = True
    return LOG_PATH


def uninstall() -> None:
    global _installed, _handlers
    if not _installed:
        return
    for h in _handlers:
        try:
            logging.getLogger(FILE_LOGGER_NAME).removeHandler(h)
            logging.getLogger().removeHandler(h)
            for name in ("wisp.core.provider_stream", "wisp.tools.registry", "wisp.multi_agent", "wisp.core.stateless"):
                try:
                    logging.getLogger(name).removeHandler(h)
                except Exception:
                    pass
            h.close()
        except Exception:
            pass
    _handlers.clear()
    # Remove BadgeFilter instances
    for name in ("wisp.core.provider_stream", "wisp.tools.registry", "wisp.multi_agent", "wisp.core.stateless"):
        lg = logging.getLogger(name)
        for f in list(lg.filters):
            if isinstance(f, BadgeFilter):
                lg.removeFilter(f)
    _installed = False


def truncate_payload(payload: Any, max_chars: int = 4000) -> Any:
    """Replace ugly truncated JSON/text with concise execution badges.

    Used before CLITransport renders tool_result so primary display never
    shows “… +35 more”. Returns either original payload (if clean) or a
    badge string/dict.

    Examples:
      truncated JSON -> "[✓ Read 4 files · 18 KB]"
      truncated list -> "[✓ Listed 12 entries · truncated]"
    """
    try:
        if isinstance(payload, dict):
            # Already-structured tool result with data field — keep dict wrapper
            data = payload.get("data", None)
            if isinstance(data, str) and _TRUNC_RE.search(data):
                size_kb = len(data) // 1024
                files = data.count(".rs") + data.count(".py") + data.count(".ts")
                badge = "[✓ Read {} files · {} KB]".format(files, max(1, size_kb)) if files else "[✓ Output truncated · {} KB — see .agent/runtime.log]".format(max(1, size_kb))
                out = dict(payload)
                out["data"] = badge
                return out
            # Generic dict without 'data' key but truncated string inside (e.g. direct payload)
            # Only replace if the whole dict is essentially the truncated string is handled above
            return payload
        if isinstance(payload, str):
            if _TRUNC_RE.search(payload):
                size_kb = len(payload) // 1024
                files = payload.count(".rs") + payload.count(".py")
                if files:
                    return "[✓ Read {} files · {} KB]".format(files, max(1, size_kb))
                # Generic badge
                return "[✓ Output truncated · {} chars — see .agent/runtime.log]".format(len(payload))
            if len(payload) > max_chars and "… +".lower() in payload.lower():
                return payload[: max_chars // 2] + "\n[✓ Truncated — see .agent/runtime.log]"
        return payload
    except Exception:
        return payload


# Convenience: allow `python -m agent.logger` to demo
if __name__ == "__main__":
    p = install()
    logging.getLogger("wisp.core.provider_stream").warning("Provider stream closed without any content [sse_lines=2 …]")
    logging.getLogger("wisp.tools.registry").warning("Tool read_file failed: File not found: src/system/mod.rs")
    print(f"Logged to {p}, console should be clean")
