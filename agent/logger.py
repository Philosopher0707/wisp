"""Stream interceptor — isolates engine diagnostics from user stdout.

Goals from §1:
  * wisp.core.provider_stream retries / SSE reconnections
  * tool file-not-found warnings (wisp.tools.registry)
  * truncated JSON badges (… +35 more) → [✓ Read 4 files · 18 KB]

Design:
  * Dedicated sink .agent/runtime.log via RotatingFileHandler (5 MB × 3)
    with WatchedFileHandler fallback for logrotate-aware deployments.
  * Installs a logging.Filter on noisy loggers that reroutes WARNING+ to
    file and suppresses propagation to the Rich console.
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
from pathlib import Path
from typing import Any, Final

__all__ = ["install", "uninstall", "truncate_payload", "LOG_PATH", "BadgeFilter", "get_log_path"]

LOG_PATH: Final[Path] = Path(".agent/runtime.log")
_FILE_LOGGER_NAME: Final[str] = "agent.runtime"

# Match truncation sentinels the model/tool layer emits:
# "… +35 more", "… +28 more", "... +23 more" (unicode ellipsis vs three dots)
_TRUNC_RE: Final[re.Pattern[str]] = re.compile(
    r"\s*…\s*\+\d+\s*more\s*|\s*\.\.\.\s*\+\d+\s*more\s*"
)

# SSE / provider diagnostics that belong in file only — never on stdout.
_PROVIDER_SILENCE: Final[re.Pattern[str]] = re.compile(
    r"(Provider stream|SSE|reconnect|empty_choice_chunks|usable=0|ssek? lines=|stream_stats|chunk_stall|backoff)",
    re.I,
)

# Tool miss / 404 warnings — also file-only.
_TOOL_MISS_RE: Final[re.Pattern[str]] = re.compile(
    r"Tool .*read_file failed|File not found:|Path not found:|Cannot list|no matches for",
    re.I,
)

# Loggers whose WARNING+ output is considered engine chrome.
_NOISY_LOGGERS: Final[tuple[str, ...]] = (
    "wisp.core.provider_stream",
    "wisp.tools.registry",
    "wisp.tools.filesystem",
    "wisp.tools.bash",
    "wisp.multi_agent",
    "wisp.core.stateless",
    "wisp.infra.circuit_breaker",
    "wisp.composition",
)

_installed: bool = False
_handlers: list[logging.Handler] = []


class BadgeFilter(logging.Filter):
    """Copy selected warnings to file sink and mute console propagation.

    The filter inspects each LogRecord's rendered message. Messages matching
    provider-noise or tool-miss patterns are forwarded verbatim to the
    dedicated file logger (``agent.runtime``) and then suppressed (``False``)
    so the original handler/console never sees them. All other records pass
    through unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            msg: str = record.getMessage()
        except Exception:
            return True
        if _PROVIDER_SILENCE.search(msg) or _TOOL_MISS_RE.search(msg):
            try:
                logging.getLogger(_FILE_LOGGER_NAME).handle(record)
            except Exception:
                pass
            return False
        return True


def _ensure_file_handler(log_path: Path = LOG_PATH) -> logging.Handler:
    """Create (and ensure directory for) the rotating file handler."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Prefer size-bounded rotation; fall back to plain file if unavailable.
    try:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except Exception:
        try:
            handler = logging.handlers.WatchedFileHandler(str(log_path), encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    return handler


def get_log_path() -> Path:
    """Return the current runtime log path (honors monkey-patched LOG_PATH)."""
    # LOG_PATH is module-level Final but tests monkey-patch it; read dynamically.
    import agent.logger as _self  # type: ignore

    return Path(getattr(_self, "LOG_PATH", LOG_PATH))


def install(level: int = logging.WARNING) -> Path:
    """Install the interceptor. Idempotent. Returns the log path.

    Args:
        level: Minimum level for the root sink. WARNING (default) captures
            engine retries and tool 404s without flooding DEBUG.

    Returns:
        Path to the runtime log file.
    """
    global _installed, _handlers
    if _installed:
        return get_log_path()

    current_path = get_log_path()
    file_handler = _ensure_file_handler(current_path)

    file_logger = logging.getLogger(_FILE_LOGGER_NAME)
    file_logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers if install called after manual patching.
    if file_handler not in file_logger.handlers:
        file_logger.addHandler(file_handler)
    file_logger.propagate = False
    _handlers.append(file_handler)

    # Attach filter + file sink to every noisy logger.
    for name in _NOISY_LOGGERS:
        lg = logging.getLogger(name)
        # Avoid double-adding the same filter.
        if not any(isinstance(f, BadgeFilter) for f in lg.filters):
            lg.addFilter(BadgeFilter())
        if file_handler not in lg.handlers:
            lg.addHandler(file_handler)

    # Root catch-all for stray warnings (e.g., warnings.warn -> logging).
    root = logging.getLogger()
    if level <= logging.WARNING and file_handler not in root.handlers:
        root.addHandler(file_handler)

    _installed = True
    return current_path


def uninstall() -> None:
    """Remove the interceptor and close file handlers. Idempotent."""
    global _installed, _handlers
    if not _installed:
        return
    for handler in list(_handlers):
        try:
            logging.getLogger(_FILE_LOGGER_NAME).removeHandler(handler)
        except Exception:
            pass
        try:
            logging.getLogger().removeHandler(handler)
        except Exception:
            pass
        for name in _NOISY_LOGGERS:
            try:
                logging.getLogger(name).removeHandler(handler)
            except Exception:
                pass
        try:
            handler.close()
        except Exception:
            pass
    _handlers.clear()

    for name in _NOISY_LOGGERS:
        lg = logging.getLogger(name)
        for filt in list(lg.filters):
            if isinstance(filt, BadgeFilter):
                lg.removeFilter(filt)

    _installed = False


def truncate_payload(payload: Any, max_chars: int = 4000) -> Any:
    """Replace ugly truncated payloads with concise badges.

    Used before ``CLITransport._render_event`` so the primary display never
    shows ``… +35 more``. Returns the original payload when clean, or a
    badge string/dict when truncated.

    Examples:
        >>> truncate_payload("x" * 100 + " … +35 more")
        '[✓ Output truncated · 112 chars — see .agent/runtime.log]'
        >>> truncate_payload({"data": "y" * 5000 + " … +28 more"})
        {'data': '[✓ Output truncated · 5013 chars — see .agent/runtime.log]'}

    Args:
        payload: Tool result payload (str, dict with ``data``, or any).
        max_chars: Hard ceiling; payloads longer than this with truncation
            markers are also badged.

    Returns:
        Badged payload of same shape when truncated, otherwise original.
    """
    try:
        if isinstance(payload, dict):
            data = payload.get("data", None)
            if isinstance(data, str) and _TRUNC_RE.search(data):
                size_kb = max(1, len(data) // 1024)
                files = data.count(".rs") + data.count(".py") + data.count(".ts") + data.count(".go")
                if files:
                    badge = f"[✓ Read {files} files · {size_kb} KB]"
                else:
                    badge = f"[✓ Output truncated · {size_kb} KB — see .agent/runtime.log]"
                out: dict[str, Any] = dict(payload)
                out["data"] = badge
                return out
            return payload

        if isinstance(payload, str):
            if _TRUNC_RE.search(payload):
                size_kb = len(payload) // 1024
                files = payload.count(".rs") + payload.count(".py")
                if files:
                    return f"[✓ Read {files} files · {max(1, size_kb)} KB]"
                return f"[✓ Output truncated · {len(payload)} chars — see .agent/runtime.log]"
            lowered = payload.lower()
            if len(payload) > max_chars and ("… +" in lowered or "... +" in lowered):
                return payload[: max_chars // 2] + "\n[✓ Truncated — see .agent/runtime.log]"
        return payload
    except Exception:
        return payload


if __name__ == "__main__":
    p = install()
    logging.getLogger("wisp.core.provider_stream").warning(
        "Provider stream closed without any content [sse_lines=2 usable=0 empty_choice_chunks=1 finish=stop] (attempt 1/3) — retrying"
    )
    logging.getLogger("wisp.tools.registry").warning("Tool read_file failed: File not found: src/system/mod.rs")
    print(f"Logged to {p}, console should be clean")
