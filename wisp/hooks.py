"""Comprehensive hooks system for the Wisp AI coding agent.

Hooks are user-defined scripts that execute at specific lifecycle events
during the agent's operation. They can inspect, modify, block, or warn about
tool calls, bash commands, file writes, and session boundaries.

Architecture:
  - Hook scripts receive context as JSON on stdin.
  - Hook scripts emit decisions as JSON on stdout.
  - Environment variables provide quick access to key fields.
  - Timeouts prevent hung scripts from stalling the agent.
  - Broken hooks never block — the agent errs on the side of allowing.

Integration point (done by a separate agent):
  The HookManager is instantiated by agent.py and its run_hooks() method
  is called before/after tool execution in _run_tool_calls().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Optional

from wisp.tools._utils_env import scrub_sensitive_env

logger = logging.getLogger(__name__)


# ============================================================================
# Type aliases
# ============================================================================

# Forward-reference type (will be bound at integration time).
# Avoids circular imports while still giving type-checkers a hint.
_HookConfigType = Any


# ============================================================================
# HookEvent — lifecycle events that hooks can subscribe to
# ============================================================================


class HookEvent(str, Enum):
    """Agent lifecycle events that hooks can subscribe to.

    Hooks are run synchronously (in order) when the agent reaches
    these points:

    * PRE_TOOL_USE   — before any tool execution (bash, file, git, etc.)
    * POST_TOOL_USE  — after tool execution completes (success or error)
    * PRE_BASH       — before a run_bash shell command
    * POST_BASH      — after a run_bash command finishes
    * PRE_FILE_WRITE — before write_file / edit_file / edit_file_multi
    * SESSION_START  — when a new agent session begins
    * SESSION_END    — when an agent session terminates
    """

    PRE_TOOL_USE = "PRE_TOOL_USE"
    POST_TOOL_USE = "POST_TOOL_USE"
    PRE_BASH = "PRE_BASH"
    POST_BASH = "POST_BASH"
    PRE_FILE_WRITE = "PRE_FILE_WRITE"
    SESSION_START = "SESSION_START"
    SESSION_END = "SESSION_END"


# ============================================================================
# HookResult — the decision a hook script returns
# ============================================================================


@dataclass
class HookResult:
    """Represents the decision made by a single hook script.

    Attributes:
        action: One of "allow", "block", "modify", or "warn".
            - allow:  proceed normally
            - block:  stop the operation entirely
            - modify: proceed with modified arguments (see modified_args)
            - warn:   proceed but surface a warning to the user
        message: Human-readable explanation of the decision.
        modified_args: New arguments dict when action is "modify".
            None for other actions.
        exit_code: Raw exit code from the hook subprocess (for debugging).
        hook_name: Name of the hook that produced this result.
    """

    action: str  # "allow" | "block" | "modify" | "warn"
    message: str = ""
    modified_args: Optional[dict[str, Any]] = None
    exit_code: int = 0
    hook_name: str = ""

    # ── Factory methods ────────────────────────────────────────────────

    @classmethod
    def allow(cls, hook_name: str = "", message: str = "") -> HookResult:
        """Convenience factory for an allow decision."""
        return cls(action="allow", message=message, hook_name=hook_name)

    @classmethod
    def block(cls, hook_name: str = "", message: str = "") -> HookResult:
        """Convenience factory for a block decision."""
        return cls(action="block", message=message, hook_name=hook_name)

    @classmethod
    def modify(
        cls,
        modified_args: dict[str, Any],
        hook_name: str = "",
        message: str = "",
    ) -> HookResult:
        """Convenience factory for a modify decision."""
        return cls(
            action="modify",
            modified_args=modified_args,
            message=message,
            hook_name=hook_name,
        )

    @classmethod
    def warn(cls, hook_name: str = "", message: str = "") -> HookResult:
        """Convenience factory for a warn decision."""
        return cls(action="warn", message=message, hook_name=hook_name)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def is_blocking(self) -> bool:
        """True if this result blocks the operation."""
        return self.action == "block"

    @property
    def is_modify(self) -> bool:
        """True if this result modifies arguments."""
        return self.action == "modify"

    @property
    def is_warning(self) -> bool:
        """True if this result is a warning."""
        return self.action == "warn"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        d: dict[str, Any] = {
            "action": self.action,
            "message": self.message,
            "hook_name": self.hook_name,
        }
        if self.modified_args is not None:
            d["modified_args"] = self.modified_args
        return d


# ============================================================================
# HookConfig — definition of a single hook
# ============================================================================


@dataclass
class HookConfig:
    """Configuration for a single hook.

    Each hook listens to one specific HookEvent and runs a shell command
    when that event fires. An optional matcher regex can restrict the hook
    to specific tool names (e.g., "rm|delete|drop" for destructive commands).

    Attributes:
        name: Unique identifier for this hook (used for removal/logging).
        event: The HookEvent this hook listens to.
        command: Shell command or script path to execute.
            Relative paths are resolved against the workspace root.
        timeout_seconds: Maximum runtime before the subprocess is killed.
        enabled: Whether this hook is active.
        matcher: Optional regex pattern matched against tool names.
            If set, the hook only fires when tool_name matches the pattern.
            Uses re.search() so partial matches work (e.g., "bash" matches "run_bash").
        working_dir: Optional working directory for the subprocess.
            Defaults to the workspace root.
    """

    name: str
    event: HookEvent
    command: str
    timeout_seconds: float = 30.0
    enabled: bool = True
    matcher: Optional[str] = None
    working_dir: Optional[str] = None

    # Compiled regex cache (not serialized — computed lazily)
    _compiled_matcher: Optional[re.Pattern] = field(default=None, repr=False, init=False)

    def matches_tool(self, tool_name: str) -> bool:
        """Return True if this hook's matcher matches the given tool name.

        If no matcher is configured, always returns True (hook fires for all tools).
        """
        if self.matcher is None:
            return True
        if self._compiled_matcher is None:
            try:
                self._compiled_matcher = re.compile(self.matcher)
            except re.error as exc:
                logger.warning(
                    "Hook '%s': invalid matcher regex '%s': %s",
                    self.name,
                    self.matcher,
                    exc,
                )
                return False
        return bool(self._compiled_matcher.search(tool_name))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (for saving config files)."""
        return {
            "name": self.name,
            "event": self.event.value if isinstance(self.event, HookEvent) else self.event,
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "matcher": self.matcher,
            "working_dir": self.working_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookConfig:
        """Deserialize from a dict (for loading config files)."""
        event_raw = data.get("event", "")
        if isinstance(event_raw, HookEvent):
            event = event_raw
        else:
            try:
                event = HookEvent[event_raw]
            except (KeyError, TypeError):
                available = [e.name for e in HookEvent]
                raise ValueError(
                    f"Unknown hook event '{event_raw}'. "
                    f"Must be one of: {', '.join(available)}"
                )
        return cls(
            name=data["name"],
            event=event,
            command=data["command"],
            timeout_seconds=data.get("timeout_seconds", 30.0),
            enabled=data.get("enabled", True),
            matcher=data.get("matcher"),
            working_dir=data.get("working_dir"),
        )


# ============================================================================
# Hook context — the data passed to hook scripts
# ============================================================================


def build_hook_context(
    event: HookEvent,
    tool_name: str = "",
    tool_args: Optional[dict[str, Any]] = None,
    workspace: str = "",
    session_id: str = "",
    cwd: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the context dict that is passed to hook scripts via stdin.

    The caller in agent.py uses this to construct consistent context
    before invoking run_hooks().
    """
    context: dict[str, Any] = {
        "event": event.value,
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "workspace": workspace,
        "session_id": session_id,
        "cwd": cwd or os.getcwd(),
    }
    if extra:
        context.update(extra)
    return context


# ============================================================================
# HookManager — orchestrates hook discovery, registration, and execution
# ============================================================================


class HookManager:
    """Manages hook discovery, registration, and execution.

    Usage sketch (integration will be done by a separate agent)::

        hooks = HookManager(config, workspace)
        hooks.load_project_hooks()

        context = build_hook_context(
            event=HookEvent.PRE_TOOL_USE,
            tool_name="run_bash",
            tool_args={"command": "rm -rf /tmp/foo"},
            workspace=str(workspace),
            session_id=agent.session_id,
        )
        results = await hooks.run_hooks(HookEvent.PRE_TOOL_USE, context)

        if should_block(results):
            # abort the tool call
            ...
        modified = get_modified_args(results)
        if modified:
            tool_args = modified
    """

    # Standard hook directory names
    HOOK_DIR: ClassVar[str] = ".wisp/hooks"

    def __init__(
        self,
        config: Optional[Any] = None,
        workspace: Optional[Path] = None,
    ):
        """Initialize the hook manager.

        Args:
            config: A WispConfig instance (optional — hooks can work without one).
            workspace: Path to the project workspace root.
        """
        self.config: Any = config
        self.workspace: Path = Path(workspace) if workspace else Path.cwd()
        self._hooks: dict[str, HookConfig] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Hook registration ─────────────────────────────────────────────

    def add_hook(self, hook: HookConfig) -> None:
        """Register a hook.

        If a hook with the same name already exists, it is replaced
        and a warning is logged.
        """
        if hook.name in self._hooks:
            logger.warning(
                "Hook '%s' already registered — replacing with new config.",
                hook.name,
            )
        self._hooks[hook.name] = hook
        logger.debug(
            "Hook '%s' registered for event %s (enabled=%s)",
            hook.name,
            hook.event.value,
            hook.enabled,
        )

    def remove_hook(self, name: str) -> bool:
        """Remove a hook by name.

        Returns:
            True if the hook was found and removed, False otherwise.
        """
        if name in self._hooks:
            del self._hooks[name]
            logger.debug("Hook '%s' removed.", name)
            return True
        logger.debug("Hook '%s' not found — nothing to remove.", name)
        return False

    def get_hook(self, name: str) -> Optional[HookConfig]:
        """Return a registered hook by name, or None."""
        return self._hooks.get(name)

    def list_hooks(self, event: Optional[HookEvent] = None) -> list[HookConfig]:
        """List all registered hooks, optionally filtered by event type."""
        if event is None:
            return list(self._hooks.values())
        return [h for h in self._hooks.values() if h.event == event]

    @property
    def hook_count(self) -> int:
        """Number of registered hooks (including disabled)."""
        return len(self._hooks)

    def maybe_reload_hooks(self) -> int:
        """Reload project hooks if the hook directory has been modified since
        the last time hooks were loaded.

        Only reloads if the hooks directory (or any of its direct children)
        mtime is newer than the cached load time.  This allows users to
        drop new hook scripts in while the server is running without requiring
        a restart, while keeping the hot path cheap.
        """
        mtime = self._get_hooks_dir_mtime()
        if mtime is not None and mtime > self._last_load_time:
            logger.info("Hook directory changed — reloading hooks.")
            # Clear existing hooks and rebuild from scratch to pick up
            # removals, renames, and additions.
            self._hooks.clear()
            self._last_load_time = mtime
            return self.load_project_hooks()
        return 0

    def _get_hooks_dir_mtime(self) -> float | None:
        """Return the newest mtime from the project hook directory or None."""
        hooks_dir = self.workspace / self.HOOK_DIR
        if not hooks_dir.is_dir():
            return None
        newest = hooks_dir.stat().st_mtime
        for entry in hooks_dir.iterdir():
            try:
                if entry.is_dir():
                    continue
                m = entry.stat().st_mtime
                if m > newest:
                    newest = m
            except OSError:
                pass
        return newest

    def _enforce_hooks_dir_readonly(self, hooks_dir: Path) -> None:
        """Make the hooks directory read-only at the OS level.

        This is defense-in-depth: even if a tool bypasses the path-blocking
        check in ``_is_hook_controlled_path``, the OS will deny writes to the
        hooks directory.  On Unix this sets mode 0o555 (r-xr-xr-x); on
        Windows it sets the read-only attribute on the directory.

        Idempotent — safe to call multiple times.
        """
        try:
            import stat
            # Remove write bits for owner, group, and others
            current = hooks_dir.stat().st_mode
            readonly = current & ~0o222  # clear all write bits
            hooks_dir.chmod(readonly)
            # Also make all existing hook files read-only
            for entry in hooks_dir.iterdir():
                if entry.is_file():
                    entry.chmod(entry.stat().st_mode & ~0o222)
            logger.debug("Enforced read-only permissions on hooks dir: %s", hooks_dir)
        except OSError as exc:
            logger.warning("Could not enforce read-only hooks dir %s: %s", hooks_dir, exc)

    # ── Hook discovery ────────────────────────────────────────────────

    def load_project_hooks(self) -> int:
        """Load hooks from .wisp/hooks/ directory in the workspace.

        Supports two layout styles:

        1. **JSON config files** (``.wisp/hooks/*.json``):
           Each JSON file defines one HookConfig.

        2. **Convention-based scripts** (``.wisp/hooks/{event}_{name}.{ext}``):
           Shell scripts (.sh) and Python scripts (.py) are auto-discovered
           by their naming convention.  Example::

               .wisp/hooks/PRE_BASH_block-rm.sh
               .wisp/hooks/PRE_FILE_WRITE_validate-paths.py

           The prefix before the first ``_`` is treated as the event name,
           the rest (minus extension) becomes the hook name, and .py files
           get ``python3 {path}`` as their command automatically.

        Also loads from ``~/.config/wisp/hooks/`` (user-global hooks) in
        the same manner.  Project hooks are loaded first, then user hooks
        (user hooks can override project hooks by name).

        Returns:
            Total number of hooks loaded.
        """
        loaded_count = 0
        search_paths: list[Path] = []

        # Project hooks directory
        project_hooks = self.workspace / self.HOOK_DIR
        if project_hooks.is_dir():
            from wisp.trust import WorkspaceTrustManager
            if WorkspaceTrustManager.is_workspace_trusted(self.workspace):
                search_paths.append(project_hooks)
            else:
                logger.warning(
                    "Skipping loading workspace-local hooks because the workspace is untrusted: %s. "
                    "To trust this workspace, add its path to trusted_workspaces.json.",
                    self.workspace
                )

        # User-global hooks directory
        user_hooks = Path.home() / ".config" / "wisp" / "hooks"
        if user_hooks.is_dir():
            search_paths.append(user_hooks)

        for hooks_dir in search_paths:
            if not hooks_dir.is_dir():
                logger.debug("Hooks directory not found: %s", hooks_dir)
                continue

            # Defense-in-depth: make hooks dir read-only at OS level
            self._enforce_hooks_dir_readonly(hooks_dir)

            logger.debug("Scanning hooks directory: %s", hooks_dir)
            loaded_count += self._load_json_hooks(hooks_dir)
            loaded_count += self._load_convention_hooks(hooks_dir)

        if loaded_count > 0:
            logger.info(
                "Loaded %d hook(s) from project and user hook directories.",
                loaded_count,
            )
        else:
            logger.debug("No hooks found in any hook directory.")

        return loaded_count

    def reload_hooks(self) -> int:
        """Re-discover hooks from disk by clearing the registry and re-loading.

        This is called automatically by the ToolExecutor before every
        tool invocation so that newly-installed hooks take effect without
        restarting the agent.

        Returns:
            Total number of hooks loaded.
        """
        self._hooks.clear()
        return self.load_project_hooks()

    def _load_json_hooks(self, hooks_dir: Path) -> int:
        """Load hooks from *.json files in a directory."""
        count = 0
        for json_file in sorted(hooks_dir.glob("*.json")):
            try:
                raw = json.loads(json_file.read_text())
                if isinstance(raw, list):
                    # Support an array of hook configs in a single file
                    for item in raw:
                        hook = HookConfig.from_dict(item)
                        self.add_hook(hook)
                        count += 1
                else:
                    hook = HookConfig.from_dict(raw)
                    self.add_hook(hook)
                    count += 1
                logger.debug("Loaded hook config from %s", json_file)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Invalid JSON in hook file %s: %s", json_file, exc
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "Invalid hook config in %s: %s", json_file, exc
                )
        return count

    def _load_convention_hooks(self, hooks_dir: Path) -> int:
        """Load hooks from convention-named scripts: {event}_{name}.{ext}."""
        count = 0
        for script_file in sorted(hooks_dir.iterdir()):
            if script_file.suffix not in (".sh", ".py"):
                continue

            stem = script_file.stem  # e.g., "PRE_BASH_block-rm"

            # Event names contain underscores (e.g., PRE_BASH, PRE_FILE_WRITE).
            # We must try the longest possible event prefix first to avoid
            # mis-parsing "PRE_BASH_block-rm" as event="PRE" name="BASH_block-rm".
            event_str = ""
            name = ""
            for event_candidate in HookEvent:
                candidate = event_candidate.name
                # Must be followed by an underscore and then a non-empty name
                prefix = candidate + "_"
                if stem.startswith(prefix):
                    potential_name = stem[len(prefix):]
                    if potential_name and len(potential_name) > len(name):
                        event_str = candidate
                        name = potential_name

            if not event_str:
                logger.debug(
                    "Skipping hook script with non-convention name: %s",
                    script_file.name,
                )
                continue

            if not name:
                logger.debug(
                    "Skipping hook script — no name after event: %s",
                    script_file.name,
                )
                continue

            # Validate event name (redundant — we matched above, but belt+braces)
            try:
                event = HookEvent[event_str]
            except KeyError:
                logger.debug(
                    "Skipping %s — '%s' is not a valid HookEvent. "
                    "Expected one of: %s",
                    script_file.name,
                    event_str,
                    ", ".join(e.name for e in HookEvent),
                )
                continue

            # Build command
            if script_file.suffix == ".py":
                command = f"python3 {shlex.quote(str(script_file))}"
            else:
                # Make sure .sh scripts are executable or run with bash
                if os.access(script_file, os.X_OK):
                    command = str(script_file)
                else:
                    command = f"bash {shlex.quote(str(script_file))}"

            hook = HookConfig(
                name=name,
                event=event,
                command=command,
                working_dir=str(script_file.parent),
            )
            self.add_hook(hook)
            count += 1
            logger.debug(
                "Auto-discovered hook '%s' for event %s from %s",
                name,
                event.value,
                script_file.name,
            )

        return count

    # ── Hook execution ────────────────────────────────────────────────

    async def run_hooks(
        self,
        event: HookEvent,
        context: dict[str, Any],
    ) -> list[HookResult]:
        """Run all enabled hooks for a given lifecycle event.

        Hooks are executed sequentially in registration order (earliest
        registered first for deterministic behavior).

        Args:
            event: The lifecycle event being processed.
            context: Context dict (built via build_hook_context()).

        Returns:
            List of HookResult, one per hook that fired.  If a hook's
            matcher does not match the tool_name in context, that hook
            is skipped and no result is produced for it.

        Priority notes for the caller (agent.py):
            * If **any** result has action="block", the operation MUST be blocked.
            * If no blocks and **any** result has action="modify", use the
              merged modified_args (last writer wins via get_modified_args).
            * If no blocks/modifies and **any** result has action="warn",
              surface the messages to the user.
        """
        results: list[HookResult] = []

        # Sort hooks by name for deterministic execution order
        matching_hooks = [
            h
            for h in self._hooks.values()
            if h.event == event and h.enabled
        ]
        matching_hooks.sort(key=lambda h: h.name)

        if not matching_hooks:
            return results

        tool_name = context.get("tool_name", "")
        logger.debug(
            "Running %d hook(s) for event %s (tool=%s)",
            len(matching_hooks),
            event.value,
            tool_name or "<none>",
        )

        for hook in matching_hooks:
            # Check matcher
            if hook.matcher is not None and tool_name:
                if not hook.matches_tool(tool_name):
                    logger.debug(
                        "Hook '%s' skipped — tool '%s' does not match '%s'",
                        hook.name,
                        tool_name,
                        hook.matcher,
                    )
                    continue

            result = await self._execute_hook(hook, context)
            results.append(result)

            # Log each decision
            if result.action == "block":
                logger.info(
                    "Hook '%s' BLOCKED tool '%s': %s",
                    hook.name,
                    tool_name,
                    result.message,
                )
            elif result.action == "modify":
                logger.info(
                    "Hook '%s' MODIFIED args for tool '%s': %s",
                    hook.name,
                    tool_name,
                    result.message or "(no message)",
                )
            elif result.action == "warn":
                logger.info(
                    "Hook '%s' WARNED on tool '%s': %s",
                    hook.name,
                    tool_name,
                    result.message,
                )
            else:
                logger.debug(
                    "Hook '%s' allowed tool '%s': %s",
                    hook.name,
                    tool_name,
                    result.message or "(no message)",
                )

        return results

    async def _execute_hook(
        self,
        hook: HookConfig,
        context: dict[str, Any],
    ) -> HookResult:
        """Execute a single hook script as a subprocess.

        Protocol:
            1. Context is serialized as JSON and sent to the subprocess's stdin.
            2. The subprocess writes a JSON decision to stdout.
            3. Exit code 0 with valid JSON -> parsed as the result.
            4. Exit code 0 with invalid stdout -> treated as "allow".
            5. Exit code non-zero with invalid stdout -> treated as "block".
            6. Timeout -> process is killed, treated as "allow" (safe default).

        Environment variables set for the subprocess:
            WISP_HOOK_EVENT, WISP_TOOL_NAME, WISP_WORKSPACE, WISP_SESSION_ID.

        Args:
            hook: The hook configuration.
            context: The context dict to pass via stdin.

        Returns:
            A HookResult representing the hook's decision.
        """
        import time
        start_time = time.time()
        async with self._lock:
            result = await self._execute_hook_impl(hook, context)
        duration = time.time() - start_time
        
        self._audit_hook_execution(hook, context, result, duration)
        return result

    def _audit_hook_execution(
        self,
        hook: HookConfig,
        context: dict[str, Any],
        result: HookResult,
        duration: float,
    ) -> None:
        """Audit hook execution by appending a structured log entry to .wisp/hooks_audit.jsonl."""
        try:
            from datetime import datetime, timezone
            audit_dir = self.workspace / ".wisp"
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit_file = audit_dir / "hooks_audit.jsonl"
            
            # Scrub sensitive tool arguments before logging (e.g. content)
            tool_args = dict(context.get("tool_args", {})) if context.get("tool_args") else {}
            for key in ("content", "text", "new_text", "old_text", "command"):
                if key in tool_args:
                    tool_args[key] = f"... [scrubbed {len(str(tool_args[key]))} chars]"
                    
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "hook_name": hook.name,
                "event": hook.event.value,
                "tool_name": context.get("tool_name", ""),
                "tool_args": tool_args,
                "action": result.action,
                "message": result.message,
                "duration_seconds": round(duration, 4),
            }
            
            with open(audit_file, "a", encoding="utf-8") as f:
                import platform
                is_windows = platform.system() == "Windows"
                
                # Apply cross-platform file lock
                if is_windows:
                    import msvcrt
                    # Lock the first byte to act as a mutex
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    
                try:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                finally:
                    if is_windows:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.warning("Failed to write hook audit log: %s", e)

    async def _execute_hook_impl(
        self,
        hook: HookConfig,
        context: dict[str, Any],
    ) -> HookResult:
        """Internal implementation of hook execution (lock already held)."""
        # Build environment — strip credentials and sensitive paths so that
        # a compromised or self-installed hook cannot exfiltrate secrets.
        env = scrub_sensitive_env(os.environ)
        env["WISP_HOOK_EVENT"] = hook.event.value
        env["WISP_TOOL_NAME"] = str(context.get("tool_name", ""))
        env["WISP_WORKSPACE"] = str(context.get("workspace", ""))
        env["WISP_SESSION_ID"] = str(context.get("session_id", ""))

        # Determine working directory
        cwd: Optional[str] = None
        if hook.working_dir:
            cwd = hook.working_dir
        elif context.get("cwd"):
            cwd = context["cwd"]

        # Serialize context
        context_json = json.dumps(context, ensure_ascii=False)

        # Parse the shell command into args
        try:
            cmd_args = shlex.split(hook.command)
        except ValueError as exc:
            logger.warning(
                "Hook '%s': failed to parse command '%s': %s. Allowing.",
                hook.name,
                hook.command,
                exc,
            )
            return HookResult.allow(
                hook_name=hook.name,
                message=f"Failed to parse hook command: {exc}",
            )

        if not cmd_args:
            logger.warning(
                "Hook '%s': empty command. Allowing.", hook.name
            )
            return HookResult.allow(hook_name=hook.name)

        stdout = ""
        exit_code = -1

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input=context_json.encode("utf-8")),
                    timeout=hook.timeout_seconds,
                )
                exit_code = process.returncode or 0
                stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
                stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                if stderr_text:
                    logger.debug("Hook '%s' stderr: %s", hook.name, stderr_text)
            except asyncio.TimeoutError:
                logger.warning(
                    "Hook '%s' timed out after %.1fs — killing and allowing.",
                    hook.name,
                    hook.timeout_seconds,
                )
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except Exception:
                    pass
                return HookResult.allow(
                    hook_name=hook.name,
                    message=f"Hook timed out after {hook.timeout_seconds}s",
                )

        except FileNotFoundError:
            logger.warning(
                "Hook '%s': command not found '%s'. Allowing.",
                hook.name,
                hook.command,
            )
            return HookResult.allow(
                hook_name=hook.name,
                message=f"Hook command not found: {hook.command}",
            )
        except PermissionError:
            logger.warning(
                "Hook '%s': permission denied for '%s'. Allowing.",
                hook.name,
                hook.command,
            )
            return HookResult.allow(
                hook_name=hook.name,
                message=f"Permission denied for hook command: {hook.command}",
            )
        except OSError as exc:
            logger.warning(
                "Hook '%s': OS error running '%s': %s. Allowing.",
                hook.name,
                hook.command,
                exc,
            )
            return HookResult.allow(
                hook_name=hook.name,
                message=f"Hook execution failed: {exc}",
            )

        # Parse stdout as JSON decision
        result = _parse_hook_stdout(stdout, exit_code, hook.name)
        return result


def _parse_hook_stdout(
    stdout: str,
    exit_code: int,
    hook_name: str,
) -> HookResult:
    """Parse hook stdout into a HookResult.

    Strategy:
        1. Try to parse stdout as JSON with expected fields.
        2. If stdout is empty or invalid JSON:
           - exit_code 0 -> "allow"
           - exit_code non-zero -> "block"
    """
    VALID_ACTIONS = frozenset({"allow", "block", "modify", "warn"})

    if stdout:
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                logger.warning(
                    "Hook '%s': stdout is valid JSON but not a dict: %r. "
                    "Falling back to exit-code logic.",
                    hook_name,
                    type(data).__name__,
                )
                return _fallback_result(exit_code, hook_name)

            action = str(data.get("action", "")).lower().strip()
            if action not in VALID_ACTIONS:
                logger.warning(
                    "Hook '%s': unknown action '%s' (expected one of %s). "
                    "Falling back to exit-code logic.",
                    hook_name,
                    action,
                    sorted(VALID_ACTIONS),
                )
                return _fallback_result(exit_code, hook_name)

            message = str(data.get("message", ""))
            modified_args = data.get("modified_args")
            if action == "modify" and modified_args is None:
                logger.warning(
                    "Hook '%s': action is 'modify' but no modified_args provided. "
                    "Treating as 'allow'.",
                    hook_name,
                )
                action = "allow"
            if modified_args is not None and not isinstance(modified_args, dict):
                logger.warning(
                    "Hook '%s': modified_args is not a dict. Ignoring.",
                    hook_name,
                )
                modified_args = None

            return HookResult(
                action=action,
                message=message,
                modified_args=modified_args if isinstance(modified_args, dict) else None,
                exit_code=exit_code,
                hook_name=hook_name,
            )

        except json.JSONDecodeError:
            logger.warning(
                "Hook '%s': stdout is not valid JSON: %r. "
                "Falling back to exit-code logic (exit_code=%d).",
                hook_name,
                stdout[:200],
                exit_code,
            )
    else:
        logger.debug(
            "Hook '%s': no stdout output. "
            "Falling back to exit-code logic (exit_code=%d).",
            hook_name,
            exit_code,
        )

    return _fallback_result(exit_code, hook_name)


def _fallback_result(exit_code: int, hook_name: str) -> HookResult:
    """Produce a result from just an exit code.

    exit_code == 0 -> allow
    exit_code != 0 -> block
    """
    if exit_code == 0:
        return HookResult.allow(hook_name=hook_name)
    else:
        return HookResult.block(
            hook_name=hook_name,
            message=f"Hook exited with code {exit_code}",
        )


# ============================================================================
# Convenience functions for processing hook results
# ============================================================================


def should_block(results: list[HookResult]) -> bool:
    """Return True if any result in the list blocks the operation.

    Blocks take highest priority. If any hook says "block", the
    operation must be aborted regardless of other results.
    """
    return any(r.action == "block" for r in results)


def has_warnings(results: list[HookResult]) -> bool:
    """Return True if any result is a warning."""
    return any(r.action == "warn" for r in results)


def has_modifications(results: list[HookResult]) -> bool:
    """Return True if any result modifies arguments."""
    return any(r.action == "modify" for r in results)


def get_modified_args(results: list[HookResult]) -> Optional[dict[str, Any]]:
    """Merge modified_args from all results.

    Later hooks override earlier hooks on a per-key basis (last writer wins).
    This is the safe default: hooks are run in order, so the most recently
    executed hook has the final say.

    Returns None if no hooks produced modifications.
    """
    merged: Optional[dict[str, Any]] = None
    for r in results:
        if r.action == "modify" and r.modified_args is not None:
            if merged is None:
                merged = {}
            merged.update(r.modified_args)
    return merged


def collect_messages(results: list[HookResult]) -> list[str]:
    """Collect all non-empty messages from hook results.

    Useful for surfacing warnings and info to the user or transport layer.
    """
    return [r.message for r in results if r.message]


def collect_block_reasons(results: list[HookResult]) -> list[str]:
    """Collect messages only from blocking results."""
    return [r.message for r in results if r.action == "block" and r.message]


def collect_warnings(results: list[HookResult]) -> list[str]:
    """Collect messages only from warning results."""
    return [r.message for r in results if r.action == "warn" and r.message]


def summarize_results(results: list[HookResult]) -> str:
    """Return a human-readable one-line summary of all results."""
    if not results:
        return "No hooks fired."

    blocks = [r for r in results if r.action == "block"]
    modifies = [r for r in results if r.action == "modify"]
    warns = [r for r in results if r.action == "warn"]
    allows = [r for r in results if r.action == "allow"]

    parts: list[str] = []
    if blocks:
        parts.append(f"{len(blocks)} block(s)")
    if modifies:
        parts.append(f"{len(modifies)} modify(s)")
    if warns:
        parts.append(f"{len(warns)} warn(s)")
    if allows and not (blocks or modifies or warns):
        return f"{len(allows)} hook(s) allowed."
    if allows:
        parts.append(f"{len(allows)} allow(s)")

    # Prepend hook names for blocking results (most important)
    if blocks:
        blocker_names = ", ".join(r.hook_name for r in blocks)
        return f"BLOCKED by [{blocker_names}]; " + ", ".join(parts)

    return ", ".join(parts)
