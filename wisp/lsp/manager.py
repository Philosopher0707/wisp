"""LSPManager — manages multiple LSP servers, mapping file extensions to LSPServer instances.

Security / cleanup note:
  LSP servers spawn child OS processes (pylsp, rust-analyzer, etc.).  A
  single agent session re-uses the same manager; the module provides a
  ``get_lsp_manager`` singleton so server endpoints do NOT create a fresh
  manager per request (which would orphan child processes).

  ``shutdown_global_lsp_manager()`` must be called on application exit or
  wrapped in FastAPI lifespan.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import weakref
from pathlib import Path
from typing import Optional

from wisp.lsp.client import (
    LSPServer,
    LSPServerConfig,
    LSPServerError,
    _BUILTIN_LSP_CONFIGS,
)

logger = logging.getLogger(__name__)


# ── Module-level singleton ──────────────────────────────────────────

_GLOBAL_LSP: LSPManager | None = None
_GLOBAL_LSP_LOCK = threading.Lock()


def get_lsp_manager(workspace: str) -> "LSPManager":
    """Return the module-level singleton LSPManager, creating it if needed.

    Args:
        workspace: Absolute or relative path to the workspace root.
            If the singleton already exists for a *different* workspace,
            it is shut down and recreated for the new one.

    Returns:
        The shared LSPManager instance.
    """
    global _GLOBAL_LSP
    with _GLOBAL_LSP_LOCK:
        if _GLOBAL_LSP is None or _GLOBAL_LSP.workspace != workspace:
            if _GLOBAL_LSP is not None:
                logger.debug(
                    "LSPManager workspace changed (%s -> %s) — shutting down old.",
                    _GLOBAL_LSP.workspace,
                    workspace,
                )
                try:
                    _GLOBAL_LSP.shutdown_all()
                except Exception:
                    pass
            _GLOBAL_LSP = LSPManager(workspace)
            _GLOBAL_LSP.initialize()
            logger.info("LSPManager singleton created for workspace: %s", workspace)
        return _GLOBAL_LSP


def shutdown_global_lsp_manager() -> None:
    """Shut down the module-level singleton LSPManager.

    Safe to call multiple times (idempotent).  Intended for FastAPI
    lifespan teardown, agent ``close()``, and ``atexit`` handlers.
    """
    global _GLOBAL_LSP
    with _GLOBAL_LSP_LOCK:
        if _GLOBAL_LSP is not None:
            try:
                _GLOBAL_LSP.shutdown_all()
                logger.info("Global LSPManager shut down.")
            except Exception as e:
                logger.warning("Error shutting down global LSPManager: %s", e)
            finally:
                _GLOBAL_LSP = None


# ── Safety: ensure child processes are killed on interpreter exit ──

atexit.register(shutdown_global_lsp_manager)


def _cleanup_servers(servers: dict[str, LSPServer]) -> None:
    """Best-effort shutdown of leftover server processes.

    Called by ``weakref.finalize`` if an LSPManager is garbage-collected
    without explicit ``shutdown_all()``.
    """
    if not servers:
        return
    logger.debug("LSPManager finalizer — cleaning up %d server(s)", len(servers))
    for lid, srv in list(servers.items()):
        try:
            srv.shutdown()
        except Exception:
            pass


# ── LSPManager class ──────────────────────────────────────────────


class LSPManager:
    """Orchestrates multiple LSP servers, one per language.

    Servers are started lazily on first use and live for the agent session.
    Call ``shutdown_all()`` when done, or use as a context manager.
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self._servers: dict[str, LSPServer] = {}  # language_id -> LSPServer
        self._configs: list[LSPServerConfig] = []
        self._initialized = False
        # If this manager is GC'd without explicit shutdown, clean up child
        # processes.  The dict snapshot is captured at creation time so the
        # finalizer never holds a strong ref back to ``self``.
        self._finalizer = weakref.finalize(
            self,
            _cleanup_servers,
            {},
        )
        self._finalizer.atexit = False  # already covered by module-level atexit

    def __enter__(self) -> "LSPManager":
        """Context manager entry — initialize on enter."""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit — always shut down servers."""
        self.shutdown_all()

    def initialize(self) -> None:
        """Load configs. Does NOT start any server processes."""
        self._configs = _load_lsp_configs(self.workspace)
        self._initialized = True
        logger.debug("LSP manager initialized with %d configs", len(self._configs))

    def _get_server(self, file_path: str) -> LSPServer:
        """Get or lazily create the LSPServer for the given file's extension."""
        if not self._initialized:
            self.initialize()

        ext = Path(file_path).suffix.lower()
        config = None
        for cfg in self._configs:
            if ext in cfg.extensions:
                config = cfg
                break

        if config is None:
            supported = []
            for cfg in self._configs:
                supported.extend(cfg.extensions)
            raise LSPServerError(
                f"No LSP server configured for {ext} files. "
                f"Supported extensions: {', '.join(sorted(supported)) if supported else 'none configured'}"
            )

        lid = config.language_id
        if lid not in self._servers:
            server = LSPServer(config, self.workspace)
            try:
                server.start()
            except LSPServerError:
                raise
            except Exception as e:
                raise LSPServerError(f"Failed to start LSP server {config.command}: {e}")
            self._servers[lid] = server
            # Update finalizer snapshot so GC cleanup sees the new server
            self._finalizer.args = (dict(self._servers),)

        return self._servers[lid]

    def get_server_safe(self, file_path: str) -> LSPServer | None:
        """Get server without raising — returns None if no server available."""
        try:
            return self._get_server(file_path)
        except LSPServerError as e:
            logger.debug("LSP server not available for %s: %s", file_path, e)
            return None
        except Exception as e:
            logger.debug("Unexpected LSP error for %s: %s", file_path, e)
            return None

    def get_diagnostics(self, file_path: str) -> list[dict]:
        """Return cached diagnostics for a file from its language server."""
        server = self.get_server_safe(file_path)
        if server:
            return server.get_diagnostics(file_path)
        return []

    def notify_change(self, file_path: str) -> None:
        """Notify the relevant LSP server of a file edit."""
        server = self.get_server_safe(file_path)
        if server:
            server.notify_change(file_path)

    def shutdown_all(self) -> None:
        """Shut down all running LSP servers."""
        # Prevent finalizer from double-cleaning after an explicit shutdown.
        if self._finalizer is not None:
            self._finalizer.detach()

        for lid, server in list(self._servers.items()):
            try:
                server.shutdown()
            except Exception as e:
                logger.debug("Error shutting down LSP server %s: %s", lid, e)
        self._servers.clear()
        self._initialized = False


def _load_lsp_configs(workspace: str) -> list[LSPServerConfig]:
    """Merge built-in LSP configs with {workspace}/.wisp/lsp.json overrides."""
    configs: dict[str, LSPServerConfig] = {}
    for cfg in _BUILTIN_LSP_CONFIGS:
        configs[cfg.language_id] = LSPServerConfig(
            language_id=cfg.language_id,
            extensions=list(cfg.extensions),
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env),
            disabled=cfg.disabled,
        )

    config_path = os.path.join(workspace, ".wisp", "lsp.json")
    if not os.path.isfile(config_path):
        return [c for c in configs.values() if not c.disabled]

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON in %s: %s", config_path, e)
        return [c for c in configs.values() if not c.disabled]
    except Exception as e:
        logger.debug("Cannot read %s: %s", config_path, e)
        return [c for c in configs.values() if not c.disabled]

    overrides = data if isinstance(data, list) else data.get("servers", data.get("lspServers", []))

    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        lid = entry.get("language_id", "")
        if not lid:
            continue

        if entry.get("disabled", False):
            configs.pop(lid, None)
            continue

        if lid in configs:
            cfg = configs[lid]
            if "command" in entry:
                cfg.command = entry["command"]
            if "args" in entry:
                cfg.args = entry["args"]
            if "extensions" in entry:
                cfg.extensions = entry["extensions"]
            if "env" in entry:
                cfg.env = entry["env"]
        else:
            configs[lid] = LSPServerConfig(
                language_id=lid,
                command=entry.get("command", ""),
                args=entry.get("args", []),
                extensions=entry.get("extensions", []),
                env=entry.get("env", {}),
            )

    return [c for c in configs.values() if not c.disabled and c.command]
