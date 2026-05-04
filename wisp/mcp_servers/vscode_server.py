"""VS Code MCP server — exposes VS Code editor capabilities as MCP tools.

Allows Wisp to interact with VS Code: open files, navigate to lines,
run commands, get editor state, and show messages.

Usage:
  python -m wisp.mcp_servers.vscode_server

Then add to .wisp/mcp.json:
  [{"name": "vscode", "command": "python", "args": ["-m", "wisp.mcp_servers.vscode_server"]}]
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── MCP Protocol Handlers ────────────────────────────────────────────


def handle_initialize(params: dict) -> dict:
    """Handle initialize request."""
    return {
        "protocolVersion": "2025-03-26",
        "capabilities": {
            "tools": {},
        },
        "serverInfo": {
            "name": "wisp-vscode",
            "version": "0.1.0",
        },
    }


def handle_list_tools() -> dict:
    """Handle tools/list request."""
    return {
        "tools": [
            {
                "name": "vscode_open_file",
                "description": "Open a file in VS Code at a specific line",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file to open",
                        },
                        "line": {
                            "type": "number",
                            "description": "Line number to navigate to (1-indexed)",
                            "default": 1,
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "vscode_run_command",
                "description": "Run a VS Code command (e.g., 'workbench.action.files.save', 'editor.action.formatDocument')",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "VS Code command ID to execute",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "vscode_show_message",
                "description": "Show a message in VS Code's notification area",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to display",
                        },
                        "type": {
                            "type": "string",
                            "description": "Message type: info, warning, error",
                            "enum": ["info", "warning", "error"],
                            "default": "info",
                        },
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "vscode_get_editor_state",
                "description": "Get the current editor state (open files, cursor position, visible range)",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ],
    }


def handle_call_tool(name: str, arguments: dict) -> dict:
    """Handle tools/call request."""
    if name == "vscode_open_file":
        return _open_file(arguments.get("path", ""), arguments.get("line", 1))
    elif name == "vscode_run_command":
        return _run_command(arguments.get("command", ""))
    elif name == "vscode_show_message":
        return _show_message(arguments.get("message", ""), arguments.get("type", "info"))
    elif name == "vscode_get_editor_state":
        return _get_editor_state()
    else:
        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}


# ── VS Code Integration ──────────────────────────────────────────────


def _code_cli(args: list[str]) -> tuple[str, str, int]:
    """Run a VS Code CLI command and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            ["code"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return "", "VS Code CLI 'code' not found. Is VS Code installed and in PATH?", 1
    except subprocess.TimeoutExpired:
        return "", "Command timed out", 1


def _open_file(path: str, line: int = 1) -> dict:
    """Open a file in VS Code at a specific line."""
    if not path:
        return {"content": [{"type": "text", "text": "No path provided"}], "isError": True}

    resolved = str(Path(path).resolve())
    goto = f"{resolved}:{line}"
    stdout, stderr, rc = _code_cli([ "--goto", goto])

    if rc == 0:
        return {"content": [{"type": "text", "text": f"✓ Opened {resolved} at line {line}"}]}
    else:
        return {
            "content": [{"type": "text", "text": f"Failed to open file: {stderr}"}],
            "isError": True,
        }


def _run_command(command: str) -> dict:
    """Run a VS Code command."""
    if not command:
        return {"content": [{"type": "text", "text": "No command provided"}], "isError": True}

    stdout, stderr, rc = _code_cli(["--command", command])

    if rc == 0:
        return {"content": [{"type": "text", "text": f"✓ Executed command: {command}"}]}
    else:
        return {
            "content": [{"type": "text", "text": f"Failed to run command '{command}': {stderr}"}],
            "isError": True,
        }


def _show_message(message: str, msg_type: str = "info") -> dict:
    """Show a message in VS Code."""
    if not message:
        return {"content": [{"type": "text", "text": "No message provided"}], "isError": True}

    command_map = {
        "info": "workbench.action.showInfoMessage",
        "warning": "workbench.action.showWarningMessage",
        "error": "workbench.action.showErrorMessage",
    }
    cmd = command_map.get(msg_type, "workbench.action.showInfoMessage")

    stdout, stderr, rc = _code_cli(["--command", f"{cmd} {message}"])

    if rc == 0:
        return {"content": [{"type": "text", "text": f"✓ Message shown: {message}"}]}
    else:
        return {
            "content": [{"type": "text", "text": f"Failed to show message: {stderr}"}],
            "isError": True,
        }


def _get_editor_state() -> dict:
    """Get current editor state via VS Code command."""
    # VS Code doesn't have a direct CLI for getting editor state,
    # but we can try to get the active file via a command
    stdout, stderr, rc = _code_cli(["--command", "workbench.action.files.activeFile"])

    if rc == 0 and stdout.strip():
        return {
            "content": [{"type": "text", "text": f"Active file: {stdout.strip()}"}],
        }
    else:
        return {
            "content": [{"type": "text", "text": "Could not determine editor state. Make sure VS Code is running."}],
        }


# ── JSON-RPC Loop ────────────────────────────────────────────────────


def main():
    """Run the VS Code MCP server on stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "initialize":
                result = handle_initialize(params)
            elif method == "tools/list":
                result = handle_list_tools()
            elif method == "tools/call":
                result = handle_call_tool(params.get("name", ""), params.get("arguments", {}))
            else:
                result = {"error": f"Unknown method: {method}"}

            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except Exception as e:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"message": str(e)},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
