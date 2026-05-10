"""Backward-compatible WispAgent — thin wrapper around WispAgentCore + CLITransport.

All I/O-specific code (printing, input, colors, signals) lives in
wisp.transport.cli. This module re-exports the helpers that other parts
of the codebase depend on and adds the synchronous run()/repl() APIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

# Re-export helpers from transport layer for backward compatibility
from wisp.transport.cli import (
    _is_interactive,
    _prompt_approve,
    _prompt_dangerous,
    _setup_readline_history,
    _input_line,
    _print_separator,
    _args_preview,
    _install_signal_handler as _cli_install_signal,
    _restore_signal_handler as _cli_restore_signal,
)

# Core logic (pure, no I/O)
from wisp.core.agent import (
    WispAgentCore,
    _remove_oldest_turn,
    _generate_agent_id,
    DEFAULT_SYSTEM,
    _coerce_tool_data,
)

from wisp.transport.cli import CLITransport
from wisp.colors import success, error, warning, info, dim, accent
from wisp.session import Session, SessionManager
from wisp.skills import discover_skills, find_skill
from wisp.tools import execute_tool, ToolError

logger = logging.getLogger(__name__)

# ── Standalone helpers (re-exported for tests & other modules) ─────

def _parse_tool_call(response: dict) -> Optional[list[dict]]:
    """Extract tool calls from an Ollama chat response."""
    msg = response.get("message", {})
    if not isinstance(msg, dict):
        logger.warning("Malformed response: message is not a dict: %s", type(msg))
        return None
    tool_calls = msg.get("tool_calls")
    if tool_calls and isinstance(tool_calls, list):
        return tool_calls
    return None


def _build_skills_block(workspace: str) -> str:
    """Build a system prompt block listing available skills (discovers skills internally)."""
    skills = discover_skills(workspace)
    return _build_skills_block_from_skills(skills)


def _build_skills_block_from_skills(skills: list) -> str:
    """Build a system prompt block from an already-discovered skills list."""
    if not skills:
        return ""
    lines = ["\n## Available Skills", "You can invoke any of these skills when relevant:"]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}")
    lines.append("To invoke a skill, mention its name and follow its instructions.")
    return "\n".join(lines)


# ── WispAgent (backward-compatible wrapper) ──────────────────────────

class WispAgent(WispAgentCore):
    """The main agent — synchronous API, backward compatible with all existing code.

    Internally delegates logic to WispAgentCore and I/O to CLITransport.
    ServerAgent and SubagentRunner subclass this.
    """

    def __init__(
        self,
        config: Optional[object] = None,
        session: Optional[object] = None,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
    ):
        super().__init__(config=config, session=session, agent_id=agent_id, role=role)

    # ── Synchronous public API ─────────────────────────────────────

    def run(self, prompt: str, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Execute the agent (single-shot mode) with streaming output."""
        _cli_install_signal()

        if not self.client.check_health():
            _cli_restore_signal()
            return

        # ── Session setup ──────────────────────────────────────────
        if session_id:
            loaded = self._resolve_session(session_id)
            if loaded is None:
                print(error(f"✗ Session '{session_id}' not found."))
                print(dim("  Run 'wisp session list' to see available sessions."))
                return
            self.session = loaded
            self.messages = list(loaded.messages)
            session_id = self.session.id
            print(info(f"📋 Continuing session: {self.session.id}"))
            if loaded.title:
                print(f"   {dim('Title:')} {loaded.title}")
            print(f"   {dim('Model:')} {self.config.model}")
            if loaded.model and loaded.model != self.config.model:
                print(warning(f"   ⚠️  Session was created with model '{loaded.model}'. Now using '{self.config.model}'."))
            print(f"   {dim('Messages so far:')} {len(self.messages)}")
            last_user = None
            for m in reversed(self.messages):
                if m.get("role") == "user" and m.get("content", "").strip():
                    last_user = m["content"]
                    break
            if last_user:
                preview = last_user[:100].replace("\n", " ")
                if len(last_user) > 100:
                    preview += "..."
                print(f"   {dim('Last prompt:')} {preview}")
            print()
        else:
            self.session = Session.create(
                model=self.config.model,
                workspace=self.config.workspace or ".",
                first_prompt=prompt,
            )
            self.messages = []

        # Handle slash commands even in single-shot mode
        if prompt.strip().startswith("/"):
            from wisp.commands import dispatch, ExitREPL
            try:
                if dispatch(prompt.strip(), self):
                    return
            except ExitREPL:
                return

        system = self._build_system_prompt(skill_name, workspace=self.config.workspace)

        if skill_name:
            skill = find_skill(skill_name, self.config.workspace or ".")
            if skill:
                print(accent(f"🧠 Loaded skill: {skill.name} — {skill.description}"))
            else:
                print(warning(f"⚠ Skill '{skill_name}' not found. Running without it."))

        print(info(f"🔮 Wisp (model: {self.config.model})"))
        print()

        try:
            self._add_message("user", self._expand_continuation(prompt))
            self._execute_loop(system, self.config.workspace or ".", self.config.auto_approve)
        finally:
            self._save_session_summary()
            self.mcp.shutdown()
            self.lsp.shutdown_all()
            _cli_restore_signal()

    def repl(self, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Interactive REPL — continuous conversation until the user exits."""
        transport = CLITransport(self)
        transport.repl(skill_name, session_id)

    def _run_turn_streaming(self, system: str) -> dict:
        """Backward compat: stream to stdout while accumulating."""
        _in_thinking = False
        _content_started = False
        for event in self._run_turn_streaming_events(system):
            if event.type == "thinking":
                # Suppress trailing thinking tokens after content has started
                if _content_started:
                    continue
                if not _in_thinking:
                    _in_thinking = True
                    if self.config.show_thinking:
                        print(dim("⏳ Thinking: "), end="", flush=True)
                    else:
                        print(dim("⏳ Thinking..."), end="", flush=True)
                if self.config.show_thinking:
                    print(event.text, end="", flush=True)
            elif event.type == "content":
                _content_started = True
                if _in_thinking:
                    _in_thinking = False
                    if self.config.show_thinking:
                        print()
                    print()
                print(event.text, end="", flush=True)
            elif event.type == "error":
                print(error(f"\n✗ {event.data.get('message', '')}"))
        if _in_thinking:
            print()
        return getattr(self.client, "stream_response", None) or {}

    # ── Internal sync execution loop ───────────────────────────────

    def _execute_loop(self, system: str, workspace: str, auto_approve: bool) -> None:
        """Execute the agent loop for one user turn."""
        self._interrupted = False
        try:
            # Auto-compact if session is getting large
            compact_event = self._maybe_compact_session()
            if compact_event is not None:
                # In backward-compat mode, just log compaction events
                logger.info("Session compaction: %s", compact_event.data.get("message", ""))

            for iteration in range(1, self.max_iterations + 1):
                if self._interrupted:
                    break

                # ── Generate with streaming ──────────────────────────
                response = self._run_turn_streaming(system)

                if not response:
                    break

                # Validate response
                if not isinstance(response, dict):
                    logger.error("Expected dict from turn, got %s", type(response))
                    break

                content = ""
                thinking = ""
                msg = response.get("message", {})
                if isinstance(msg, dict):
                    content = msg.get("content", "") or ""
                    thinking = msg.get("thinking", "") or ""
                tool_calls = _parse_tool_call(response)

                # If no tool calls, just a text response — done for this turn
                if not tool_calls:
                    if content:
                        self._add_message("assistant", content, thinking)
                    # Always save, even if content is empty
                    self._save_session()
                    break

                # Tool call turn
                self._add_message("assistant", content or "", thinking)
                if tool_calls:
                    self.messages[-1]["tool_calls"] = tool_calls

                print()  # blank line before tool calls
                results = self._run_tool_calls(tool_calls, workspace, auto_approve)
                self.messages.extend(results)
        else:
            # Hit max_iterations without a final answer
            print(error(f"\n✗ Reached max iterations ({self.max_iterations}) without completing the task."))

        except KeyboardInterrupt:
            print(error("\n⏹  Turn interrupted by user."))
        finally:
            # Always save session on exit
            self._save_session()

    # ── Sync tool execution (backward compat) ──────────────────────

    def _run_tool_calls(self, tool_calls: list, workspace: str, auto_approve: bool, quiet: bool = False) -> list[dict]:
        """Execute a batch of tool calls, return result messages for the model."""
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

            if not func_name:
                continue

            if isinstance(func_args, str):
                try:
                    func_args = json.loads(func_args)
                except json.JSONDecodeError:
                    logger.warning("Malformed tool arguments for %s: %.200s", func_name, func_args)
                    func_args = {}
            if not isinstance(func_args, dict):
                logger.warning("Tool arguments for %s are not a dict: %s", func_name, type(func_args).__name__)
                func_args = {}

            if not quiet:
                print(dim(f"  🛠  {func_name}({_args_preview(func_args)})"))

            # Dangerous command guard
            danger_reason = None
            if func_name == "run_bash":
                from wisp.tools import check_dangerous_command
                danger_reason = check_dangerous_command(func_args.get("command", ""))

            if danger_reason:
                if quiet or not _is_interactive():
                    if not quiet:
                        print(warning(f"  ⚠️  Blocked dangerous command ({danger_reason})"))
                    all_results.append({
                        "role": "tool",
                        "content": f"[Blocked: dangerous command — {danger_reason}]",
                        "name": func_name,
                    })
                    continue
                approved = _prompt_dangerous(func_name, danger_reason)
            else:
                approved = auto_approve or (not quiet and _prompt_approve(func_name))

            if not approved:
                if not quiet:
                    print(dim(f"  ⏭  Skipped {func_name}"))
                all_results.append({
                    "role": "tool",
                    "content": f"[User skipped {func_name}]",
                    "name": func_name,
                })
                continue

            # ── Checkpoint before write operations ──
            if func_name in ("write_file", "edit_file", "edit_file_multi"):
                try:
                    from wisp.checkpoints import CheckpointManager
                    cpm = CheckpointManager(workspace)
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        cp = asyncio.run(cpm.auto_checkpoint(func_name))
                    else:
                        cp = loop.run_until_complete(cpm.auto_checkpoint(func_name))
                    logger.debug("Checkpoint created: %s (%s)", cp.id, func_name)
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning("Checkpoint creation failed for %s: %s", func_name, e)

            # Execute tool
            if func_name == "spawn_subagent":
                result = self._spawn_subagent(func_args, workspace)
            elif self._is_mcp_tool(func_name):
                try:
                    result = self.mcp.call_tool(func_name, func_args)
                    if len(result) > 8000:
                        result = result[:8000] + f"\n... [truncated {len(result)} total chars]"
                except Exception as e:
                    result = f"MCP error: {e}"
                    logger.warning("MCP tool %s failed: %s", func_name, e)
            else:
                try:
                    result = execute_tool(func_name, func_args, workspace, max_data_chars=8000, file_lock=self.file_lock)
                except ToolError as e:
                    result = f"Error: {e}"
                    logger.warning("Tool %s failed: %s", func_name, e)
                except Exception as e:
                    result = f"Unexpected error: {e}"
                    logger.error("Unexpected error in tool %s: %s", func_name, e, exc_info=True)

            # Auto-diagnose errors
            if isinstance(result, str) and ("Error" in result or "FAILED" in result or "Traceback" in result):
                from wisp.error_diagnosis import diagnose_tool_error
                diag = diagnose_tool_error(func_name, func_args, result, workspace)
                if diag.error_type != "None":
                    logger.debug("Diagnosis for %s: %s", func_name, diag.format())
                    if not hasattr(self, "_pending_diagnoses"):
                        self._pending_diagnoses = []
                    self._pending_diagnoses.append(diag)

            # Invalidate system prompt cache if memory changed
            if func_name == "remember":
                self._invalidate_system_prompt_cache()

            # Extract preview
            preview = result[:200].replace("\n", " ")
            if func_name != "spawn_subagent" and result.startswith("{"):
                try:
                    parsed = json.loads(result)
                    data = _coerce_tool_data(parsed.get("data", result))
                    preview = data[:200].replace("\n", " ")
                except (json.JSONDecodeError, KeyError):
                    pass

            all_results.append({"role": "tool", "content": result, "name": func_name})
            if len(preview) > 200:
                preview += "..."
            if not quiet:
                print(dim(f"     → {preview}"))

        return all_results
