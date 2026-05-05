"""Server-aware agent for Wisp Cloud.

Extends WispAgent to stream events over WebSocket and request tool approvals
from a remote client instead of the local terminal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Optional

from wisp.agent import WispAgent
from wisp.stream_events import (
    TokenBatch,
    ToolCallBatch,
    Checkpoint,
    StreamComplete,
    StreamError,
)
from wisp.tools import execute_tool, ToolError

logger = logging.getLogger(__name__)


class PendingApproval:
    """Represents a tool call waiting for client approval."""

    def __init__(self, call_id: str, name: str, arguments: dict):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.event = threading.Event()
        self.approved: bool = False
        self.denied_reason: Optional[str] = None


class ServerAgent(WispAgent):
    """WispAgent adapted for cloud server + Android client architecture.

    Streams tokens/tool-calls to an async callback and waits for client
    approval before executing tools.
    """

    def __init__(self, config, send_callback, loop: asyncio.AbstractEventLoop):
        super().__init__(config)
        self._send_callback = send_callback
        self._loop = loop
        self._pending_approvals: dict[str, PendingApproval] = {}
        self._approval_lock = threading.Lock()
        self._session_id: Optional[str] = None

    def _send(self, msg: dict):
        """Thread-safe send to the async callback."""
        try:
            asyncio.run_coroutine_threadsafe(self._send_callback(msg), self._loop)
        except Exception as e:
            logger.warning("Failed to send message to client: %s", e)

    def _run_turn_streaming(self, system: str) -> dict:
        """Override to stream tokens to the WebSocket client instead of stdout."""
        self._trim_context_if_needed(system)

        _in_thinking = False
        _last_checkpoint_hash: Optional[str] = None

        try:
            for event in self.client.generate_stream_events(
                system_prompt=system,
                messages=self.messages,
                tools=self._get_tool_schemas(),
                checkpoint_every=50,
            ):
                if self._interrupted:
                    break

                if isinstance(event, TokenBatch):
                    phase = "thinking" if event.phase == "thinking" else "content"
                    if event.phase == "thinking":
                        _in_thinking = True
                    else:
                        _in_thinking = False
                    self._send({
                        "type": "token",
                        "phase": phase,
                        "text": event.text,
                    })

                elif isinstance(event, ToolCallBatch):
                    if _in_thinking:
                        _in_thinking = False
                    logger.debug("Tool calls received with checksum: %s", event.checksum)
                    # Tool calls are handled by _execute_loop after this returns

                elif isinstance(event, Checkpoint):
                    _last_checkpoint_hash = event.checkpoint_hash
                    logger.debug(
                        "Checkpoint: thinking=%d chars, content=%d chars, tokens=%d",
                        len(event.accumulated_thinking),
                        len(event.accumulated_content),
                        event.token_count,
                    )

                elif isinstance(event, StreamComplete):
                    if _in_thinking:
                        _in_thinking = False
                    expected = Checkpoint.compute_hash(event.final_thinking, event.final_content)
                    if event.validation_hash != expected:
                        logger.warning("StreamComplete hash mismatch")
                    self._send({"type": "checkpoint", "hash": event.validation_hash})
                    break

                elif isinstance(event, StreamError):
                    if _in_thinking:
                        _in_thinking = False
                    self._send({
                        "type": "error",
                        "error_type": event.error_type,
                        "message": event.message,
                    })
                    return {}

        except Exception as e:
            logger.error("Unexpected error in streaming turn: %s", e, exc_info=True)
            self._send({"type": "error", "error_type": "unexpected", "message": str(e)})
            return {}

        # Retrieve the assembled response
        response = getattr(self.client, "stream_response", None) or {}
        return response

    def _run_tool_calls(self, tool_calls: list, workspace: str, auto_approve: bool) -> list[dict]:
        """Override to request approval from the remote client before executing tools."""
        all_results = []
        for tc in tool_calls:
            if self._interrupted:
                break

            func = tc.get("function", {})
            if not isinstance(func, dict):
                logger.warning("Malformed tool call: %s", tc)
                continue

            func_name = func.get("name", "")
            func_args = func.get("arguments", {})
            call_id = tc.get("id", f"call_{int(time.time()*1000)}")

            if not func_name:
                continue

            if isinstance(func_args, str):
                try:
                    func_args = json.loads(func_args)
                except json.JSONDecodeError:
                    func_args = {}
            if not isinstance(func_args, dict):
                func_args = {}

            # Dangerous command guard
            danger_reason = None
            if func_name == "run_bash":
                from wisp.tools import check_dangerous_command
                danger_reason = check_dangerous_command(func_args.get("command", ""))

            if danger_reason:
                self._send({
                    "type": "tool_blocked",
                    "id": call_id,
                    "name": func_name,
                    "arguments": func_args,
                    "reason": danger_reason,
                })
                all_results.append({
                    "role": "tool",
                    "content": f"[Blocked: dangerous command — {danger_reason}]",
                    "name": func_name,
                    "tool_call_id": call_id,
                })
                continue

            # Send tool call to client for approval
            self._send({
                "type": "tool_call",
                "id": call_id,
                "name": func_name,
                "arguments": func_args,
            })

            pending = PendingApproval(call_id, func_name, func_args)
            with self._approval_lock:
                self._pending_approvals[call_id] = pending

            # Wait for client response (with 5-minute timeout)
            pending.event.wait(timeout=300)

            with self._approval_lock:
                self._pending_approvals.pop(call_id, None)

            if not pending.event.is_set():
                self._send({
                    "type": "tool_result",
                    "id": call_id,
                    "output": "[Approval timed out after 5 minutes]",
                    "error": "timeout",
                })
                all_results.append({
                    "role": "tool",
                    "content": "[Approval timed out]",
                    "name": func_name,
                    "tool_call_id": call_id,
                })
                continue

            if not pending.approved:
                reason = pending.denied_reason or "User denied"
                self._send({
                    "type": "tool_result",
                    "id": call_id,
                    "output": f"[{reason}]",
                    "error": "denied",
                })
                all_results.append({
                    "role": "tool",
                    "content": f"[{reason}]",
                    "name": func_name,
                    "tool_call_id": call_id,
                })
                continue

            # Execute the tool
            self._send({"type": "tool_executing", "id": call_id, "name": func_name})

            if func_name == "spawn_subagent":
                result = self._spawn_subagent(func_args, workspace)
            elif self._is_mcp_tool(func_name):
                try:
                    result = self.mcp.call_tool(func_name, func_args)
                    if len(result) > 8000:
                        result = result[:8000] + f"\n... [truncated {len(result)} total chars]"
                except Exception as e:
                    result = f"MCP error: {e}"
            else:
                try:
                    result = execute_tool(func_name, func_args, workspace, max_data_chars=8000, file_lock=self.file_lock)
                except ToolError as e:
                    result = f"Error: {e}"

            self._send({
                "type": "tool_result",
                "id": call_id,
                "output": result,
                "error": None,
            })

            all_results.append({
                "role": "tool",
                "content": result,
                "name": func_name,
                "tool_call_id": call_id,
            })

        return all_results

    def approve_tool(self, call_id: str, approved: bool, reason: Optional[str] = None):
        """Called by the WebSocket handler when the client approves/denies a tool."""
        with self._approval_lock:
            pending = self._pending_approvals.get(call_id)
            if pending is None:
                logger.warning("Approval received for unknown call_id: %s", call_id)
                return False
            pending.approved = approved
            pending.denied_reason = reason
            pending.event.set()
            return True

    def _maybe_compact_session(self):
        """Override to notify the remote client when auto-compaction happens.

        Compaction now triggers purely on token usage (message-count gate
        removed). Warns the client on 2nd+ compaction.
        """
        if not self.config.auto_compact:
            return
        if not self.session:
            return

        system = self._build_system_prompt()
        overhead = self._estimate_tokens([{"content": system}])
        msg_tokens = self._estimate_tokens(self.messages)
        budget = self.config.max_context_tokens
        token_pct = (msg_tokens + overhead) / budget * 100 if budget else 0

        if token_pct < self.config.compact_threshold_tokens:
            return

        compaction_count = len(self.session.compaction_history)

        # Warn client on 2nd+ compaction
        if compaction_count >= 1:
            self._send({
                "type": "status",
                "message": (
                    "⚠️ This session has already been compacted once. "
                    "Accuracy may degrade with repeated compaction. "
                    "Start a new session if responses become less coherent."
                ),
            })

        self._send({
            "type": "status",
            "message": f"Compacting session (~{token_pct:.0f}% context)...",
        })

        result = self.session.compact(
            keep_recent=self.config.compact_keep_recent,
            chars_per_token=self.config.chars_per_token,
        )

        if result.get("compacted"):
            self.messages = list(self.session.messages)
            saved = result["before_count"] - result["after_count"]
            self._send({
                "type": "status",
                "message": f"Compacted: {result['before_count']} → {result['after_count']} messages ({saved} removed)",
            })
            logger.info(
                "Server auto-compacted session %s: %d → %d messages",
                self.session.id, result["before_count"], result["after_count"],
            )
        else:
            self._send({
                "type": "status",
                "message": "Compaction skipped: not enough messages to summarize.",
            })

    def run_server(self, prompt: str, session_id: Optional[str] = None):
        """Run the agent in server mode (single-shot)."""
        if not self.client.check_health():
            self._send({"type": "error", "message": "Ollama is not reachable"})
            return

        if session_id:
            loaded = self._resolve_session(session_id)
            if loaded is None:
                self._send({"type": "error", "message": f"Session '{session_id}' not found"})
                return
            self.session = loaded
            self.messages = list(loaded.messages)
            self._session_id = self.session.id
        else:
            from wisp.session import Session
            self.session = Session.create(
                model=self.config.model,
                workspace=self.config.workspace or ".",
                first_prompt=prompt,
            )
            self.messages = []
            self._session_id = self.session.id

        system = self._build_system_prompt(None, workspace=self.config.workspace)
        self._add_message("user", prompt)

        try:
            self._execute_loop(system, self.config.workspace or ".", auto_approve=False)
        except Exception as e:
            logger.error("Error in server agent loop: %s", e, exc_info=True)
            self._send({"type": "error", "message": str(e)})
        finally:
            self._save_session_summary()
            self.mcp.shutdown()
            self._send({"type": "complete", "session_id": self._session_id})
