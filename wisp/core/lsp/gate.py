"""Pre-test compiler gate (GH#28).

Slow test suites burn wall-clock on errors a compiler catches in a
second. This gate type-checks a mutation's *proposed* content through a
language server before the write lands, and blocks the write with a
compact error frame when the proposal introduces errors:

    [LSP COMPILER ERROR] pkg/core.py:4:10 - Type 'str' is not assignable...

Design rules (load-bearing):

- The gate depends on a ``diagnose`` callable, not on any LSP class —
  production passes ``AsyncLSPClient.diagnose_proposal`` over the real
  ``wisp.lsp`` JSON-RPC client, tests pass a fake. No parallel client.
- The gate blocks ONLY on positive error evidence (severity == error).
  Missing servers, unsupported extensions, timeouts, and server
  exceptions all fail OPEN (proceed) — the gate must never break the
  write path, only sharpen it.
- Reads are never gated; only write/edit ops with computable post-images.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Wall-clock ceiling for one gate check. The server round-trip is
# settle + request; beyond this the suite would have been faster —
# fail open and let the tests speak.
GATE_TIMEOUT_S = 15.0

# LSP severity: 1 == Error. Warnings/hints never block.
_ERROR_SEVERITY = 1


@dataclass(frozen=True)
class GateError:
    """One blocking diagnostic, display-normalized (1-based position)."""

    path: str
    line: int
    character: int
    message: str


@dataclass(frozen=True)
class GateVerdict:
    """Outcome of one gate check."""

    blocked: bool
    frame: str = ""
    errors: tuple[GateError, ...] = ()


def format_lsp_frame(path: str, line0: int, char0: int, message: str) -> str:
    """Format one diagnostic as a compact compiler-error frame."""
    first_line = (message or "").strip().splitlines()[0] if message else "(no message)"
    return f"[LSP COMPILER ERROR] {path}:{line0 + 1}:{char0} - {first_line}"


def proposed_content(
    op: str,
    current: str | None,
    *,
    content: str = "",
    old_text: str = "",
    new_text: str = "",
) -> str | None:
    """Compute the in-memory post-image of a mutation, or None to skip.

    - write -> the full new content.
    - edit  -> current text with old_text replaced once; None when the
      anchor is absent (the edit tool will report that itself).
    - read (or anything else) -> None: reads are never gated.
    """
    if op == "write":
        return content
    if op == "edit":
        if current is None or not old_text or old_text not in current:
            return None
        return current.replace(old_text, new_text, 1)
    return None


# Async (abs_path, proposed_text) -> raw diagnostic dicts, e.g.
# AsyncLSPClient.diagnose_proposal bound to one server.
DiagnoseFn = Callable[[str, str], Awaitable[list[Any]]]


class CompilerGate:
    """Block writes whose proposed content has compiler errors."""

    def __init__(
        self,
        diagnose: DiagnoseFn,
        timeout_s: float = GATE_TIMEOUT_S,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._diagnose = diagnose
        self._timeout_s = timeout_s

    async def check(self, display_path: str, abs_path: str, proposal: str | None) -> GateVerdict:
        """Check a proposal; fail open on any infrastructure problem."""
        if proposal is None:
            return GateVerdict(blocked=False)
        try:
            async with asyncio.timeout(self._timeout_s):
                raw = await self._diagnose(abs_path, proposal)
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("LSP gate timed out for %s — failing open", display_path)
            return GateVerdict(blocked=False)
        except Exception:
            logger.debug("LSP gate failed for %s — failing open", display_path, exc_info=True)
            return GateVerdict(blocked=False)
        errors: list[GateError] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            try:
                severity = int(item.get("severity", 3))
            except (TypeError, ValueError):
                continue
            if severity != _ERROR_SEVERITY:
                continue
            pos = item.get("range", {}).get("start", {}) if isinstance(item.get("range"), dict) else {}
            try:
                line0 = int(pos.get("line", 0))
                char0 = int(pos.get("character", 0))
            except (TypeError, ValueError):
                line0, char0 = 0, 0
            message = str(item.get("message", ""))
            errors.append(GateError(display_path, line0 + 1, char0, message.splitlines()[0] if message else ""))
        if not errors:
            return GateVerdict(blocked=False)
        frames = "\n".join(
            format_lsp_frame(e.path, e.line - 1, e.character, e.message) for e in errors[:5])
        if len(errors) > 5:
            frames += f"\n... and {len(errors) - 5} more errors"
        return GateVerdict(blocked=True, frame=frames, errors=tuple(errors))


def gate_from_manager(manager: Any, settle_s: float = 1.0) -> CompilerGate | None:
    """Build a CompilerGate over a real LSP manager, or None to skip.

    Returns None when the manager cannot serve the workspace (never
    partially wired). Server resolution is per-file at check time so
    unsupported extensions skip individually.
    """
    if manager is None:
        return None

    from wisp.core.lsp.client import AsyncLSPClient

    async def _diagnose(abs_path: str, proposal: str) -> list[Any]:
        server = manager.get_server_safe(abs_path)
        if server is None:
            return []
        client = AsyncLSPClient(server, settle_s=settle_s)
        diags = await client.diagnose_proposal(abs_path, proposal, settle_s=settle_s)
        return [
            {"range": {"start": {"line": d.line, "character": d.character}},
             "severity": d.severity, "message": d.message}
            for d in diags
        ]

    return CompilerGate(_diagnose)
