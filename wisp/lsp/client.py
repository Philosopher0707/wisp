"""LSPServer — manages a single Language Server Protocol process.

Handles Content-Length framed JSON-RPC transport, background reader thread
for async notifications, two-phase init handshake, and document sync.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


class LSPServerError(Exception):
    """Raised on LSP communication failures, timeouts, or server crashes."""


@dataclass
class LSPServerConfig:
    """Configuration for a single language server."""

    language_id: str
    extensions: list[str] = field(default_factory=list)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False


# ── Built-in language server configs ─────────────────────────────────

_BUILTIN_LSP_CONFIGS = [
    LSPServerConfig(
        language_id="python",
        extensions=[".py", ".pyi"],
        command="pylsp",
    ),
    LSPServerConfig(
        language_id="rust",
        extensions=[".rs"],
        command="rust-analyzer",
    ),
    LSPServerConfig(
        language_id="typescript",
        extensions=[".ts", ".tsx", ".js", ".jsx"],
        command="typescript-language-server",
        args=["--stdio"],
    ),
    LSPServerConfig(
        language_id="go",
        extensions=[".go"],
        command="gopls",
    ),
]

# ── URI helpers ──────────────────────────────────────────────────────


def path_to_uri(file_path: str) -> str:
    """Convert an absolute filesystem path to a file:// URI."""
    return Path(file_path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI back to a filesystem path."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise LSPServerError(f"Not a file URI: {uri}")
    return unquote(parsed.path)


# ── Sentinel for reader thread shutdown ──────────────────────────────

_STOP_SENTINEL = object()

# ── LSPServer ────────────────────────────────────────────────────────


class LSPServer:
    """Manages a single LSP process with Content-Length framed JSON-RPC transport."""

    def __init__(self, config: LSPServerConfig, root_path: str):
        self.config = config
        self.root_path = os.path.abspath(root_path)
        self.process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._response_queue: queue.Queue = queue.Queue()
        self._notification_queue: queue.Queue = queue.Queue()
        self._next_id = 1
        self._open_docs: dict[str, int] = {}  # uri -> version
        self._diagnostics: dict[str, list[dict]] = {}  # uri -> list[Diagnostic]
        self._started = False
        self.server_capabilities: dict = {}

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the LSP process, start reader thread, perform init handshake."""
        env = dict(os.environ)
        if self.config.env:
            env.update(self.config.env)

        # Resolve command path — try PATH, then common framework locations
        import shutil
        command_path = shutil.which(self.config.command)
        if not command_path:
            _EXTRA_PATHS = [
                "/Library/Frameworks/Python.framework/Versions/Current/bin",
                "/Library/Frameworks/Python.framework/Versions/3/bin",
                os.path.expanduser("~/.local/bin"),
                os.path.expanduser("~/Library/Python/3.12/bin"),
                os.path.expanduser("~/Library/Python/3.13/bin"),
            ]
            for p in _EXTRA_PATHS:
                candidate = os.path.join(p, self.config.command)
                if os.path.isfile(candidate):
                    command_path = candidate
                    break

        if not command_path:
            raise LSPServerError(
                f"{self.config.command} not found on PATH. Install it first "
                f"(e.g., pip install python-lsp-server)."
            )

        try:
            self.process = subprocess.Popen(
                [command_path] + self.config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.root_path,
                env=env,
                text=False,  # binary mode for Content-Length header parsing
            )
        except Exception as e:
            raise LSPServerError(f"Failed to start {self.config.command}: {e}")

        # Start background reader
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(self.process, self._response_queue, self._notification_queue, self._stop_event),
            daemon=True,
        )
        self._reader_thread.start()

        # Init handshake
        caps = self._send_init()
        self.server_capabilities = caps.get("capabilities", {})

        # Send initialized notification
        self._send_notification("initialized", {})
        self._started = True
        logger.info("LSP server %s initialized", self.config.command)

    def shutdown(self) -> None:
        """Gracefully shut down the LSP process."""
        if not self._started:
            return
        self._stop_event.set()

        try:
            self.send_request("shutdown", {}, timeout=3)
        except Exception:
            pass

        try:
            self._send_notification("exit", {})
        except Exception:
            pass

        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                    self.process.wait(timeout=2)
                except Exception:
                    pass

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)

        self._started = False
        self.process = None

    # ── Request / Notification ────────────────────────────────────

    def send_request(self, method: str, params: dict, timeout: int = 30) -> dict:
        """Send a JSON-RPC request and block waiting for the response."""
        if not self._started:
            raise LSPServerError(f"LSP server {self.config.command} not started")

        req_id = self._next_id
        self._next_id += 1

        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self._write_message(request)

        # Block waiting for matching response
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LSPServerError(f"LSP request timeout: {method} ({timeout}s)")
            try:
                item = self._response_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue

            if item is _STOP_SENTINEL:
                raise LSPServerError(f"LSP server {self.config.command} process died")

            if isinstance(item, dict) and item.get("id") == req_id:
                if "error" in item:
                    raise LSPServerError(f"LSP error: {item['error']}")
                return item.get("result", {})

            # Response for a different request — re-queue
            self._response_queue.put(item)

    def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write_message(msg)

    def _send_init(self) -> dict:
        """Send initialize request and return the result."""
        if not self.process or not self.process.stdin:
            raise LSPServerError("Process not started")

        req_id = self._next_id
        self._next_id += 1

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": path_to_uri(self.root_path),
                "capabilities": {
                    "textDocument": {
                        "definition": {"linkSupport": True},
                        "references": {},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    }
                },
            },
        }
        self._write_message(request)

        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LSPServerError(f"LSP init timeout for {self.config.command}")
            try:
                item = self._response_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue

            if item is _STOP_SENTINEL:
                raise LSPServerError(f"LSP server {self.config.command} died during init")

            if isinstance(item, dict) and item.get("id") == req_id:
                if "error" in item:
                    raise LSPServerError(f"LSP init error: {item['error']}")
                return item.get("result", {})

            self._response_queue.put(item)

    # ── Document sync ─────────────────────────────────────────────

    def ensure_document_open(self, file_path: str) -> None:
        """Send textDocument/didOpen if not already tracked."""
        uri = path_to_uri(os.path.abspath(file_path))
        if uri in self._open_docs:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            raise LSPServerError(f"Cannot read {file_path}: {e}")

        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.config.language_id,
                    "version": 1,
                    "text": content,
                }
            },
        )
        self._open_docs[uri] = 1

    def notify_text_change(self, file_path: str, text: str) -> int:
        """Send textDocument/didChange with caller-supplied in-memory text.

        Unlike :meth:`notify_change` (which re-reads disk), this carries
        speculative content — the basis for pre-mutation diagnostics.
        Returns the new document version. Disk is never touched.
        """
        uri = path_to_uri(os.path.abspath(file_path))
        if uri not in self._open_docs:
            self.ensure_document_open(file_path)
            uri = path_to_uri(os.path.abspath(file_path))
        version = self._open_docs[uri] + 1
        self._open_docs[uri] = version
        self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )
        return version

    def notify_change(self, file_path: str) -> None:
        """Send textDocument/didChange with full content replacement."""
        uri = path_to_uri(os.path.abspath(file_path))
        if uri not in self._open_docs:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return

        version = self._open_docs[uri] + 1
        self._open_docs[uri] = version

        self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            },
        )

    # ── LSP feature methods ───────────────────────────────────────

    def get_definition(self, file_path: str, line: int, character: int) -> list[dict]:
        """Return list of Location/LocationLink dicts."""
        self.ensure_document_open(file_path)
        uri = path_to_uri(os.path.abspath(file_path))
        result = self.send_request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )
        if isinstance(result, dict) and "uri" in result:
            return [result]
        if isinstance(result, list):
            return result
        return []

    def get_references(self, file_path: str, line: int, character: int) -> list[dict]:
        """Return list of Location dicts."""
        self.ensure_document_open(file_path)
        uri = path_to_uri(os.path.abspath(file_path))
        result = self.send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": False},
            },
        )
        if isinstance(result, list):
            return result
        return []

    def get_hover(self, file_path: str, line: int, character: int) -> dict:
        """Return Hover result dict."""
        self.ensure_document_open(file_path)
        uri = path_to_uri(os.path.abspath(file_path))
        result = self.send_request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": character}},
        )
        if isinstance(result, dict):
            return result
        return {}

    def get_diagnostics(self, file_path: str) -> list[dict]:
        """Return cached diagnostics for a file."""
        uri = path_to_uri(os.path.abspath(file_path))
        return self._diagnostics.get(uri, [])

    def get_symbols(self, file_path: str) -> list[dict]:
        """Return list of DocumentSymbol or SymbolInformation dicts."""
        self.ensure_document_open(file_path)
        uri = path_to_uri(os.path.abspath(file_path))
        result = self.send_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        if isinstance(result, list):
            return result
        return []

    # ── Transport layer ───────────────────────────────────────────

    def _write_message(self, msg: dict) -> None:
        """Write a Content-Length framed JSON-RPC message to stdin."""
        if not self.process or not self.process.stdin:
            raise LSPServerError("LSP process not available")
        body_bytes = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("utf-8")
        try:
            self.process.stdin.write(header + body_bytes)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise LSPServerError(f"Failed to write to LSP server: {e}")

    def _reader_loop(
        self,
        process: subprocess.Popen,
        response_q: queue.Queue,
        notification_q: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        """Background thread: read Content-Length framed messages from stdout."""
        stdout = process.stdout
        if not stdout:
            return

        try:
            while not stop_event.is_set():
                # Read headers until \r\n\r\n
                header_bytes = b""
                while b"\r\n\r\n" not in header_bytes:
                    ch = stdout.read(1)
                    if not ch:
                        logger.debug("LSP server stdout closed")
                        response_q.put(_STOP_SENTINEL)
                        return
                    header_bytes += ch

                # Parse Content-Length
                header_text = header_bytes.decode("utf-8", errors="replace")
                m = re.search(r"Content-Length:\s*(\d+)", header_text)
                if not m:
                    logger.warning("LSP: no Content-Length in header: %r", header_text)
                    continue

                content_length = int(m.group(1))
                body_bytes = stdout.read(content_length)
                if len(body_bytes) < content_length:
                    logger.warning("LSP: short read (%d < %d)", len(body_bytes), content_length)
                    response_q.put(_STOP_SENTINEL)
                    return

                try:
                    msg = json.loads(body_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning("LSP: invalid JSON in message body")
                    continue

                if "id" in msg:
                    response_q.put(msg)
                else:
                    notification_q.put(msg)

                # Drain notifications — capture diagnostics, log others
                while True:
                    try:
                        notif = notification_q.get_nowait()
                        method = notif.get("method", "")
                        if method == "textDocument/publishDiagnostics":
                            params = notif.get("params", {})
                            uri = params.get("uri", "")
                            diagnostics = params.get("diagnostics", [])
                            self._diagnostics[uri] = diagnostics
                        logger.debug("LSP notification: %s", method)
                    except queue.Empty:
                        break

        except Exception as e:
            logger.error("LSP reader error: %s", e)
            try:
                response_q.put(_STOP_SENTINEL)
            except Exception:
                pass


# ── Output formatters ────────────────────────────────────────────────


def _format_locations(locations: list[dict], workspace: str, max_items: int = 50) -> str:
    """Format LSP Location/LocationLink objects into readable text."""
    if not locations:
        return "No locations found."

    lines: list[str] = []
    for i, loc in enumerate(locations[:max_items]):
        if "uri" in loc:
            uri = loc["uri"]
            range_info = loc.get("range", {})
        elif "targetUri" in loc:
            uri = loc["targetUri"]
            range_info = loc.get("targetSelectionRange", loc.get("targetRange", {}))
        else:
            continue

        try:
            fpath = uri_to_path(uri)
        except LSPServerError:
            fpath = uri

        start = range_info.get("start", {})
        line_num = start.get("line", 0) + 1  # LSP is 0-based → 1-based
        char_num = start.get("character", 0) + 1

        # Try to read context line
        try:
            rel = os.path.relpath(fpath, workspace) if fpath.startswith("/") else fpath
        except ValueError:
            rel = fpath

        snippet = ""
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for j, file_line in enumerate(f):
                    if j == start.get("line", 0):
                        snippet = file_line.strip()[:80]
                        break
        except Exception:
            pass

        entry = f"{rel}:{line_num}:{char_num}"
        if snippet:
            entry += f"  → {snippet}"
        lines.append(entry)

    if len(locations) > max_items:
        lines.append(f"... and {len(locations) - max_items} more")

    return "\n".join(lines)


def _format_hover(result: dict) -> str:
    """Format LSP Hover result into readable text."""
    if not result:
        return "No hover information."

    parts: list[str] = []

    contents = result.get("contents", {})
    if isinstance(contents, str):
        parts.append(contents)
    elif isinstance(contents, dict):
        # MarkupContent
        value = contents.get("value", "")
        if value:
            parts.append(value)
    elif isinstance(contents, list):
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("value", ""))

    # Range info
    range_info = result.get("range")
    if range_info:
        start = range_info.get("start", {})
        end = range_info.get("end", {})
        parts.append(f"[L{start.get('line',0)+1}:C{start.get('character',0)+1} - L{end.get('line',0)+1}:C{end.get('character',0)+1}]")

    text = "\n".join(parts)
    if len(text) > 5000:
        text = text[:5000] + "\n... [truncated]"
    return text or "No hover information."


def _format_symbols(symbols: list[dict], depth: int = 0, max_chars: int = 5000) -> str:
    """Recursively format hierarchical DocumentSymbol list."""
    if not symbols:
        return "No symbols found."

    lines: list[str] = []
    _format_symbols_recursive(symbols, lines, depth, max_chars)
    return "\n".join(lines)


_SYMBOL_KIND_MAP = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum member", 23: "struct", 24: "event",
    25: "operator", 26: "type parameter",
}


def _format_symbols_recursive(
    symbols: list[dict], lines: list[str], depth: int, max_chars: int
) -> None:
    """Recursively append formatted symbols to lines list."""
    prefix = "  " * depth
    for s in symbols:
        name = s.get("name", "?")
        kind = s.get("kind", 0)
        kind_name = _SYMBOL_KIND_MAP.get(kind, f"kind{kind}")
        line_info = ""
        range_info = s.get("range") or s.get("location", {}).get("range", {})
        if range_info:
            ln = range_info.get("start", {}).get("line", 0) + 1
            line_info = f":{ln}"
        entry = f"{prefix}{name} [{kind_name}]{line_info}"
        lines.append(entry)

        # Check char limit
        if sum(len(l) for l in lines) > max_chars:
            lines.append("... [output truncated]")
            return

        children = s.get("children", [])
        if children:
            _format_symbols_recursive(children, lines, depth + 1, max_chars)
