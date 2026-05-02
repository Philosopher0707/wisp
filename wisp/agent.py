"""Agent loop — the core planning-acting-observing loop with Ollama tool calling.

Supports:
- Session persistence (save/load across invocations)
- Streaming text output (tokens appear in real-time)
- Interactive REPL mode
- Signal handling (graceful Ctrl+C)
- Non-interactive stdin detection
- Structured logging
- Malformed response guards
"""

import json
import logging
import os
import shutil
import signal
import sys
import weakref
from typing import Optional

# Enable readline for line-editing and history in REPL
try:
    import readline
except ImportError:
    readline = None

from wisp.config import WispConfig
from wisp.ollama_client import OllamaClient, OllamaError
from wisp.stream_events import (
    TokenBatch,
    ToolCallBatch,
    Checkpoint,
    StreamComplete,
    StreamError,
)
from wisp.tools import TOOL_SCHEMAS, execute_tool, ToolError
from wisp.skills import discover_skills
from wisp.session import Session, SessionManager, format_session_preview

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM = """You are Wisp, a helpful coding agent that works in the user's terminal.

You have access to tools that let you read, write, and edit files, run bash commands, and list directories.

## Guidelines
1. Always think step by step. Analyze the problem before writing code.
2. Prefer targeted edits (edit_file) over rewriting entire files.
3. Run tests after making changes to verify correctness.
4. For git operations, use run_bash with appropriate git commands.
5. If a command fails, diagnose the error and try a different approach.
6. Keep explanations concise but clear. Show the user what you're doing.
7. When you're done, summarize what was accomplished.

## Tools available
- read_file: Read file contents (supports offset/limit for large files)
- write_file: Create or overwrite a file
- edit_file: Targeted text replacement (surgical edits)
- run_bash: Execute shell commands
- list_files: Explore directory structure
- web_fetch: Fetch content from URLs (web pages, APIs, documentation)
"""

# ── Signal handling ──────────────────────────────────────────────────

_agent_instances: weakref.WeakSet = weakref.WeakSet()
_old_sigint_handler = None

def _handle_sigint(signum, frame):
    """Mark interruption on all live agent instances so loops exit gracefully."""
    for inst in _agent_instances:
        inst._interrupted = True
    print("\n\n⏹  Interrupted. Finishing current step... (Ctrl+C again to force quit)")
    signal.signal(signal.SIGINT, signal.default_int_handler)

def _install_signal_handler():
    """Register interrupt handler and reset interrupt state on all instances."""
    global _old_sigint_handler
    for inst in _agent_instances:
        inst._interrupted = False
    _old_sigint_handler = signal.signal(signal.SIGINT, _handle_sigint)

def _restore_signal_handler():
    """Restore the previous SIGINT handler."""
    global _old_sigint_handler
    if _old_sigint_handler is not None:
        signal.signal(signal.SIGINT, _old_sigint_handler)
        _old_sigint_handler = None


# ── Helpers ──────────────────────────────────────────────────────────

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


def _is_interactive() -> bool:
    """Detect if stdin is a real terminal (vs pipe/redirect)."""
    return sys.stdin.isatty()


def _prompt_approve(func_name: str) -> bool:
    """Prompt user to approve a tool call. Returns True if approved."""
    if not _is_interactive():
        return True
    try:
        choice = input(f"     Enter to approve, 's' to skip: ").strip().lower()
        return choice != "s"
    except KeyboardInterrupt:
        print()
        return False
    except (EOFError, OSError):
        logger.warning("Stdin unavailable, auto-approving")
        return True


def _prompt_dangerous(func_name: str, reason: str) -> bool:
    """Prompt user to approve a dangerous tool call. Requires typing 'yes'."""
    if not _is_interactive():
        return False
    try:
        print(f"     ⚠️  DANGEROUS: {reason}")
        choice = input(f"     Type 'yes' to approve {func_name}: ").strip().lower()
        return choice == "yes"
    except KeyboardInterrupt:
        print()
        return False
    except (EOFError, OSError):
        logger.warning("Stdin unavailable, auto-declining dangerous command")
        return False


def _setup_readline_history():
    """Load readline history from disk for REPL arrow-key recall."""
    if readline is None:
        return
    histfile = os.path.expanduser("~/.config/wisp/history")
    try:
        os.makedirs(os.path.dirname(histfile), exist_ok=True)
        readline.read_history_file(histfile)
    except (OSError, FileNotFoundError):
        pass
    # Auto-save on exit
    import atexit
    atexit.register(lambda: readline.write_history_file(histfile))

    # Tab completion for slash commands
    def _completer(text, state):
        if not text.startswith("/"):
            return None
        from wisp.commands import all_commands
        names = sorted(
            {f"/{c.name}" for c in all_commands()}
            | {f"/{a}" for c in all_commands() for a in c.aliases}
        )
        matches = [n for n in names if n.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(_completer)
    readline.parse_and_bind("tab: complete")


def _input_line(prompt: str, allow_multiline: bool = True) -> str:
    """Read input from the user with a prompt.

    Interactive mode:
      - Uses readline for arrow-key editing and history.
      - Supports multi-line input: if a line ends with '\\',
        the next line is appended (like bash continuation).
      - Returns '' on EOF/Error.

    Non-interactive mode:
      - Reads raw bytes to survive invalid UTF-8 in piped input.
    """
    if sys.stdin.isatty():
        prompt = f"\033[1m{prompt}\033[0m"
        lines = []
        while True:
            try:
                current_prompt = prompt if not lines else "... "
                line = input(current_prompt)
            except KeyboardInterrupt:
                print()
                raise
            except (EOFError, OSError, UnicodeDecodeError):
                return ""

            stripped = line.rstrip()
            if allow_multiline and stripped.endswith("\\"):
                # Continuation mode: drop the backslash, keep reading
                lines.append(stripped[:-1])
                continue
            lines.append(line)
            break
        return "\n".join(lines)

    # Non-interactive: read raw bytes to survive invalid UTF-8 in piped input
    try:
        data = sys.stdin.buffer.readline()
    except (EOFError, OSError):
        return ""
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").rstrip("\n")


def _print_separator():
    """Print a visual separator between turns."""
    try:
        width = shutil.get_terminal_size().columns
    except OSError:
        width = 50
    print("─" * max(20, min(width, 80)))


def _remove_oldest_turn(messages: list):
    """Remove the oldest logical turn (user + response + tool results).
    
    After removal, ensures the list still starts with a user message
    (or is empty) to maintain conversation integrity.
    
    Safety: never removes the last user message (preserves at least one turn).
    """
    if not messages:
        return

    # Strip any orphaned non-user messages from the start first
    # (these shouldn't exist in normal operation, but guard against corruption)
    while messages and messages[0].get("role") != "user":
        del messages[0]
    if not messages:
        return

    # Find the first user message (now guaranteed to be at index 0)
    start = 0

    # Find the next user message (start of next turn) or end of list
    end = len(messages)
    for i in range(start + 1, len(messages)):
        if messages[i].get("role") == "user":
            end = i
            break

    # SAFETY: Don't remove if this is the last user turn (preserve at least one)
    remaining_user_count = sum(1 for m in messages if m.get("role") == "user")
    if remaining_user_count <= 1:
        return

    # Remove [start, end) — the entire oldest turn
    del messages[start:end]


# ── Agent ────────────────────────────────────────────────────────────

class WispAgent:
    """The main agent loop — orchestrates planning, tool calls, and response generation."""

    def __init__(self, config: Optional[WispConfig] = None, session: Optional[Session] = None):
        self.config = config or WispConfig()
        self.client = OllamaClient(self.config)
        # Auto-detect context window unless user explicitly configured it
        if not self.config._context_tokens_explicit:
            try:
                detected = self.client.get_context_length()
                if detected != self.config.max_context_tokens:
                    logger.info(
                        "Auto-detected context window for %s: %d tokens",
                        self.config.model, detected,
                    )
                    self.config.max_context_tokens = detected
            except OllamaError:
                pass  # Health check will report the real problem later
        self.session_mgr = SessionManager()
        self.session = session
        self.messages: list[dict] = []
        self.max_iterations = self.config.max_iterations
        self._interrupted = False
        self._system_prompt = ""
        self._active_skill: Optional[str] = None
        _agent_instances.add(self)

    def _add_message(self, role: str, content: str, thinking: str = ""):
        """Add a message to the conversation history."""
        msg = {"role": role, "content": content}
        if thinking:
            msg["thinking"] = thinking
        self.messages.append(msg)

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Rough token estimate (chars / chars_per_token) for context budget."""
        total = 0
        for msg in messages:
            # Count content/thinking, but tool messages count content separately below
            if msg.get("role") != "tool":
                for key in ("content", "thinking"):
                    val = msg.get(key, "") or ""
                    total += len(val)
            # Tool call definitions
            for tc in msg.get("tool_calls", []) or []:
                func = tc.get("function", {})
                total += len(func.get("name", ""))
                args = func.get("arguments", {})
                if isinstance(args, str):
                    total += len(args)
                elif isinstance(args, dict):
                    total += len(str(args))
            # Tool results (only count once, here)
            if msg.get("role") == "tool":
                total += len(msg.get("content", "") or "")
        return total // self.config.chars_per_token

    def _trim_context_if_needed(self, system_prompt: str = ""):
        """Trim oldest messages when estimated context exceeds budget.

        Messages form logical turns: user → assistant (+ tool_results).
        We pop the oldest user message and everything that follows it
        until the start of the next turn (next user message).

        Safety: never removes the last user turn (preserves at least one).
        """
        budget = self.config.max_context_tokens
        overhead = self._estimate_tokens([{"content": system_prompt}])

        # Count user messages to know how many we can safely remove
        user_count = sum(1 for m in self.messages if m.get("role") == "user")

        while user_count > 1 and self._estimate_tokens(self.messages) + overhead > budget:
            _remove_oldest_turn(self.messages)
            # Re-count after removal
            user_count = sum(1 for m in self.messages if m.get("role") == "user")

    def _save_session(self):
        """Persist the current session to disk."""
        if self.session is not None:
            self.session.messages = self.messages
            self.session_mgr.save(self.session)

    def _resolve_session(self, session_id: str):
        """Load a session by exact ID or prefix fragment."""
        loaded = self.session_mgr.load(session_id)
        if loaded is not None:
            return loaded
        resolved = self.session_mgr.get_session_id_from_fragment(session_id)
        if resolved:
            return self.session_mgr.load(resolved)
        return None

    def _build_system_prompt(self, skill_name: Optional[str] = None, workspace: Optional[str] = None) -> str:
        """Build the fully assembled system prompt (cached for REPL reuse).

        The result is cached in ``self._system_prompt_cache`` keyed by
        ``(skill_name, workspace)`` so REPL turns don't rebuild it every time.
        """
        ws = workspace or self.config.workspace or "."
        # Allow runtime override from /skill command
        effective_skill = skill_name or self._active_skill
        cache_key = (effective_skill, ws)

        # Return cached version if still valid
        if not hasattr(self, "_system_prompt_cache"):
            self._system_prompt_cache = {}
        cached = self._system_prompt_cache.get(cache_key)
        if cached is not None:
            return cached

        system = DEFAULT_SYSTEM

        # Discover skills ONCE and reuse for both the skills block and the active skill
        skills = discover_skills(ws)
        system += _build_skills_block_from_skills(skills)

        if effective_skill:
            skill = next((s for s in skills if s.name == effective_skill), None)
            if skill:
                system += f"\n\n## Active Skill: {skill.name}\n{skill.description}\n\n{skill.instructions}"
            else:
                logger.warning("Skill '%s' not found in discovered skills", effective_skill)

        # Cache for subsequent calls (e.g., REPL turns)
        self._system_prompt_cache[cache_key] = system
        return system

    def _run_turn_streaming(self, system: str) -> dict:
        """Run one agent turn with streaming text output using batched events.

        Consumes typed events from generate_stream_events:
        - TokenBatch: Batched thinking/content (stdout only)
        - ToolCallBatch: Tool calls with checksum validation
        - Checkpoint: Periodic integrity checkpoints (logged, not shown)
        - StreamComplete: Success with validation hash
        - StreamError: Error occurred

        Returns the assembled response dict for message history.
        """
        self._trim_context_if_needed(system)

        _in_thinking = False
        _last_checkpoint_hash: str | None = None

        try:
            for event in self.client.generate_stream_events(
                system_prompt=system,
                messages=self.messages,
                tools=TOOL_SCHEMAS,
                checkpoint_every=50,
            ):
                if self._interrupted:
                    print()
                    break

                # Handle TokenBatch (thinking/content) - stdout only
                if isinstance(event, TokenBatch):
                    if event.phase == "thinking":
                        if _in_thinking:
                            if self.config.show_thinking:
                                print(event.text, end="", flush=True)
                        else:
                            _in_thinking = True
                            if self.config.show_thinking:
                                print("⏳ Thinking:", end=" ", flush=True)
                                print(event.text, end="", flush=True)
                            else:
                                print("⏳ Thinking...", end="", flush=True)
                    else:  # content
                        if _in_thinking:
                            _in_thinking = False
                            if self.config.show_thinking:
                                print()
                            # When thinking was hidden, just print newline to separate
                            # from the "⏳ Thinking..." indicator, no extra spaces
                            print()
                        print(event.text, end="", flush=True)

                # Handle ToolCallBatch - metadata (not stdout)
                elif isinstance(event, ToolCallBatch):
                    # Tool calls are handled by _execute_loop, just close thinking if open
                    if _in_thinking:
                        _in_thinking = False
                        print()
                    # Store checksum for validation
                    logger.debug("Tool calls received with checksum: %s", event.checksum)

                # Handle Checkpoint - metadata (logged, not shown)
                elif isinstance(event, Checkpoint):
                    _last_checkpoint_hash = event.checkpoint_hash
                    logger.debug(
                        "Checkpoint: thinking=%d chars, content=%d chars, tokens=%d",
                        len(event.accumulated_thinking),
                        len(event.accumulated_content),
                        event.token_count,
                    )

                # Handle StreamComplete - validate and finish
                elif isinstance(event, StreamComplete):
                    if _in_thinking:
                        _in_thinking = False
                        print()
                    # Validate final hash against our own recomputation
                    expected = Checkpoint.compute_hash(event.final_thinking, event.final_content)
                    if event.validation_hash != expected:
                        logger.warning("StreamComplete hash mismatch — text may be corrupted")
                    # Log checkpoint info for debugging
                    logger.debug(
                        "Stream complete: thinking=%d chars, content=%d chars, hash=%s",
                        len(event.final_thinking),
                        len(event.final_content),
                        event.validation_hash,
                    )
                    # Add trailing newline if needed
                    if event.final_content and not event.final_content.endswith("\n"):
                        print()
                    break

                # Handle StreamError
                elif isinstance(event, StreamError):
                    if _in_thinking:
                        _in_thinking = False
                        print()
                    print(f"\n✗ Stream error ({event.error_type}): {event.message}")
                    return {}

        except OllamaError as e:
            print(f"\n✗ Ollama Error: {e}")
            return {}
        except KeyboardInterrupt:
            print("\n⏹  Interrupted by user.")
            return {}
        except Exception as e:
            logger.error("Unexpected error in streaming turn: %s", e, exc_info=True)
            print(f"\n✗ Unexpected error: {e}")
            return {}

        if _in_thinking:
            print()

        # Retrieve the assembled response
        response = getattr(self.client, "stream_response", None) or {}
        return response

    def _run_tool_calls(self, tool_calls: list, workspace: str, auto_approve: bool) -> list[dict]:
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

            print(f"  🛠  {func_name}({_args_preview(func_args)})")

            # Dangerous command guard: always prompt, even with auto_approve
            danger_reason = None
            if func_name == "run_bash":
                from wisp.tools import check_dangerous_command
                danger_reason = check_dangerous_command(func_args.get("command", ""))

            if danger_reason:
                if not _is_interactive():
                    print(f"  ⚠️  Blocked dangerous command ({danger_reason})")
                    all_results.append({
                        "role": "tool",
                        "content": f"[Blocked: dangerous command — {danger_reason}]",
                        "name": func_name,
                    })
                    continue
                approved = _prompt_dangerous(func_name, danger_reason)
            else:
                approved = auto_approve or _prompt_approve(func_name)

            if not approved:
                print(f"  ⏭  Skipped {func_name}")
                all_results.append({
                    "role": "tool",
                    "content": f"[User skipped {func_name}]",
                    "name": func_name,
                })
                continue

            # Subagent spawn: handled specially (needs parent agent reference)
            if func_name == "spawn_subagent":
                result = self._spawn_subagent(func_args, workspace)
            else:
                try:
                    result = execute_tool(func_name, func_args, workspace)
                except ToolError as e:
                    result = f"Error: {e}"
                    logger.warning("Tool %s failed: %s", func_name, e)
                except Exception as e:
                    result = f"Unexpected error: {e}"
                    logger.error("Unexpected error in tool %s: %s", func_name, e, exc_info=True)

            if len(result) > 8000:
                result = result[:8000] + f"\n... [truncated {len(result)} total chars]"

            all_results.append({"role": "tool", "content": result, "name": func_name})
            preview = result[:200].replace("\n", " ")
            if len(result) > 200:
                preview += "..."
            print(f"     → {preview}")

        return all_results

    def _spawn_subagent(self, args: dict, workspace: str) -> str:
        """Handle the spawn_subagent tool by delegating to SubagentRunner."""
        from wisp.subagent import SubagentRunner, SubagentContract

        # Prevent infinite recursion
        depth = getattr(self, "_subagent_depth", 0)
        if depth >= 1:
            return "[Error: subagents cannot spawn subagents (max depth = 1)]"

        contract = SubagentContract(
            task=args.get("task", ""),
            tools=args.get("tools", ["all"]),
            max_iterations=int(args.get("max_iterations", 15)),
            timeout_seconds=int(args.get("timeout_seconds", 120)),
            output_format=args.get("output_format", "text"),
            workspace=workspace,
        )

        runner = SubagentRunner(self)
        print(f"  🧬 Spawning subagent (timeout={contract.timeout_seconds}s, iterations={contract.max_iterations})")
        result = runner.spawn(contract)

        status = "✓" if result.success else "✗"
        if result.timed_out:
            status = "⏱"

        lines = [
            f"{status} Subagent result (elapsed={result.elapsed_seconds:.1f}s, iterations={result.iterations_used})",
            "",
            result.output,
        ]
        return "\n".join(lines)

    def run(self, prompt: str, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Execute the agent (single-shot mode) with streaming output."""
        _install_signal_handler()

        if not self.client.check_health():
            _restore_signal_handler()
            return

        # ── Session setup ──────────────────────────────────────────
        if session_id:
            loaded = self._resolve_session(session_id)
            if loaded is None:
                print(f"✗ Session '{session_id}' not found.")
                print(f"  Run 'wisp session list' to see available sessions.")
                return
            self.session = loaded
            self.messages = list(loaded.messages)
            session_id = self.session.id
            print(f"📋 Continuing session: {self.session.id}")
            if loaded.title:
                print(f"   Title: {loaded.title}")
            print(f"   Messages so far: {len(self.messages)}")
            last_user = None
            for m in reversed(self.messages):
                if m.get("role") == "user" and m.get("content", "").strip():
                    last_user = m["content"]
                    break
            if last_user:
                preview = last_user[:100].replace("\n", " ")
                if len(last_user) > 100:
                    preview += "..."
                print(f"   Last prompt: {preview}")
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
            # Skill was already loaded in _build_system_prompt, just report status
            from wisp.skills import find_skill
            skill = find_skill(skill_name, self.config.workspace or ".")
            if skill:
                print(f"🧠 Loaded skill: {skill.name} — {skill.description}")
            else:
                print(f"⚠ Skill '{skill_name}' not found. Running without it.")

        print(f"🔮 Wisp (model: {self.config.model})")
        print()

        try:
            self._add_message("user", prompt)
            self._execute_loop(system, self.config.workspace or ".", self.config.auto_approve)
        except KeyboardInterrupt:
            print("\n⏹  Interrupted.")
        finally:
            # ── Done ───────────────────────────────────────────────────
            if self.session and self.session.id and not self._interrupted:
                print(f"\n📋 Session: {self.session.id}")
            _restore_signal_handler()

    def repl(self, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Interactive REPL — continuous conversation until the user exits."""
        _install_signal_handler()

        if not self.client.check_health():
            _restore_signal_handler()
            return

        # ── Session setup ──────────────────────────────────────────
        if session_id:
            loaded = self._resolve_session(session_id)
            if loaded is None:
                print(f"✗ Session '{session_id}' not found.")
                return
            self.session = loaded
            self.messages = list(loaded.messages)
            session_id = self.session.id
        else:
            self.session = Session.create(
                model=self.config.model,
                workspace=self.config.workspace or ".",
                first_prompt="REPL session",
            )
            self.messages = []

        ws = self.config.workspace or "."

        _setup_readline_history()

        msg_count = len(self.messages)
        print(f"🔮 Wisp (model: {self.config.model})")
        print(f"   Session: {self.session.id}")
        if msg_count:
            print(f"   History: {msg_count} messages so far")
        if skill_name:
            print(f"   Skill: {skill_name}")
        print()
        print("Type /help for commands, 'exit', or press Ctrl+C to end.")
        print("Tip: end a line with \\ to continue on the next line.")
        print()

        self._interrupted = False
        try:
            while not self._interrupted:
                try:
                    user_input = _input_line("➜ ")
                except KeyboardInterrupt:
                    print("\n⏹  Exiting.")
                    break

                cmd = user_input.strip()
                if not cmd:
                    if not _is_interactive():
                        break
                    continue

                # ── Slash commands (local directives, never sent to LLM) ──
                from wisp.commands import dispatch, ExitREPL
                try:
                    if dispatch(cmd, self):
                        # Known or unknown /command consumed; continue loop
                        continue
                except ExitREPL:
                    print("👋 Goodbye.")
                    break

                # Legacy non-slash commands (backward compatibility)
                if cmd in ("exit", "quit"):
                    print("👋 Goodbye.")
                    break
                if cmd in ("help", "?"):
                    dispatch("/help", self)
                    continue

                # Update session title on first meaningful prompt
                if self.session and (
                    not self.session.title
                    or self.session.title in ("REPL session", "(untitled)")
                ):
                    self.session.title = cmd[:60].strip()

                # Print blank line so response breathes after the prompt
                print()

                try:
                    # Rebuild system prompt each turn so /skill and /model take effect immediately
                    system = self._build_system_prompt(skill_name)
                    self._add_message("user", cmd)
                    self._execute_loop(system, ws, self.config.auto_approve)
                except KeyboardInterrupt:
                    print("\n⏹  Turn interrupted. Type 'exit' to quit or continue chatting.")
                    # Session was saved by _execute_loop's finally block
                    # Reset interrupt flag so REPL loop continues
                    self._interrupted = False
                    continue

                # Visual turn separator (only if not empty turn)
                if not self._interrupted:
                    _print_separator()

            print()
            if self.session:
                print(f"📋 Session {self.session.id} saved.")
                print(f"   Continue with: wisp repl -S {self.session.id}")
        finally:
            _restore_signal_handler()

    def _execute_loop(self, system: str, workspace: str, auto_approve: bool) -> None:
        """Execute the agent loop for one user turn."""
        self._interrupted = False  # Reset for each new turn
        try:
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
                    # Always save, even if content is empty (preserves conversation state)
                    self._save_session()
                    break

                # Tool call turn - use _add_message for consistency, then attach tool_calls
                self._add_message("assistant", content or "", thinking)
                if tool_calls:
                    self.messages[-1]["tool_calls"] = tool_calls
                
                print()  # blank line before tool calls
                results = self._run_tool_calls(tool_calls, workspace, auto_approve)
                self.messages.extend(results)
        except KeyboardInterrupt:
            print("\n⏹  Turn interrupted by user.")
            # Let finally block save session, then return gracefully
        finally:
            # Always save session on exit (single save point), even if interrupted mid-tool-call
            self._save_session()


def _args_preview(args: dict) -> str:
    """Short one-line preview of tool arguments."""
    parts = []
    path = args.get("path", args.get("command", ""))
    if path:
        s = str(path)
        parts.append(s[:60])
    content = args.get("content", "")
    if content:
        parts.append(f"({len(content)} chars)")
    return ", ".join(parts) if parts else "..."
