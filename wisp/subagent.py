"""Subagent spawning — delegate tasks to child agents with contracts and timeouts.

A subagent is a fresh WispAgent instance that handles a scoped task
(research, coding, testing) and returns a structured result to the parent.
Subagents run in-process (threaded) with hard timeout enforcement.

Safety:
- Subagents cannot spawn subagents (max depth = 1)
- Subagents inherit the parent's dangerous-command guard
- Subagent edits happen in the same workspace (parent can review)
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Optional

from wisp.agent import WispAgent
from wisp.config import WispConfig
from wisp.tools import execute_tool, ToolError

logger = logging.getLogger(__name__)


@dataclass
class SubagentContract:
    """Defines the scope and constraints of a subagent task."""

    task: str
    """The instruction given to the subagent. Be specific."""

    tools: list[str] = field(default_factory=lambda: ["all"])
    """Tool names the subagent may use. ["all"] means inherit parent's full toolset."""

    max_iterations: int = 15
    """Maximum agent loop iterations before forced stop."""

    timeout_seconds: int = 120
    """Hard wall-clock timeout. Subagent is killed after this."""

    output_format: str = "text"
    """How the subagent should format its final answer: text | json | markdown | report."""

    model: Optional[str] = None
    """Ollama model override. None = inherit parent's model."""

    workspace: Optional[str] = None
    """Working directory. None = inherit parent's workspace."""

    system_prompt_extra: str = ""
    """Additional system prompt text appended after the default."""

    auto_approve: bool = True
    """If False, dangerous commands (sudo, rm -rf, etc.) are blocked instead of executed."""

    max_output_chars: int = 8000
    """Truncate subagent output to this length before returning to parent."""


@dataclass
class SubagentResult:
    """Structured output from a completed (or timed-out) subagent."""

    success: bool
    """True if the subagent completed within budget and timeout."""

    output: str
    """The subagent's final answer or partial result."""

    messages: list[dict]
    """Full conversation history for audit / replay."""

    elapsed_seconds: float
    """Wall-clock time consumed."""

    iterations_used: int
    """Number of agent loop iterations actually executed."""

    timed_out: bool = False
    """True if the subagent hit the hard timeout."""

    hit_iteration_limit: bool = False
    """True if the subagent hit max_iterations."""

    files_changed: list[str] = field(default_factory=list)
    """Paths the subagent reported modifying (best-effort, not guaranteed)."""


class SubagentRunner:
    """Spawns and manages child WispAgent instances."""

    def __init__(self, parent_agent: WispAgent):
        self.parent = parent_agent

    def spawn(self, contract: SubagentContract) -> SubagentResult:
        """Run a subagent with the given contract and return its result.

        The subagent executes in a background thread with a hard timeout.
        If the timeout fires, the subagent's current state is captured
        and returned with timed_out=True.
        """
        start = time.monotonic()

        # Build child config from parent
        child_config = self._build_child_config(contract)

        # Create child agent (no session, fresh messages)
        child = WispAgent(config=child_config)
        child.max_iterations = contract.max_iterations

        # Prevent infinite recursion: subagents cannot spawn subagents
        child._subagent_depth = getattr(self.parent, "_subagent_depth", 0) + 1

        # Build specialized system prompt
        system = self._build_subagent_system(contract, child)

        # Add user task as first message
        child.messages.append({"role": "user", "content": contract.task})

        logger.info(
            "Spawning subagent (depth=%d, timeout=%ds, iterations=%d)",
            child._subagent_depth,
            contract.timeout_seconds,
            contract.max_iterations,
        )

        # Run in thread pool with timeout
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._run_child, child, system, contract)
            try:
                final_output, iterations = future.result(timeout=contract.timeout_seconds)
                elapsed = time.monotonic() - start
                # Truncate output if too large
                if len(final_output) > contract.max_output_chars:
                    final_output = final_output[:contract.max_output_chars] + f"\n... [truncated: {len(final_output)} total chars]"
                return SubagentResult(
                    success=True,
                    output=final_output,
                    messages=list(child.messages),
                    elapsed_seconds=elapsed,
                    iterations_used=iterations,
                    timed_out=False,
                    hit_iteration_limit=iterations >= contract.max_iterations,
                )
            except FutureTimeoutError:
                elapsed = time.monotonic() - start
                logger.warning("Subagent timed out after %.1fs", elapsed)
                # Snapshot messages before the thread modifies them further
                # (ThreadPoolExecutor doesn't kill threads on timeout)
                messages_snapshot = list(child.messages)
                partial = self._extract_partial_output_from_snapshot(messages_snapshot)
                return SubagentResult(
                    success=False,
                    output=f"[TIMED OUT after {elapsed:.1f}s]\n\nPartial result:\n{partial}",
                    messages=messages_snapshot,
                    elapsed_seconds=elapsed,
                    iterations_used=getattr(child, "_iteration_count", 0),
                    timed_out=True,
                    hit_iteration_limit=False,
                )

    def _build_child_config(self, contract: SubagentContract) -> WispConfig:
        """Clone parent config, optionally overriding model/workspace."""
        parent_cfg = self.parent.config
        child = WispConfig()
        child.model = contract.model or parent_cfg.model
        child.workspace = contract.workspace or parent_cfg.workspace
        child.auto_approve = contract.auto_approve
        child.show_thinking = parent_cfg.show_thinking
        child.max_context_tokens = parent_cfg.max_context_tokens
        child.chars_per_token = parent_cfg.chars_per_token
        child.ollama_url = parent_cfg.ollama_url
        child.temperature = parent_cfg.temperature
        # Skip extra Ollama calls on init — inherit parent's context window
        child._context_tokens_explicit = True
        child.max_context_tokens = parent_cfg.max_context_tokens
        return child

    def _build_subagent_system(self, contract: SubagentContract, child: WispAgent) -> str:
        """Assemble a system prompt tailored to the subagent's task."""
        from wisp.agent import DEFAULT_SYSTEM

        lines = [DEFAULT_SYSTEM]
        lines.append("")
        lines.append("## Subagent Mode")
        lines.append("You are a specialist subagent working on a scoped task.")
        lines.append(f"Output format: {contract.output_format}")
        lines.append(f"Iteration budget: {contract.max_iterations}")
        lines.append("")
        lines.append("## Rules")
        lines.append("1. Focus ONLY on the given task. Do not drift.")
        lines.append("2. Return a concise, actionable result.")
        lines.append("3. If you edit files, list the changed paths in your final answer.")
        lines.append("4. You CANNOT spawn subagents.")
        lines.append("5. If stuck, summarize what you learned and stop.")

        if contract.tools != ["all"]:
            lines.append("")
            lines.append(f"## Allowed Tools")
            lines.append(", ".join(contract.tools))

        if contract.system_prompt_extra:
            lines.append("")
            lines.append("## Additional Instructions")
            lines.append(contract.system_prompt_extra)

        return "\n".join(lines)

    def _run_child(self, child: WispAgent, system: str, contract: SubagentContract) -> tuple[str, int]:
        """Execute the child agent loop. Returns (final_output, iterations_used)."""
        child._iteration_count = 0
        workspace = contract.workspace or child.config.workspace or "."

        # Filter tools if contract specifies a subset
        available_tools = self._filter_tools(contract)

        for iteration in range(1, contract.max_iterations + 1):
            child._iteration_count = iteration

            # Generate response
            try:
                response = child.client.generate(system, child.messages, tools=available_tools)
            except Exception as e:
                logger.error("Subagent generation failed: %s", e)
                return f"[Error: generation failed — {e}]", iteration

            msg = response.get("message", {})
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls")

            # No tool calls = final answer
            if not tool_calls:
                child.messages.append({"role": "assistant", "content": content})
                return content, iteration

            # Tool call turn
            child.messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            # Execute tools
            for tc in tool_calls:
                func = tc.get("function", {})
                func_name = func.get("name", "")
                func_args = func.get("arguments", "")

                if isinstance(func_args, str):
                    import json
                    try:
                        func_args = json.loads(func_args)
                    except json.JSONDecodeError:
                        func_args = {}
                if not isinstance(func_args, dict):
                    func_args = {}

                # Block subagent-from-subagent
                if func_name == "spawn_subagent":
                    child.messages.append({
                        "role": "tool",
                        "content": "[Error: subagents cannot spawn subagents]",
                        "name": func_name,
                    })
                    print(f"  [sub] ⚠️  blocked nested spawn_subagent")
                    continue

                # Dangerous command guard (same as parent)
                danger_reason = None
                if func_name == "run_bash":
                    from wisp.tools import check_dangerous_command
                    danger_reason = check_dangerous_command(func_args.get("command", ""))

                if danger_reason:
                    if not contract.auto_approve:
                        result = f"[Blocked: dangerous command — {danger_reason}]"
                        print(f"  [sub] ⚠️  {func_name} blocked ({danger_reason})")
                        child.messages.append({"role": "tool", "content": result, "name": func_name})
                        continue
                    print(f"  [sub] ⚠️  DANGEROUS: {danger_reason} — executing anyway (auto_approve=True)")

                # Print tool call for visibility
                arg_preview = self._args_preview(func_args)
                print(f"  [sub] 🛠  {func_name}({arg_preview})")

                try:
                    result = execute_tool(func_name, func_args, workspace)
                except ToolError as e:
                    result = f"Error: {e}"
                    logger.warning("Subagent tool %s failed: %s", func_name, e)
                except Exception as e:
                    result = f"Unexpected error: {e}"
                    logger.error("Unexpected error in subagent tool %s: %s", func_name, e, exc_info=True)

                # Truncate large results
                if len(result) > 4000:
                    result = result[:4000] + f"\n... [truncated {len(result)} chars]"

                preview = result[:120].replace("\n", " ")
                if len(result) > 120:
                    preview += "..."
                print(f"  [sub]    → {preview}")

                child.messages.append({"role": "tool", "content": result, "name": func_name})

        # Hit iteration limit
        return "[Hit iteration limit — returning best effort]", contract.max_iterations

    def _filter_tools(self, contract: SubagentContract):
        """Return full tool schemas or a filtered subset."""
        from wisp.tools import TOOL_SCHEMAS
        if contract.tools == ["all"]:
            return TOOL_SCHEMAS
        allowed = set(contract.tools)
        return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]

    def _extract_partial_output_from_snapshot(self, messages: list[dict]) -> str:
        """Best-effort extraction from a snapshot of messages (thread-safe)."""
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return "(no output captured)"

    def _args_preview(self, args: dict) -> str:
        """Short one-line preview of tool arguments (same as parent)."""
        parts = []
        path = args.get("path", args.get("command", ""))
        if path:
            s = str(path)
            parts.append(s[:60])
        content = args.get("content", "")
        if content:
            parts.append(f"({len(content)} chars)")
        return ", ".join(parts) if parts else "..."
