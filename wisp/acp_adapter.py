"""ACP (Agent Client Protocol) adapter for Zed integration.

Runs Wisp as an external agent inside Zed's agent panel.
Communicates via newline-delimited JSON-RPC on stdin/stdout.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from wisp import __version__
from wisp.infra.audit import audit
from wisp.acp_protocol import (
    AgentCapabilities,
    ConfigSetRequest,
    ErrorCode,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionRequest,
    NewSessionRequest,
    NewSessionResponse,
    PermissionRequest,
    PermissionOption,
    PermissionResponse,
    PromptRequest,
    TextContent,
    ThinkingContent,
    ToolResultRequest,
    make_error,
    make_notification,
    make_response,
)
from wisp.acp_session import AcpSessionManager
from wisp.config import WispConfig
from wisp.tools import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

# ACP protocol version we support
ACP_PROTOCOL_VERSION = "2025-03-26"


class AcpAdapter:
    """Main ACP adapter that bridges Zed and Wisp."""

    def __init__(self, workspace: str = ".", session_mgr: Optional[AcpSessionManager] = None):
        self.workspace = workspace
        self.session_mgr = session_mgr or AcpSessionManager()
        self.initialized = False
        self.client_capabilities: Optional[dict] = None
        self._lock = threading.Lock()
        self._pending_permissions: dict[str, threading.Event] = {}
        self._permission_results: dict[str, str] = {}

    def run(self) -> None:
        """Main loop: read JSON-RPC from stdin, handle, write to stdout."""
        logger.info("Wisp ACP adapter starting (protocol %s)", ACP_PROTOCOL_VERSION)

        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                msg = self._parse(line)
                if msg is None:
                    continue

                if msg.get("id") is not None:
                    self._handle_request(msg)
                else:
                    self._handle_notification(msg)
        except KeyboardInterrupt:
            logger.info("ACP adapter interrupted")
        finally:
            logger.info("ACP adapter shutting down")

    # ── Parsing ────────────────────────────────────────────────────────────

    def _parse(self, line: str) -> Optional[dict]:
        """Parse a JSON-RPC message from a line."""
        try:
            msg = json.loads(line)
            # Reject non-object payloads (e.g. bare numbers, strings, arrays)
            # before calling .get() -- otherwise a malformed JSON-RPC frame
            # raises AttributeError and kills the adapter (DoS).
            if not isinstance(msg, dict):
                logger.warning("JSON-RPC payload is not an object: %s", type(msg).__name__)
                self._send_error(None, ErrorCode.PARSE_ERROR, "JSON-RPC payload must be an object")
                return None
            if msg.get("jsonrpc") != "2.0":
                logger.warning("Invalid JSON-RPC version: %s", msg.get("jsonrpc"))
                return None
            return msg
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse JSON-RPC: %s", e)
            self._send_error(None, ErrorCode.PARSE_ERROR, f"Parse error: {e}")
            return None

    # ── Request Handling ─────────────────────────────────────────────────

    def _handle_request(self, msg: dict) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        req_id = msg["id"]

        handler_name = f"_handle_{method.replace('/', '_')}"
        handler = getattr(self, handler_name, None)

        if handler:
            try:
                result = handler(params)
                self._send_response(req_id, result)
            except Exception as e:
                logger.exception("Error handling %s", method)
                self._send_error(req_id, ErrorCode.INTERNAL_ERROR, str(e))
        else:
            logger.warning("Method not found: %s", method)
            self._send_error(req_id, ErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}")

    def _handle_notification(self, msg: dict) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})

        handler_name = f"_handle_notif_{method.replace('/', '_')}"
        handler = getattr(self, handler_name, None)

        if handler:
            try:
                handler(params)
            except Exception:
                logger.exception("Error handling notification %s", method)

    # ── Method Handlers ──────────────────────────────────────────────────

    def _handle_initialize(self, params: dict) -> dict:
        """Handle initialize request — handshake with Zed."""
        req = InitializeRequest.from_dict(params)
        self.client_capabilities = req.capabilities.to_dict()
        self.initialized = True

        # Build agent capabilities with available tools
        tools = []
        for schema in TOOL_SCHEMAS:
            fn = schema.get("function", {})
            tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            })

        caps = AgentCapabilities(tools=tools)
        resp = InitializeResponse(
            protocol_version=ACP_PROTOCOL_VERSION,
            capabilities=caps,
            agent_info=Implementation(name="wisp", version=__version__),
        )
        logger.info("Initialized with client: %s %s", req.client_info.name, req.client_info.version)
        return resp.to_dict()

    def _handle_session_new(self, params: dict) -> dict:
        """Create a new ACP session."""
        req = NewSessionRequest.from_dict(params)
        config = WispConfig()
        workspace = req.workspace or self.workspace

        # Security: reject paths containing traversal sequences
        if ".." in workspace:
            return make_error(
                None, ErrorCode.INVALID_PARAMS,
                "Workspace path may not contain directory traversal sequences."
            )

        # If WISP_ALLOWED_WORKSPACE_ROOTS is set, enforce it.
        # Otherwise (default), allow the path after resolving it.
        allowed_roots_raw = os.environ.get("WISP_ALLOWED_WORKSPACE_ROOTS")
        if allowed_roots_raw:
            try:
                workspace_path = Path(workspace).resolve()
                allowed = [Path(r.strip()).resolve() for r in allowed_roots_raw.split(",")]
                if not any(
                    workspace_path == a or str(workspace_path).startswith(str(a) + os.sep)
                    for a in allowed
                ):
                    return make_error(
                        None,
                        ErrorCode.INVALID_PARAMS,
                        f"Workspace path must be within allowed roots: {allowed_roots_raw}"
                    )
            except Exception:
                return make_error(
                    None, ErrorCode.INVALID_PARAMS,
                    f"Invalid workspace path: {workspace}"
                )

        config = config.replace(workspace=str(Path(workspace).resolve()))
        session = self.session_mgr.create(config.workspace, config, title=req.title)
        return NewSessionResponse(session=session.to_info()).to_dict()

    def _handle_session_load(self, params: dict) -> dict:
        """Load an existing session."""
        req = LoadSessionRequest.from_dict(params)
        session = self.session_mgr.load(req.session_id)
        if session:
            return {"session": session.to_info().to_dict()}
        return make_error(None, ErrorCode.INVALID_PARAMS, f"Session not found: {req.session_id}")

    def _handle_session_list(self, _params: dict) -> dict:
        """List all sessions."""
        return ListSessionsResponse(sessions=self.session_mgr.list()).to_dict()

    def _handle_prompt(self, params: dict) -> dict:
        """Handle a prompt request — this is the main chat interaction.

        Returns immediately with a response ID, then streams content
        via notifications.
        """
        req = PromptRequest.from_dict(params)
        session = self.session_mgr.get(req.session_id)
        if not session:
            return make_error(None, ErrorCode.INVALID_PARAMS, f"Session not found: {req.session_id}")

        # Add user messages
        for msg in req.messages:
            if msg.role == "user":
                session.add_user_message(msg.content)

        # Run the agent turn and collect all content blocks
        content_blocks = []
        try:
            for block in session.run_turn():
                content_blocks.append(block)
        except Exception as e:
            logger.exception("Error in prompt turn")
            content_blocks.append(TextContent(text=f"Error: {e}"))

        # Build response text from content blocks
        response_text = ""
        for block in content_blocks:
            if isinstance(block, TextContent):
                response_text += block.text
            elif isinstance(block, ThinkingContent):
                response_text += block.text

        # Return simple text response — Zed expects content as string, not blocks
        return {
            "content": response_text,
            "stop_reason": "end_turn",
        }

    def _handle_tool_result(self, params: dict) -> dict:
        """Handle a tool result from Zed (after we sent a tool_call)."""
        req = ToolResultRequest.from_dict(params)
        session = self.session_mgr.get(req.session_id)
        if not session:
            return make_error(None, ErrorCode.INVALID_PARAMS, f"Session not found: {req.session_id}")

        # Extract result text from content blocks
        result_text = ""
        for block in req.content:
            if hasattr(block, "text"):
                result_text += block.text

        session.add_tool_result(req.tool_call_id, result_text, is_error=req.is_error)
        return {"status": "ok"}

    def _handle_permission_response(self, params: dict) -> dict:
        """Handle user permission response."""
        resp = PermissionResponse.from_dict(params)
        key = f"{resp.session_id}:{resp.tool_call_id}"
        self._permission_results[key] = resp.selected_option
        event = self._pending_permissions.get(key)
        if event:
            event.set()
        return {"status": "ok"}

    def _handle_config_set(self, params: dict) -> dict:
        """Set a session configuration option."""
        req = ConfigSetRequest.from_dict(params)
        session = self.session_mgr.get(req.session_id)
        if not session:
            return make_error(None, ErrorCode.INVALID_PARAMS, f"Session not found: {req.session_id}")

        # Map config keys to Wisp settings
        original_value = None
        if req.key == "model":
            original_value = session.config.model
            session.config = session.config.replace(model=req.value)
        elif req.key == "skill":
            original_value = getattr(session, "_active_skill", None)
            session._active_skill = req.value
        elif req.key == "auto_approve":
            original_value = session.config.auto_approve
            session.config = session.config.replace(auto_approve=req.value.lower() in ("true", "1", "yes"))
        elif req.key == "show_thinking":
            original_value = session.config.show_thinking
            session.config = session.config.replace(show_thinking=req.value.lower() in ("true", "1", "yes"))

        audit.record(
            "config_change",
            actor=f"acp:{req.session_id}",
            key=req.key,
            old_value=original_value,
            new_value=req.value,
        )
        return {"status": "ok"}

    def _handle_cancel(self, params: dict) -> dict:
        """Cancel an ongoing operation."""
        session_id = params.get("session_id", "")
        session = self.session_mgr.get(session_id)
        if session:
            session._interrupted = True
        return {"status": "ok"}

    # ── Notification Handlers ──────────────────────────────────────────────

    def _handle_notif_config_update(self, params: dict) -> None:
        """Client notified us of config change."""
        logger.debug("Config update: %s", params)

    def _handle_notif_mode_update(self, params: dict) -> None:
        """Client notified us of mode change."""
        session_id = params.get("session_id", "")
        mode = params.get("mode", "")
        session = self.session_mgr.get(session_id)
        if session:
            session.mode = mode
            logger.info("Session %s mode changed to %s", session_id, mode)

    # ── I/O ────────────────────────────────────────────────────────────────

    def _send_response(self, req_id: int | str, result: dict) -> None:
        msg = make_response(req_id, result)
        self._write(msg)

    def _send_error(self, req_id: int | str | None, code: int, message: str) -> None:
        msg = make_error(req_id, code, message)
        self._write(msg)

    def _send_notification(self, method: str, params: dict) -> None:
        msg = make_notification(method, params)
        self._write(msg)

    def _write(self, msg: dict) -> None:
        """Write a JSON-RPC message to stdout."""
        try:
            line = json.dumps(msg, ensure_ascii=False)
            with self._lock:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        except Exception as e:
            logger.error("Failed to write response: %s", e)

    # ── Permission Helper ────────────────────────────────────────────────

    def request_permission(
        self,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        description: str,
    ) -> bool:
        """Request permission from Zed user. Returns True if granted."""
        key = f"{session_id}:{tool_call_id}"
        event = threading.Event()
        self._pending_permissions[key] = event

        req = PermissionRequest(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            description=description,
            options=[
                PermissionOption(id="allow", label="Allow"),
                PermissionOption(id="deny", label="Deny"),
                PermissionOption(id="allow_once", label="Allow Once"),
            ],
        )
        self._send_notification("permission/request", req.to_dict())

        # Wait for response (with timeout)
        if event.wait(timeout=60):
            result = self._permission_results.get(key, "deny")
            return result in ("allow", "allow_once")
        return False


def main(args=None):
    """Entry point for `python -m wisp acp`."""
    import argparse

    parser = argparse.ArgumentParser(description="Wisp ACP adapter for Zed")
    parser.add_argument("--workspace", "-w", default=".", help="Workspace directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parsed = parser.parse_args(args)

    logging.basicConfig(
        level=logging.DEBUG if parsed.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    adapter = AcpAdapter(workspace=parsed.workspace)
    adapter.run()


if __name__ == "__main__":
    main()
