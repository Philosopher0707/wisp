"""Async LSP bridge: speculative diagnostics without disk mutation."""

from __future__ import annotations

from wisp.core.lsp.client import AsyncLSPClient, Diagnostic

__all__ = ["AsyncLSPClient", "Diagnostic"]
