# Implementation Plan: ACP (Agent Client Protocol) Adapter for Zed

**Status:** Ready for Implementation  
**Scope:** Full ACP adapter enabling Wisp to run as an external agent inside Zed's agent panel  
**Protocol:** JSON-RPC 2.0 based Agent Client Protocol v0.11.1  
**Estimated Effort:** ~1200 lines across 4 files + tests

---

## Table of Contents

1. [Protocol Overview](#1-protocol-overview)
2. [Architecture](#2-architecture)
3. [Phase 1: Protocol Types — `wisp/acp_protocol.py`](#3-phase-1-protocol-types)
4. [Phase 2: Session Manager — `wisp/acp_session.py`](#4-phase-2-session-manager)
5. [Phase 3: Adapter Core — `wisp/acp_adapter.py`](#5-phase-3-adapter-core)
6. [Phase 4: CLI Entry Point](#6-phase-4-cli-entry-point)
7. [Phase 5: Zed Configuration](#7-phase-5-zed-configuration)
8. [Phase 6: Testing](#8-phase-6-testing)
9. [File Change Summary](#9-file-change-summary)

---

## 1. Protocol Overview

ACP is a JSON-RPC 2.0 protocol. Messages are newline-delimited JSON objects on stdin/stdout.

### Message Types

| Direction | Method | Purpose |
|-----------|--------|---------|
| Client → Agent | `initialize` | Handshake, exchange capabilities |
| Client → Agent | `session/new` | Create new conversation session |
| Client → Agent | `session/load` | Resume existing session |
| Client → Agent | `session/list` | List available sessions |
| Client → Agent | `prompt` | Send user message, get streaming response |
| Client → Agent | `tool/result` | Return result of a tool call |
| Client → Agent | `permission/response` | User granted/denied permission |
| Client → Agent | `config/set` | Set session configuration |
| Client → Agent | `cancel` | Cancel ongoing operation |
| Agent → Client | `initialize` (response) | Capabilities acknowledgment |
| Agent → Client | `prompt` (response) | Assistant response (streaming chunks) |
| Agent → Client | `tool/call` | Request to execute a tool |
| Agent → Client | `permission/request` | Ask user for permission |
| Agent → Client | `config/update` | Notify config changed |
| Agent → Client | `mode/update` | Notify mode changed |
| Agent → Client | `session/info` | Session metadata update |
| Agent → Client | `notification` | Generic notification |

### JSON-RPC Format

```json
// Request
{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {...}}

// Response
{"jsonrpc": "2.0", "id": 1, "result": {...}}

// Error
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "..."}}

// Notification (no id)
{"jsonrpc": "2.0", "method": "session/info", "params": {...}}
```

### Key Capabilities

**Agent (Wisp) capabilities:**
- `tools`: List of available tools (read_file, edit_file, run_bash, etc.)
- `prompts`: Support for user/assistant message exchange
- `terminals`: Can create and manage terminals
- `mcp`: MCP server integration
- `plans`: Structured task decomposition
- `permissions`: Can request user permission for dangerous operations
- `checkpoints`: Can save/restore git checkpoints
- `modes`: Support for different agent modes

**Client (Zed) capabilities:**
- `file_system`: Can read/write files
- `terminals`: Can create terminals
- `mcp`: Can connect MCP servers
- `permissions`: Can show permission dialogs
- `checkpoints`: Can create git checkpoints

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Zed Editor                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Agent Panel │  │ Threads     │  │ Tool Calls  │  │ Permission Dialogs  │  │
│  │ (chat UI)   │  │ Sidebar     │  │ (file ops)  │  │ (dangerous ops)     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                │                    │            │
│         └────────────────┴────────────────┴────────────────────┘            │
│                                    │                                        │
│                           ACP Client (Zed Rust)                             │
│                                    │                                        │
│                         stdin/stdout (JSON-RPC)                             │
│                                    │                                        │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                              ┌──────┴──────┐
                              │  ACP Adapter  │
                              │ (Python)      │
                              │               │
                              │ • JSON-RPC    │
                              │   parser      │
                              │ • Message     │
                              │   router      │
                              │ • Stream      │
                              │   handler     │
                              └──────┬──────┘
                                     │
                              ┌──────┴──────┐
                              │ Wisp Agent  │
                              │             │
                              │ • Ollama    │
                              │   client    │
                              │ • Tool      │
                              │   execution │
                              │ • Session   │
                              │   manager   │
                              └─────────────┘
```

---

## 3. Phase 1: Protocol Types — `wisp/acp_protocol.py`

**Purpose:** Typed dataclasses for all ACP messages with JSON serialization.

### Core Types

```python
@dataclass
class JsonRpcMessage:
    jsonrpc: str = "2.0"
    id: Optional[int | str] = None
    method: Optional[str] = None
    params: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[dict] = None

@dataclass
class InitializeRequest:
    protocol_version: str
    capabilities: ClientCapabilities
    client_info: Implementation

@dataclass
class InitializeResponse:
    protocol_version: str
    capabilities: AgentCapabilities
    agent_info: Implementation

@dataclass
class PromptRequest:
    session_id: str
    messages: list[Message]
    tools: Optional[list[Tool]] = None

@dataclass
class PromptResponse:
    content: list[ContentBlock]
    stop_reason: str = "end_turn"

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    meta: Optional[dict] = None

@dataclass
class ToolResult:
    id: str
    content: list[ContentBlock]
    is_error: bool = False

@dataclass
class ContentBlock:
    type: str  # text, image, resource, tool_call, tool_result, thinking
    text: Optional[str] = None
    # ... other fields
```

### Message Factory

```python
def make_request(id: int, method: str, params: dict) -> dict: ...
def make_response(id: int, result: dict) -> dict: ...
def make_error(id: int, code: int, message: str) -> dict: ...
def make_notification(method: str, params: dict) -> dict: ...
```

---

## 4. Phase 2: Session Manager — `wisp/acp_session.py`

**Purpose:** Map ACP sessions to Wisp sessions, manage conversation state.

```python
class AcpSession:
    """Wraps a WispAgent for use inside an ACP session."""

    def __init__(self, session_id: str, workspace: str, config: WispConfig):
        self.session_id = session_id
        self.workspace = workspace
        self.agent = WispAgent(config)
        self.messages: list[dict] = []
        self.tools: list[dict] = []
        self.mode: str = "default"
        self.config: dict = {}

    def add_user_message(self, content: str) -> None: ...
    def add_assistant_message(self, content: str) -> None: ...
    def add_tool_result(self, tool_call_id: str, result: str) -> None: ...
    def run_turn(self) -> Iterator[ContentBlock]: ...
    def to_info(self) -> SessionInfo: ...

class SessionManager:
    """Manages multiple ACP sessions."""

    def __init__(self):
        self.sessions: dict[str, AcpSession] = {}

    def create(self, workspace: str, config: WispConfig) -> AcpSession: ...
    def get(self, session_id: str) -> Optional[AcpSession]: ...
    def list(self) -> list[SessionInfo]: ...
    def load(self, session_id: str) -> Optional[AcpSession]: ...
```

---

## 5. Phase 3: Adapter Core — `wisp/acp_adapter.py`

**Purpose:** Main event loop — read JSON-RPC, route to handlers, write responses.

```python
class AcpAdapter:
    """ACP adapter that runs Wisp as an external agent in Zed."""

    def __init__(self, workspace: str = "."):
        self.workspace = workspace
        self.session_mgr = SessionManager()
        self.initialized = False
        self.protocol_version = "2025-03-26"
        self.agent_info = Implementation(name="wisp", version=__version__)

    def run(self) -> None:
        """Main loop: read stdin, handle messages, write stdout."""
        for line in sys.stdin:
            msg = self._parse(line)
            if msg is None:
                continue
            if msg.get("id") is not None:
                self._handle_request(msg)
            else:
                self._handle_notification(msg)

    def _handle_request(self, msg: dict) -> None:
        method = msg.get("method", "")
        handler = getattr(self, f"_handle_{method.replace('/', '_')}", None)
        if handler:
            try:
                result = handler(msg.get("params", {}))
                self._send_response(msg["id"], result)
            except Exception as e:
                self._send_error(msg["id"], -32603, str(e))
        else:
            self._send_error(msg["id"], -32601, f"Method not found: {method}")

    # ── Handlers ──
    def _handle_initialize(self, params: dict) -> dict: ...
    def _handle_session_new(self, params: dict) -> dict: ...
    def _handle_session_load(self, params: dict) -> dict: ...
    def _handle_session_list(self, params: dict) -> dict: ...
    def _handle_prompt(self, params: dict) -> dict: ...
    def _handle_tool_result(self, params: dict) -> dict: ...
    def _handle_permission_response(self, params: dict) -> dict: ...
    def _handle_config_set(self, params: dict) -> dict: ...
    def _handle_cancel(self, params: dict) -> dict: ...

    # ── Streaming ──
    def _stream_prompt_response(self, session: AcpSession, request_id: int) -> None:
        """Stream assistant response as incremental content blocks."""
        for block in session.run_turn():
            self._send_notification("prompt/chunk", {"content": [block.to_dict()]})
        self._send_response(request_id, {"stop_reason": "end_turn"})

    # ── Tool execution ──
    def _execute_tool_call(self, tool_call: ToolCall, session: AcpSession) -> ToolResult:
        """Execute a tool and return result."""
        # Map ACP tool names to Wisp tools
        # Handle dangerous commands with permission requests
        ...

    # ── I/O ──
    def _send_response(self, id: int, result: dict) -> None: ...
    def _send_error(self, id: int, code: int, message: str) -> None: ...
    def _send_notification(self, method: str, params: dict) -> None: ...
    def _parse(self, line: str) -> Optional[dict]: ...
```

### Tool Mapping

| ACP Tool Name | Wisp Tool | Notes |
|---------------|-----------|-------|
| `read_file` | `read_file` | Direct mapping |
| `write_file` | `write_file` | Direct mapping |
| `edit_file` | `edit_file` | Direct mapping |
| `run_bash` | `run_bash` | Dangerous → permission request |
| `list_files` | `list_files` | Direct mapping |
| `search_symbols` | `search_symbols` | Direct mapping |
| `web_fetch` | `web_fetch` | Direct mapping |
| `git_status` | `git_status` | Direct mapping |
| `git_diff` | `git_diff` | Direct mapping |
| `diagnose` | `diagnose` | Direct mapping |
| `plan_task` | `plan_task` | Direct mapping |
| `mark_step_done` | `mark_step_done` | Direct mapping |
| `update_plan` | `update_plan` | Direct mapping |
| `remember` | `remember` | Direct mapping |
| `spawn_subagent` | `spawn_subagent` | Direct mapping |
| `create_terminal` | N/A | Create via Zed's terminal API |
| `kill_terminal` | N/A | Kill via Zed's terminal API |

### Permission Handling

For dangerous operations (`run_bash` with destructive commands, `write_file` overwriting existing files):

```python
def _request_permission(self, tool_call: ToolCall, session: AcpSession) -> bool:
    """Ask Zed user for permission before executing dangerous tool."""
    self._send_notification("permission/request", {
        "session_id": session.session_id,
        "tool_call": tool_call.to_dict(),
        "options": [
            {"id": "allow", "label": "Allow"},
            {"id": "deny", "label": "Deny"},
            {"id": "allow_once", "label": "Allow Once"},
        ]
    })
    # Wait for permission/response notification
    # (handled by _handle_permission_response)
    ...
```

---

## 6. Phase 4: CLI Entry Point

Add to `wisp/__main__.py`:

```bash
wisp acp              # Start ACP adapter (reads stdin, writes stdout)
wisp acp --version    # Show ACP protocol version supported
```

```python
def cmd_acp(args: list[str]):
    """Run Wisp as an ACP external agent for Zed."""
    from wisp.acp_adapter import AcpAdapter
    adapter = AcpAdapter(workspace=".")
    adapter.run()
```

---

## 7. Phase 5: Zed Configuration

### Zed Settings (`~/.config/zed/settings.json`)

```json
{
  "agent_servers": {
    "wisp": {
      "type": "custom",
      "command": "python -m wisp acp",
      "env": {
        "WISP_MODEL": "kimi-k2.6:cloud",
        "WISP_WORKSPACE": "$ZED_WORKTREE_ROOT"
      }
    }
  }
}
```

### Keyboard Shortcut (`~/.config/zed/keymap.json`)

```json
[
  {
    "bindings": {
      "cmd-alt-w": [
        "agent::NewExternalAgentThread",
        { "agent": { "custom": { "name": "wisp" } } }
      ]
    }
  }
]
```

### Project-Specific (`.zed/settings.json`)

```json
{
  "agent_servers": {
    "wisp": {
      "type": "custom",
      "command": "python -m wisp acp",
      "env": {
        "WISP_MODEL": "kimi-k2.6:cloud"
      }
    }
  }
}
```

---

## 8. Phase 6: Testing

### `tests/test_acp_protocol.py` (~150 lines)
- Test JSON-RPC message parsing/serialization
- Test all message type roundtrips
- Test error formatting

### `tests/test_acp_adapter.py` (~200 lines)
- Test initialize handshake
- Test session creation
- Test prompt handling with mock agent
- Test tool call execution
- Test permission flow
- Test cancellation
- Test streaming responses

### `tests/test_acp_session.py` (~100 lines)
- Test session creation
- Test message history
- Test tool result integration
- Test session info serialization

---

## 9. File Change Summary

| File | Action | Lines | Notes |
|------|--------|-------|-------|
| `wisp/acp_protocol.py` | **New** | ~300 | All ACP message types |
| `wisp/acp_session.py` | **New** | ~200 | Session manager |
| `wisp/acp_adapter.py` | **New** | ~500 | Main adapter loop |
| `wisp/__main__.py` | Modify | ~30 | `wisp acp` command |
| `tests/test_acp_protocol.py` | **New** | ~150 | Protocol tests |
| `tests/test_acp_adapter.py` | **New** | ~200 | Adapter tests |
| `tests/test_acp_session.py` | **New** | ~100 | Session tests |
| **Total** | | **~1480** | |

---

## Open Questions

1. **Streaming:** ACP supports streaming content chunks. Wisp's `_run_turn_streaming` already produces `TokenBatch` events. Map these to `prompt/chunk` notifications.

2. **Terminals:** ACP has `terminal/create`, `terminal/kill`, `terminal/release`. Wisp doesn't have native terminal management — delegate to Zed via these ACP methods, or use `run_bash` as fallback.

3. **Checkpoints:** ACP supports git checkpoints (`checkpoint/create`, `checkpoint/restore`). Wisp has `git_context.py` but no checkpoint API. Phase 2 feature.

4. **Modes:** ACP supports session modes (e.g., "agent", "review", "ask"). Wisp has skills — map skills to modes.

5. **MCP over ACP:** ACP can tunnel MCP messages. Wisp already has MCP support — wire it through.

---

*End of Plan — Ready for implementation approval.*
