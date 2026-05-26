"""Hook types for Wisp hook system.

Extracted from wisp/adapters.py during Phase 7.1 migration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        decision: str = "allow",
        reason: str = "",
        modified_args: dict | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ):
        self.decision = decision
        self.reason = reason
        self.modified_args = modified_args
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def action(self) -> str:
        return self.decision

    @property
    def is_blocking(self) -> bool:
        return self.decision == self.BLOCK

    @property
    def message(self) -> str:
        return self.reason or self.stdout.strip() or ""

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:500] if self.stdout else "",
            "stderr": self.stderr[:500] if self.stderr else "",
        }


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
        self._last_mtime: float = 0.0

    # ── Hook loading ──────────────────────────────────────────────────

    def load_hooks(self) -> None:
        """Load hooks from project and global config directories."""
        self.hooks.clear()
        self._load_from_workspace()
        self._load_from_config_dir()

    def load_project_hooks(self) -> None:
        """Reload project-level hooks from disk (called before each tool call)."""
        if self.workspace:
            hooks_dir = Path(self.workspace) / ".wisp" / "hooks"
            if hooks_dir.is_dir():
                self._load_from_dir(hooks_dir)

    def maybe_reload_hooks(self) -> None:
        """Refresh hook registry if needed. Safe to call before every tool use."""
        if not self.workspace:
            return
        hooks_dir = Path(self.workspace) / ".wisp" / "hooks"
        if not hooks_dir.is_dir():
            return
        try:
            mtime = hooks_dir.stat().st_mtime
        except OSError:
            return
        if mtime > self._last_mtime:
            self._last_mtime = mtime
            self.load_project_hooks()

    def _load_from_workspace(self) -> None:
        if self.workspace:
            hooks_dir = Path(self.workspace) / ".wisp" / "hooks"
            if hooks_dir.is_dir():
                self._load_from_dir(hooks_dir)
                try:
                    self._last_mtime = hooks_dir.stat().st_mtime
                except OSError:
                    pass

    def _load_from_config_dir(self) -> None:
        if self.config_dir:
            hooks_dir = Path(self.config_dir) / "hooks"
            if hooks_dir.is_dir():
                self._load_from_dir(hooks_dir)

    def _load_from_dir(self, hooks_dir: Path) -> None:
        """Load all .json hook files from a directory."""
        for hook_file in sorted(hooks_dir.glob("*.json")):
            try:
                data = json.loads(hook_file.read_text(encoding="utf-8"))
                hook = HookConfig(
                    name=data.get("name", hook_file.stem),
                    event=data.get("event", ""),
                    command=data.get("command", ""),
                    timeout_seconds=data.get("timeout_seconds", 5.0),
                    enabled=data.get("enabled", True),
                    matcher=data.get("matcher", ""),
                    working_dir=data.get("working_dir", ""),
                )
                # Avoid duplicates by name
                if not any(h.name == hook.name for h in self.hooks):
                    self.hooks.append(hook)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load hook %s: %s", hook_file, exc)

    # ── Hook listing ───────────────────────────────────────────────────

    def list_hooks(self) -> list[HookConfig]:
        return list(self.hooks)

    def get_hook(self, name: str) -> HookConfig | None:
        for h in self.hooks:
            if h.name == name:
                return h
        return None

    def register(self, hook: HookConfig) -> None:
        self.hooks.append(hook)

    # ── Hook execution ─────────────────────────────────────────────────

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
            event_type = event if isinstance(event, str) else event.event_type
            return self._run_matching_hooks(event_type, context)
        return HookResult(decision="allow")

    async def arun_hooks(self, event, context):
        """Async variant of run_hooks — for tool_executor compatibility."""
        if context is not None:
            event_type = event if isinstance(event, str) else str(event)
            return await self._arun_matching_hooks(event_type, context)
        return [HookResult(decision="allow")]

    # ── Internal matching + execution ──────────────────────────────────

    def _match_hooks(self, event_type: str, context: dict) -> list[HookConfig]:
        """Return enabled hooks whose event matches and whose matcher (if set)
        matches the tool_name in context."""
        matched: list[HookConfig] = []
        tool_name = context.get("tool_name", "")
        for hook in self.hooks:
            if not hook.enabled:
                continue
            if hook.event != event_type:
                continue
            if hook.matcher:
                try:
                    if not re.search(hook.matcher, tool_name):
                        continue
                except re.error:
                    logger.warning("Invalid matcher regex in hook %s: %s", hook.name, hook.matcher)
                    continue
            matched.append(hook)
        return matched

    def _build_env(self, context: dict) -> dict[str, str]:
        """Build environment variables for hook process."""
        env = dict(os.environ)
        env["WISP_EVENT"] = str(context.get("event", ""))
        env["WISP_TOOL_NAME"] = str(context.get("tool_name", ""))
        env["WISP_WORKSPACE"] = str(context.get("workspace", ""))
        env["WISP_SESSION_ID"] = str(context.get("session_id", ""))
        # Serialize tool_args as JSON for structured access
        tool_args = context.get("tool_args", {})
        if isinstance(tool_args, dict):
            env["WISP_TOOL_ARGS"] = json.dumps(tool_args, ensure_ascii=False)
        else:
            env["WISP_TOOL_ARGS"] = str(tool_args)
        # Extra context
        extra = context.get("extra", {})
        if isinstance(extra, dict):
            env["WISP_RESULT"] = str(extra.get("result", ""))[:4000]
        return env

    def _substitute_command(self, command: str, context: dict) -> str:
        """Replace {placeholders} in command with context values."""
        subs = {
            "tool_name": str(context.get("tool_name", "")),
            "event": str(context.get("event", "")),
            "workspace": str(context.get("workspace", "")),
            "session_id": str(context.get("session_id", "")),
        }
        result = command
        for key, val in subs.items():
            result = result.replace("{" + key + "}", val)
        return result

    def _run_matching_hooks(self, event_type: str, context: dict) -> list[HookResult]:
        """Synchronous execution of matched hooks. Each hook runs in a subprocess."""
        results: list[HookResult] = []
        matched = self._match_hooks(event_type, context)
        for hook in matched:
            try:
                hr = self._run_one_hook(hook, context)
                results.append(hr)
            except Exception as exc:
                logger.warning("Hook %s failed: %s", hook.name, exc)
                results.append(HookResult(
                    decision="warn",
                    reason=f"Hook execution failed: {exc}",
                ))
        return results

    async def _arun_matching_hooks(self, event_type: str, context: dict) -> list[HookResult]:
        """Async execution of matched hooks. Each hook runs in a subprocess."""
        results: list[HookResult] = []
        matched = self._match_hooks(event_type, context)
        for hook in matched:
            try:
                hr = await self._arun_one_hook(hook, context)
                results.append(hr)
            except Exception as exc:
                logger.warning("Hook %s async execution failed: %s", hook.name, exc)
                results.append(HookResult(
                    decision="warn",
                    reason=f"Hook execution failed: {exc}",
                ))
        return results

    def _run_one_hook(self, hook: HookConfig, context: dict) -> HookResult:
        """Run a single hook synchronously via subprocess."""
        cmd = hook.command or hook.script
        if not cmd:
            return HookResult(decision="allow", reason="empty command")

        cmd = self._substitute_command(cmd, context)
        env = self._build_env(context)
        cwd = hook.working_dir or self.workspace or None

        try:
            proc = __import__("subprocess").run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=hook.timeout_seconds,
                env=env,
                cwd=cwd,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            if proc.returncode == 0:
                decision = "allow"
            elif proc.returncode == 1:
                decision = "warn"
            else:
                decision = "block"

            modified_args = None
            # Last line of stdout may contain JSON-modified tool args
            if stdout.strip():
                last_line = stdout.strip().split("\n")[-1]
                try:
                    parsed = json.loads(last_line)
                    if isinstance(parsed, dict) and "tool_args" in parsed:
                        modified_args = parsed["tool_args"]
                except (json.JSONDecodeError, ValueError):
                    pass

            return HookResult(
                decision=decision,
                reason=stderr.strip() or stdout.strip()[:200],
                modified_args=modified_args,
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
            )
        except __import__("subprocess").TimeoutExpired:
            return HookResult(
                decision="block",
                reason=f"Hook timed out after {hook.timeout_seconds}s",
                exit_code=-1,
            )
        except Exception as exc:
            return HookResult(
                decision="warn",
                reason=f"Hook failed: {exc}",
                exit_code=-1,
            )

    async def _arun_one_hook(self, hook: HookConfig, context: dict) -> HookResult:
        """Run a single hook asynchronously via subprocess."""
        cmd = hook.command or hook.script
        if not cmd:
            return HookResult(decision="allow", reason="empty command")

        cmd = self._substitute_command(cmd, context)
        env = self._build_env(context)
        cwd = hook.working_dir or self.workspace or None

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=hook.timeout_seconds,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if proc.returncode == 0:
                decision = "allow"
            elif proc.returncode == 1:
                decision = "warn"
            else:
                decision = "block"

            modified_args = None
            if stdout.strip():
                last_line = stdout.strip().split("\n")[-1]
                try:
                    parsed = json.loads(last_line)
                    if isinstance(parsed, dict) and "tool_args" in parsed:
                        modified_args = parsed["tool_args"]
                except (json.JSONDecodeError, ValueError):
                    pass

            return HookResult(
                decision=decision,
                reason=stderr.strip() or stdout.strip()[:200],
                modified_args=modified_args,
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            return HookResult(
                decision="block",
                reason=f"Hook timed out after {hook.timeout_seconds}s",
                exit_code=-1,
            )
        except Exception as exc:
            return HookResult(
                decision="warn",
                reason=f"Hook failed: {exc}",
                exit_code=-1,
            )


def build_hook_context(**kwargs) -> dict:
    """Build a context dict for hook execution."""
    return kwargs


class InterceptHookManager(HookManager):
    """HookManager for intercept path (HookExtension).

    Overrides ``run_hooks`` so that a 1-arg call with a HookEvent
    actually evaluates matching hooks instead of short-circuiting to allow.
    """

    def run_hooks(self, event, context=None):
        if context is not None:
            return super().run_hooks(event, context)
        # 1-arg path: event is a HookEvent instance
        event_type = event.event_type if hasattr(event, "event_type") else str(event)
        tool_name = getattr(event, "name", "")
        tool_args = getattr(event, "args", {})
        ctx = {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "event": event_type,
            "workspace": self.workspace or "",
        }
        results = self._run_matching_hooks(event_type, ctx)
        for r in results:
            if getattr(r, "is_blocking", False) or getattr(r, "action", "") == "block":
                return r
        return results[0] if results else HookResult(decision="allow")


class ToolHookManager(HookManager):
    """HookManager for tool execution path (ToolExecutor).

    Inherits all behaviour from HookManager unchanged; the separate
    class exists to make the architectural split explicit.
    """
    pass
