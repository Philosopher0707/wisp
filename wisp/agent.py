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
import signal
import sys
import weakref
from typing import Optional

# Enable readline for line-editing and history in REPL
try:
    import readline  # noqa: F401
except ImportError:
    pass

from wisp.config import WispConfig
from wisp.ollama_client import OllamaClient, OllamaError
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

def _handle_sigint(signum, frame):
    """Mark interruption on all live agent instances so loops exit gracefully."""
    for inst in _agent_instances:
        inst._interrupted = True
    print("\n\n⏹  Interrupted. Finishing current step... (Ctrl+C again to force quit)")
    signal.signal(signal.SIGINT, signal.default_int_handler)

def _install_signal_handler():
    """Register interrupt handler and reset interrupt state on all instances."""
    for inst in _agent_instances:
        inst._interrupted = False
    signal.signal(signal.SIGINT, _handle_sigint)


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
    """Build a system prompt block listing available skills."""
    skills = discover_skills(workspace)
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


def _input_line(prompt: str) -> str:
    """Read a line from the user with a prompt. Returns '' on EOF/Error."""
    if sys.stdin.isatty():
        prompt = f"\033[1m{prompt}\033[0m"
    try:
        return input(prompt)
    except KeyboardInterrupt:
        print()
        raise
    except (EOFError, OSError):
        return ""


def _print_separator():
    """Print a visual separator between turns."""
    print("─" * 50)


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
        self.session_mgr = SessionManager()
        self.session = session
        self.messages: list[dict] = []
        self.max_iterations = self.config.max_iterations
        self._interrupted = False
        self._system_prompt = ""
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
        cache_key = (skill_name, ws)

        # Return cached version if still valid
        if not hasattr(self, "_system_prompt_cache"):
            self._system_prompt_cache = {}
        cached = self._system_prompt_cache.get(cache_key)
        if cached is not None:
            return cached

        system = DEFAULT_SYSTEM
        system += _build_skills_block(ws)

        if skill_name:
            from wisp.skills import find_skill
            skill = find_skill(skill_name, ws)
            if skill:
                system += f"\n\n## Active Skill: {skill.name}\n{skill.description}\n\n{skill.instructions}"

        # Cache for subsequent calls (e.g., REPL turns)
        self._system_prompt_cache[cache_key] = system
        return system

    def _run_turn_streaming(self, system: str) -> dict:
        """Run one agent turn with streaming text output.

        Consumes (text, kind) tuples from generate_stream.
        Default: hides reasoning trace, shows compact indicator only.
        With show_thinking=True: shows full trace in a dim section.
        Returns the assembled response dict for message history.
        """
        self._trim_context_if_needed(system)
        _in_thinking = False
        try:
            for text, kind in self.client.generate_stream(
                system_prompt=system,
                messages=self.messages,
                tools=TOOL_SCHEMAS,
            ):
                if self._interrupted:
                    print()
                    break
                if not text:
                    continue

                if kind == "thinking":
                    if _in_thinking:
                        # Already showing indicator / trace — just print thinking text
                        if self.config.show_thinking:
                            print(text, end="", flush=True)
                        # In default mode: no spinner, just silence (already showing indicator)
                    else:
                        # First thinking token → show indicator
                        _in_thinking = True
                        if self.config.show_thinking:
                            print("⏳ Thinking:", end=" ", flush=True)
                            print(text, end="", flush=True)
                        else:
                            print("⏳ Thinking...", end="", flush=True)

                else:  # content
                    if _in_thinking:
                        _in_thinking = False
                        if self.config.show_thinking:
                            print()  # close the thinking block
                        else:
                            print()
                        print("   ", end="", flush=True)
                    print(text, end="", flush=True)

        except OllamaError as e:
            print(f"\n✗ Ollama Error: {e}")
            return {}
        except Exception as e:
            logger.error("Unexpected error in streaming turn: %s", e, exc_info=True)
            print(f"\n✗ Unexpected error: {e}")
            return {}

        if _in_thinking:
            print()  # close any open thinking indicator

        # Retrieve the assembled response (contains full content + thinking)
        response = getattr(self.client, "stream_response", None) or {}
        msg = response.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", "") or ""
            # Only add trailing newline if content doesn't already end with one
            if content and not content.endswith("\n"):
                print()
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

            if not auto_approve and not _prompt_approve(func_name):
                print(f"  ⏭  Skipped {func_name}")
                all_results.append({
                    "role": "tool",
                    "content": f"[User skipped {func_name}]",
                    "name": func_name,
                })
                continue

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

    def run(self, prompt: str, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Execute the agent (single-shot mode) with streaming output."""
        _install_signal_handler()

        if not self.client.check_health():
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

        self._add_message("user", prompt)
        self._execute_loop(system, self.config.workspace or ".", self.config.auto_approve)

        # ── Done ───────────────────────────────────────────────────
        if self.session and self.session.id and not self._interrupted:
            print(f"\n📋 Session: {self.session.id}")

    def repl(self, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Interactive REPL — continuous conversation until the user exits."""
        _install_signal_handler()

        if not self.client.check_health():
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

        system = self._build_system_prompt(skill_name)
        ws = self.config.workspace or "."

        msg_count = len(self.messages)
        print(f"🔮 Wisp (model: {self.config.model})")
        print(f"   Session: {self.session.id}")
        if msg_count:
            print(f"   History: {msg_count} messages so far")
        if skill_name:
            print(f"   Skill: {skill_name}")
        print()
        print("Type 'exit', 'quit', or press Ctrl+C to end.")
        print()

        while not self._interrupted:
            try:
                user_input = _input_line("➜ ")
            except KeyboardInterrupt:
                print("\n⏹  Exiting.")
                break

            cmd = user_input.strip()
            if not cmd:
                if not sys.stdin.isatty():
                    break
                continue
            if cmd in ("exit", "quit", "/exit", "/quit"):
                print("👋 Goodbye.")
                break
            if cmd in ("help", "/help", "?"):
                print("  Commands: help / exit / quit")
                print("  Type any prompt to chat with Wisp.")
                continue

            # Update session title BEFORE executing, so it's saved correctly
            if self.session and self.session.title in ("REPL session", "(untitled)"):
                self.session.title = cmd[:60].strip()

            # Print blank line so response breathes after the prompt
            print()

            self._add_message("user", cmd)
            self._execute_loop(system, ws, self.config.auto_approve)

            # Visual turn separator (only if not empty turn)
            if not self._interrupted:
                _print_separator()

        print()
        print(f"📋 Session {self.session.id} saved.")
        print(f"   Continue with: wisp repl -S {self.session.id}")

    def _execute_loop(self, system: str, workspace: str, auto_approve: bool) -> None:
        """Execute the agent loop for one user turn."""
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
                self._save_session()
        finally:
            # Always save session on exit, even if interrupted mid-tool-call
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
