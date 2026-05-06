"""LSP (Language Server Protocol) integration for Wisp.

Provides go-to-definition, find-references, hover, and document symbols
by managing language server processes (pylsp, rust-analyzer, etc.).
"""

from wisp.lsp.client import LSPServer, LSPServerConfig, LSPServerError
from wisp.lsp.manager import LSPManager

__all__ = ["LSPServer", "LSPServerConfig", "LSPServerError", "LSPManager"]
