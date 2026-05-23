"""Hook types for Wisp hook system.

Extracted from wisp/adapters.py during Phase 7.1 migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class HookEvent:
    """Hook event type constants."""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    BASH_COMMAND = "bash_command"
    FILE_WRITE = "file_write"
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Composite events used by tool_executor
    PRE_BASH = "pre_bash"
    PRE_FILE_WRITE = "pre_file_write"
    POST_TOOL_USE = "post_tool_use"
    POST_BASH = "post_bash"
    PRE_TOOL_USE = "pre_tool_use"

    # Subagent lifecycle events
    SUBAGENT_SPAWN = "subagent_spawn"
    SUBAGENT_COMPLETE = "subagent_complete"
    SUBAGENT_FAIL = "subagent_fail"

    def __init__(self, event_type: str, **kwargs):
        self.event_type = event_type
        self.__dict__.update(kwargs)


@dataclass
class HookConfig:
    """Configuration for a single hook."""
    name: str = ""
    event: str = ""
    command: str = ""
    script: str = ""
    timeout: float = 5.0
    timeout_seconds: float = 5.0
    enabled: bool = True
    matcher: str = ""
    working_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "event": self.event,
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "matcher": self.matcher,
            "working_dir": self.working_dir,
        }


class HookResult:
    """Result from running a hook."""
    ALLOW = "allow"
    BLOCK = "block"
    WARN = "warn"

    def __init__(self, decision: str = "allow", reason: str = "", modified_args: dict | None = None):
        self.decision = decision
        self.reason = reason
        self.modified_args = modified_args


class HookManager:
    """Manages hooks — loading, listing, and running them.

    Supports two call signatures for ``run_hooks``:
      - ``run_hooks(event)`` — legacy 1-arg, returns single HookResult
      - ``run_hooks(event, context)`` — 2-arg with context dict, returns list[HookResult]
    """

    def __init__(self, config_dir: str | None = None, workspace: str | None = None):
        self.config_dir = config_dir
        self.workspace = workspace
        self.hooks: list[HookConfig] = []

    def load_hooks(self) -> None:
        pass

    def load_project_hooks(self) -> None:
        """Reload project-level hooks from disk (called before each tool call)."""
        pass

    def maybe_reload_hooks(self) -> None:
        """Refresh hook registry if needed. Safe to call before every tool use."""
        pass

    def list_hooks(self) -> list[HookConfig]:
        return list(self.hooks)

    def get_hook(self, name: str) -> HookConfig | None:
        for h in self.hooks:
            if h.name == name:
                return h
        return None

    def run_hooks(self, event, context=None):
        """Run registered hooks for *event*.

        Args:
            event: HookEvent instance (1-arg) or event type string (2-arg)
            context: Optional context dict (2-arg path). When provided,
                     returns a list of HookResult objects.

        Returns:
            Single HookResult for 1-arg calls.
            List[HookResult] for 2-arg calls.
        """
        if context is not None:
            return [HookResult(decision="allow")]
        return HookResult(decision="allow")

    async def arun_hooks(self, event, context):
        """Async variant of run_hooks — for tool_executor compatibility.

        Real hook execution (shell commands) is async; this stub returns a
        synchronous allow-list. When hooks are fully implemented, run_hooks
        itself will become async and this method can be removed.
        """
        return self.run_hooks(event, context)

    def register(self, hook: HookConfig) -> None:
        self.hooks.append(hook)


def build_hook_context(**kwargs) -> dict:
    """Build a context dict for hook execution."""
    return kwargs
