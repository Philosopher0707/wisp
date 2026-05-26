"""LSP (Language Server Protocol) tools for Wisp.

Provides diagnostics, go-to-definition, find-references, hover info,
and symbol listing via language servers.
"""

import logging

from wisp.tools._utils import (
    _resolve_path,
    _lsp_manager_ctx,
)

logger = logging.getLogger(__name__)


def _get_lsp_server(path: str, workspace: str, lsp_manager=None):
    """Resolve LSP manager and return (server, full_path) or error string."""
    mgr = lsp_manager or _lsp_manager_ctx.get()
    if mgr is None:
        return "Error: LSP not available (no language servers configured)."
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        return f"Error: file not found: {path}"
    server = mgr.get_server_safe(str(full_path))
    if server is None:
        return f"No LSP server available for {full_path.suffix} files."
    return (server, full_path)


def tool_lsp_diagnostics(path: str, workspace: str = ".") -> str:
    """Run language server diagnostics on a file to find errors and warnings."""
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        return f"Error: file not found: {path}"
    ext = full_path.suffix.lower()

    linters = {
        ".py": ["python3", "-m", "py_compile"],
        ".ts": ["npx", "tsc", "--noEmit"],
        ".tsx": ["npx", "tsc", "--noEmit"],
        ".js": ["npx", "eslint"],
        ".jsx": ["npx", "eslint"],
        ".rs": ["cargo", "check"],
        ".go": ["go", "vet"],
    }
    cmd = linters.get(ext)
    if not cmd:
        return f"No diagnostics available for {ext} files."

    import subprocess
    try:
        r = subprocess.run(cmd + [str(full_path)], capture_output=True, text=True,
                          timeout=60, cwd=workspace)
        output = r.stdout + r.stderr
        if not output.strip():
            return "✓ No issues found."
        if len(output) > 5000:
            output = output[:5000] + "\n... [output truncated]"
        status = "✓ No errors" if r.returncode == 0 else f"Found issues (exit {r.returncode})"
        return f"[{status}]\n{output}"
    except FileNotFoundError:
        return f"Error: linter not found for {ext}. Install it first."
    except subprocess.TimeoutExpired:
        return "Error: diagnostics timed out."


def tool_lsp_definition(path: str, workspace: str = ".", line: int = 1, character: int = 1, lsp_manager=None) -> str:
    """Go to definition of a symbol at the given line and character (1-based)."""
    from wisp.lsp.client import _format_locations
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        locations = server.get_definition(str(full_path), line - 1, character - 1)
        return _format_locations(locations, workspace, max_items=5)
    except Exception as e:
        return f"Error: {e}"


def tool_lsp_references(path: str, workspace: str = ".", line: int = 1, character: int = 1, lsp_manager=None) -> str:
    """Find all references to a symbol at the given line and character (1-based)."""
    from wisp.lsp.client import _format_locations
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        locations = server.get_references(str(full_path), line - 1, character - 1)
        return _format_locations(locations, workspace, max_items=50)
    except Exception as e:
        return f"Error: {e}"


def tool_lsp_hover(path: str, workspace: str = ".", line: int = 1, character: int = 1, lsp_manager=None) -> str:
    """Get hover info (type signature, docstring) for the symbol at the given line and character (1-based)."""
    from wisp.lsp.client import _format_hover
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        result = server.get_hover(str(full_path), line - 1, character - 1)
        return _format_hover(result)
    except Exception as e:
        return f"Error: {e}"


def tool_lsp_symbols(path: str, workspace: str = ".", lsp_manager=None) -> str:
    """List all symbols (functions, classes, methods, etc.) in a file."""
    from wisp.lsp.client import _format_symbols
    resolved = _get_lsp_server(path, workspace, lsp_manager)
    if isinstance(resolved, str):
        return resolved
    server, full_path = resolved
    try:
        symbols = server.get_symbols(str(full_path))
        return _format_symbols(symbols)
    except Exception as e:
        return f"Error: {e}"
