"""LSPManager — manages multiple LSP servers, mapping file extensions to LSPServer instances."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from wisp.lsp.client import (
    LSPServer,
    LSPServerConfig,
    LSPServerError,
    _BUILTIN_LSP_CONFIGS,
)

logger = logging.getLogger(__name__)


class LSPManager:
    """Orchestrates multiple LSP servers, one per language.

    Servers are started lazily on first use and live for the agent session.
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self._servers: dict[str, LSPServer] = {}  # language_id -> LSPServer
        self._configs: list[LSPServerConfig] = []
        self._initialized = False

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

        return self._servers[lid]

    def get_server_safe(self, file_path: str) -> Optional[LSPServer]:
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
