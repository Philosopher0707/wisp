"""Async JSON-RPC bridge over the synchronous language-server client.

The event loop must never block on a language server: every blocking call
is dispatched with ``asyncio.to_thread``. The headline API is speculative
diagnostics — :meth:`AsyncLSPClient.diagnose_proposal` pushes proposed
content via ``textDocument/didChange``, waits for the server's
``publishDiagnostics`` round-trip, reads the diagnostics, then reverts the
open document to disk content in a ``finally``. Disk is never mutated, so
type and compilation errors feed straight back into the agent loop before
any write happens.

Only public methods of ``wisp.lsp.client.LSPServer`` are used.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SETTLE_S = 1.0
DEFAULT_TIMEOUT_S = 30


@dataclass(frozen=True)
class Diagnostic:
    """One published diagnostic, normalized from the raw LSP payload."""

    path: str
    line: int
    character: int
    severity: int
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == 1


def _normalize_diagnostics(path: str, raw: Any) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            position = item.get("range", {}).get("start", {})
            found.append(Diagnostic(
                path=path,
                line=int(position.get("line", 0)),
                character=int(position.get("character", 0)),
                severity=int(item.get("severity", 3)),
                message=str(item.get("message", "")),
            ))
        except (TypeError, ValueError):
            continue
    return found


class AsyncLSPClient:
    """Thin async facade over a started :class:`LSPServer` instance."""

    def __init__(self, server: Any, settle_s: float = DEFAULT_SETTLE_S) -> None:
        if settle_s <= 0:
            raise ValueError("settle_s must be positive")
        self._server = server
        self._settle_s = settle_s

    async def _call(self, method: str, *args: Any) -> Any:
        func = getattr(self._server, method)
        return await asyncio.to_thread(func, *args)

    async def open(self, file_path: str) -> None:
        await self._call("ensure_document_open", file_path)

    async def diagnostics(self, file_path: str) -> list[Diagnostic]:
        raw = await self._call("get_diagnostics", file_path)
        return _normalize_diagnostics(file_path, raw)

    async def symbols(self, file_path: str) -> list[dict[str, Any]]:
        raw = await self._call("get_symbols", file_path)
        return raw if isinstance(raw, list) else []

    async def diagnose_proposal(self, file_path: str, new_content: str,
                               settle_s: float | None = None) -> list[Diagnostic]:
        """Type-check proposed content without touching disk.

        Pushes ``new_content`` as an in-memory ``didChange``, waits one
        server round-trip, collects diagnostics, then reverts the document
        to the on-disk text — always, via ``finally``. Returns the
        diagnostics observed for the proposal (empty means clean).
        """
        wait = self._settle_s if settle_s is None else settle_s
        await self._call("ensure_document_open", file_path)
        await self._call("notify_text_change", file_path, new_content)
        try:
            await asyncio.sleep(wait)
            return await self.diagnostics(file_path)
        finally:
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    disk_text = handle.read()
            except OSError:
                disk_text = ""
            try:
                await self._call("notify_text_change", file_path, disk_text)
            except Exception:
                logger.debug("diagnostic revert failed for %s", file_path, exc_info=True)

    async def shutdown(self) -> None:
        await self._call("shutdown")
