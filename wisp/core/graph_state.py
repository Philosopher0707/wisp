"""Agentic graph loop — state schema and primitives.

Addresses the 10 gap terms from the architecture audit:

 1. State Schema — typed per-turn GraphState (replaces the ad-hoc session dict + verification locals)
 2. code_files — dict[file_path -> contents] snapshot, maintained via ChangeTracker/FileLock hooks
 3. execution_logs — structured stdout/stderr/exit_code capture (vs flattened run_bash string)
 4. iteration_count / max_iterations — explicit loop counter with circuit-breaker guard
 5. status enum — in_progress / completed / failed / needs_human_review
 6-10. Node signatures — see `wisp/core/graph_nodes.py` and `wisp/core/agentic_graph.py`

Backwards compatible:
  - All fields have defaults; `GraphState.from_dict({})` yields a valid initial state.
  - `to_dict`/`from_dict` round-trip for session persistence.
  - Existing `WispAgentCore.turn()` and `AgentRuntime.run_turn()` are not modified — the graph is an
    additive layer (`GraphRunner`) that can be invoked from `AgentRuntime` optionally.

Defensive guarantees:
  - State transitions are wrapped in explicit try/except with actionable logs.
  - Circuit breaker (max_iterations / recursion depth) fails gracefully to FAILED, not exception.
  - Oscillation guard tracks recent state hashes to detect infinite loops.
  - Execution timeouts are enforced at the sandbox node (see graph_nodes.py).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import StrEnum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Execution log pruning knobs ────────────────────────────────────
DEFAULT_MAX_LOGS = 50
DEFAULT_MAX_LOG_CHARS = 50_000  # mirrors _MAX_BASH_OUTPUT
DEFAULT_MAX_CODE_FILES = 100
DEFAULT_MAX_CODE_FILE_BYTES = 100 * 1024 * 1024  # mirrors _MAX_WRITE_SIZE


class GraphStatus(StrEnum):
    """Turn-level lifecycle — maps 1:1 to the spec's status enum.

    Values are deliberately lowercase to match the audit spec and to stay
    compatible with the existing Plan.Task.status convention (`in_progress`).
    """

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


# Backwards-compat alias — spec uses `status` often without qualifier.
Status = GraphStatus


@dataclass
class ExecutionLog:
    """Structured capture of one `run_bash` (or equivalent) invocation.

    Unlike `wisp/tools/bash.py`'s flattened `"[exit code: N]\\n<stdout>\\n--- stderr ---\\n<stderr>"`
    string, this keeps stdout/stderr/exit_code distinct so verifiers and renderers
    can reason without reparsing.
    """

    command: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    truncated: bool = False
    # Raw flattened string kept for backwards compat / LLM prompt injection.
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionLog:
        try:
            return cls(
                command=str(data.get("command", "")),
                exit_code=int(data.get("exit_code", 0) or 0),
                stdout=str(data.get("stdout", "")),
                stderr=str(data.get("stderr", "")),
                duration_ms=float(data.get("duration_ms", 0.0) or 0.0),
                timestamp=float(data.get("timestamp", time.time()) or time.time()),
                truncated=bool(data.get("truncated", False)),
                raw=str(data.get("raw", "")),
            )
        except Exception as e:
            logger.warning("ExecutionLog.from_dict failed — using safe defaults: %s", e)
            return cls(command=str(data.get("command", "")) if isinstance(data, dict) else "")

    @classmethod
    def from_raw(cls, command: str, raw_output: str, duration_ms: float = 0.0) -> ExecutionLog:
        """Parse the legacy flattened run_bash string into structured fields.

        Heuristic — best-effort. A bespoke sandbox node should construct
        ExecutionLog directly from the sandbox's tuple.
        """
        exit_code = 0
        stdout = raw_output
        stderr = ""
        truncated = False
        try:
            if raw_output.startswith("[exit code:"):
                try:
                    first_line, rest = raw_output.split("\n", 1)
                    exit_str = first_line.split("[exit code:")[1].split("]")[0].strip()
                    exit_code = int(exit_str)
                    stdout = rest
                except Exception:
                    pass
            if "\n--- stderr ---\n" in stdout:
                stdout, stderr = stdout.split("\n--- stderr ---\n", 1)
            if "... [output truncated]" in raw_output or "... [truncated" in raw_output:
                truncated = True
        except Exception as e:
            logger.debug("ExecutionLog.from_raw parse failed for %r: %s", command[:60], e)
        return cls(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=truncated,
            raw=raw_output,
        )

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    @property
    def short_summary(self) -> str:
        """One-line human summary for operating-context / renderer."""
        status = "ok" if self.succeeded else f"exit {self.exit_code}"
        preview = (self.stdout or self.stderr or self.raw)[:80].replace("\n", " ")
        return f"`{self.command[:40]}` → {status}" + (f" — {preview}" if preview else "")


@dataclass
class GraphState:
    """Per-turn graph state — the `State` in the agentic graph loop spec.

    This is the single object threaded through planner_coder → sandbox_executor →
    verifier → human_approval → END/fallback. It owns the four spec fields that
    were previously scattered or missing:

      code_files: dict[path -> contents]
      execution_logs: list[ExecutionLog]
      iteration_count: int  (with max_iterations circuit breaker)
      status: GraphStatus  (in_progress / completed / failed / needs_human_review)

    Plus operational bookkeeping (workspace, messages, snapshots) so every state
    transition can be validated, logged, and rolled back on fatal error.
    """

    # ── Spec-mandated fields ───────────────────────────────────────
    code_files: dict[str, str] = field(default_factory=dict)
    execution_logs: list[ExecutionLog] = field(default_factory=list)
    iteration_count: int = 0
    max_iterations: int = 5  # spec default 5; reconciled with config.max_iterations=50
    status: GraphStatus = GraphStatus.IN_PROGRESS

    # ── Operational context ────────────────────────────────────────
    workspace: str = ""
    session_id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    # Human-approval bookmark: which tool call is awaiting review, if any.
    pending_approval: dict[str, Any] | None = None
    # Oscillation / infinite-loop tracking
    _recent_hashes: list[str] = field(default_factory=list, repr=False)
    _snapshot_stack: list[dict[str, Any]] = field(default_factory=list, repr=False)
    created_at: float = field(default_factory=time.time)

    # ── Construction helpers ───────────────────────────────────────

    @classmethod
    def initial(cls, workspace: str = "", session_id: str = "", max_iterations: int | None = None) -> GraphState:
        """Create a fresh IN_PROGRESS state for a new turn."""
        # Resolve the discrepancy: spec says default 5, engine says 50.
        # This layer defaults to 5 for the *graph* loop (outer), leaving the inner
        # WispAgentCore loop at 50. Callers may override from config.
        mi = max_iterations if max_iterations is not None else 5
        try:
            mi = max(1, min(200, int(mi)))
        except Exception:
            mi = 5
        return cls(workspace=workspace, session_id=session_id, max_iterations=mi)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphState:
        """Backwards-compatible loader — missing keys yield safe defaults."""
        if not isinstance(data, dict):
            logger.warning("GraphState.from_dict expected dict, got %s — returning initial state", type(data).__name__)
            return cls.initial()
        try:
            code_files = data.get("code_files") or {}
            if not isinstance(code_files, dict):
                code_files = {}
            # Clamp oversized code_files (defensive: persisted state may be stale/huge)
            if len(code_files) > DEFAULT_MAX_CODE_FILES:
                logger.warning("GraphState.from_dict: code_files %d entries exceeds cap %d — truncating", len(code_files), DEFAULT_MAX_CODE_FILES)
                code_files = dict(list(code_files.items())[:DEFAULT_MAX_CODE_FILES])

            raw_logs = data.get("execution_logs") or []
            logs: list[ExecutionLog] = []
            for entry in raw_logs[:DEFAULT_MAX_LOGS]:
                try:
                    if isinstance(entry, dict):
                        logs.append(ExecutionLog.from_dict(entry))
                    elif isinstance(entry, ExecutionLog):
                        logs.append(entry)
                except Exception as e:
                    logger.debug("Skipping corrupt execution_log entry: %s", e)
                    continue

            status_raw = str(data.get("status", GraphStatus.IN_PROGRESS))
            try:
                status = GraphStatus(status_raw)
            except ValueError:
                logger.warning("GraphState.from_dict: unknown status %r — defaulting to in_progress", status_raw)
                status = GraphStatus.IN_PROGRESS

            iteration_count = 0
            try:
                iteration_count = int(data.get("iteration_count", 0) or 0)
            except Exception:
                pass
            max_iterations = 5
            try:
                max_iterations = int(data.get("max_iterations", 5) or 5)
                max_iterations = max(1, min(200, max_iterations))
            except Exception:
                max_iterations = 5

            recent = data.get("_recent_hashes") or []
            if not isinstance(recent, list):
                recent = []

            inst = cls(
                code_files={str(k): str(v) for k, v in code_files.items()},
                execution_logs=logs,
                iteration_count=iteration_count,
                max_iterations=max_iterations,
                status=status,
                workspace=str(data.get("workspace", "")),
                session_id=str(data.get("session_id", "")),
                messages=list(data.get("messages", []) or []),
                error=str(data["error"]) if data.get("error") is not None else None,
                pending_approval=data.get("pending_approval"),
                created_at=float(data.get("created_at", time.time()) or time.time()),
            )
            # Restore oscillation history — critical for loop detection across node copies.
            try:
                inst._recent_hashes = [str(h) for h in recent][-20:]
            except Exception:
                inst._recent_hashes = []
            return inst
        except Exception as e:
            logger.warning("GraphState.from_dict failed — returning initial state: %s", e, exc_info=True)
            return cls.initial(
                workspace=str(data.get("workspace", "")) if isinstance(data.get("workspace"), str) else "",
                session_id=str(data.get("session_id", "")) if isinstance(data.get("session_id"), str) else "",
            )

    def to_dict(self) -> dict[str, Any]:
        try:
            return {
                "code_files": dict(self.code_files),
                "execution_logs": [log.to_dict() for log in self.execution_logs],
                "iteration_count": self.iteration_count,
                "max_iterations": self.max_iterations,
                "status": str(self.status),
                "workspace": self.workspace,
                "session_id": self.session_id,
                "messages": list(self.messages),
                "error": self.error,
                "pending_approval": copy.deepcopy(self.pending_approval) if self.pending_approval else None,
                "created_at": self.created_at,
                "_recent_hashes": list(self._recent_hashes),
            }
        except Exception as e:
            logger.error("GraphState.to_dict failed: %s", e, exc_info=True)
            return {"status": str(GraphStatus.FAILED), "error": f"serialization failed: {e}", "iteration_count": self.iteration_count, "max_iterations": self.max_iterations}

    # ── Snapshot / rollback ────────────────────────────────────────

    def snapshot(self) -> None:
        """Push a deep copy onto the rollback stack (call before risky mutations)."""
        try:
            self._snapshot_stack.append(copy.deepcopy(self.to_dict()))
            # Cap stack depth to avoid unbounded memory on pathological loops.
            if len(self._snapshot_stack) > 20:
                self._snapshot_stack.pop(0)
        except Exception as e:
            logger.warning("GraphState.snapshot failed: %s", e, exc_info=True)

    def rollback(self) -> bool:
        """Pop and restore the last snapshot. Returns True if a snapshot existed."""
        if not self._snapshot_stack:
            logger.warning("GraphState.rollback: no snapshot to restore (session %s)", self.session_id)
            return False
        try:
            prev = self._snapshot_stack.pop()
            restored = self.from_dict(prev)
            # Preserve the stack itself so multiple rollbacks work.
            restored._snapshot_stack = self._snapshot_stack
            restored._recent_hashes = list(self._recent_hashes)
            self.__dict__.update(restored.__dict__)
            logger.info("GraphState rolled back to iteration %d (session %s)", self.iteration_count, self.session_id)
            return True
        except Exception as e:
            logger.error("GraphState.rollback failed: %s", e, exc_info=True)
            return False

    def clear_snapshots(self) -> None:
        self._snapshot_stack.clear()

    # ── State transitions (defensive wrappers) ─────────────────────

    def transition(self, target: GraphStatus, error: str | None = None) -> bool:
        """Attempt a status transition with guard rails.

        Returns True if transition occurred, False if blocked (logs at warning).
        Never raises.
        """
        try:
            if self.status in (GraphStatus.COMPLETED, GraphStatus.FAILED) and target == GraphStatus.IN_PROGRESS:
                logger.warning("GraphState.transition blocked: terminal status %s cannot revert to in_progress (session %s)", self.status, self.session_id)
                return False
            # Only NEEDS_HUMAN_REVIEW may be entered from IN_PROGRESS, and it must carry pending_approval
            if target == GraphStatus.NEEDS_HUMAN_REVIEW and not self.pending_approval:
                logger.warning("GraphState.transition to needs_human_review without pending_approval — allowing with empty bookmark (session %s)", self.session_id)
            self.status = target
            if error is not None:
                self.error = str(error)[:2000]
            elif target in (GraphStatus.COMPLETED, GraphStatus.NEEDS_HUMAN_REVIEW):
                self.error = None  # clear prior errors on success paths
            logger.debug("GraphState status → %s (iter=%d, session=%s)", target, self.iteration_count, self.session_id)
            return True
        except Exception as e:
            logger.error("GraphState.transition %s → %s failed: %s", self.status, target, e, exc_info=True)
            try:
                self.status = GraphStatus.FAILED
                self.error = f"transition failed: {e}"
            except Exception:
                pass
            return False

    def increment_iteration(self) -> bool:
        """Advance iteration_count, enforcing the circuit breaker.

        Returns True if incremented and still IN_PROGRESS.
        Returns False and transitions to FAILED if the budget is exceeded (graceful).
        Never raises.
        """
        try:
            self.iteration_count += 1
            if self.iteration_count > self.max_iterations:
                msg = f"Max graph iterations ({self.max_iterations}) reached — circuit breaker tripped"
                logger.warning("%s (session %s, iter=%d)", msg, self.session_id, self.iteration_count)
                self.transition(GraphStatus.FAILED, error=msg)
                return False
            logger.debug("GraphState iteration %d/%d (session %s)", self.iteration_count, self.max_iterations, self.session_id)
            return True
        except Exception as e:
            logger.error("GraphState.increment_iteration failed: %s", e, exc_info=True)
            try:
                self.transition(GraphStatus.FAILED, error=f"increment failed: {e}")
            except Exception:
                pass
            return False

    # ── code_files management ──────────────────────────────────────

    def upsert_code_file(self, path: str, content: str) -> bool:
        """Record/update a file's contents in the state snapshot.

        Prunes by DEFAULT_MAX_CODE_FILES and per-file byte cap, with actionable logs.
        Never raises.
        """
        try:
            if not path or not isinstance(path, str):
                logger.warning("GraphState.upsert_code_file: invalid path %r — skipping", path)
                return False
            if content is None:
                content = ""
            if len(content) > DEFAULT_MAX_CODE_FILE_BYTES:
                logger.warning("GraphState.upsert_code_file %s exceeds %d bytes (%d) — truncating", path, DEFAULT_MAX_CODE_FILE_BYTES, len(content))
                content = content[:DEFAULT_MAX_CODE_FILE_BYTES] + "\n... [code_file truncated]"
            if path not in self.code_files and len(self.code_files) >= DEFAULT_MAX_CODE_FILES:
                # Evict oldest (first inserted) to keep cap bounded.
                oldest = next(iter(self.code_files))
                logger.warning("GraphState code_files cap %d reached — evicting %s to add %s (session %s)", DEFAULT_MAX_CODE_FILES, oldest, path, self.session_id)
                self.code_files.pop(oldest)
            self.code_files[path] = content
            return True
        except Exception as e:
            logger.error("GraphState.upsert_code_file %s failed: %s", path, e, exc_info=True)
            return False

    def sync_from_tracker(self, tracker: Any | None) -> int:
        """Sync code_files from a ChangeTracker/workspace (best-effort).

        Reads each changed file from disk via the tracker's workspace. Returns count synced.
        Never raises.
        """
        if tracker is None:
            return 0
        try:
            files = tracker.get_changed_files() if hasattr(tracker, "get_changed_files") else []
            workspace = getattr(tracker, "workspace", self.workspace) or self.workspace
            synced = 0
            for rel in files[:DEFAULT_MAX_CODE_FILES]:
                try:
                    from pathlib import Path
                    full = Path(workspace).resolve() / rel
                    if full.exists() and full.is_file() and full.stat().st_size <= DEFAULT_MAX_CODE_FILE_BYTES:
                        content = full.read_text(encoding="utf-8", errors="replace")
                        if self.upsert_code_file(rel, content):
                            synced += 1
                except Exception as e:
                    logger.debug("sync_from_tracker: failed to read %s: %s", rel, e)
            return synced
        except Exception as e:
            logger.warning("GraphState.sync_from_tracker failed: %s", e, exc_info=True)
            return 0

    # ── execution_logs management ──────────────────────────────────

    def add_execution_log(self, log: ExecutionLog | dict[str, Any] | str, *, command: str = "") -> bool:
        """Append a structured or raw execution log with pruning.

        Accepts ExecutionLog, a dict, or a raw run_bash string; normalizes and
        enforces caps. Never raises.
        """
        try:
            if isinstance(log, ExecutionLog):
                entry = log
            elif isinstance(log, dict):
                entry = ExecutionLog.from_dict(log)
            elif isinstance(log, str):
                entry = ExecutionLog.from_raw(command or "run_bash", log)
            else:
                logger.warning("GraphState.add_execution_log unknown type %s — skipping", type(log).__name__)
                return False

            # Prune by count
            if len(self.execution_logs) >= DEFAULT_MAX_LOGS:
                evicted = self.execution_logs.pop(0)
                logger.debug("GraphState execution_logs cap %d — evicted oldest command %r", DEFAULT_MAX_LOGS, evicted.command[:60])

            # Prune by per-entry chars (prune payload, not drop entry)
            if len(entry.stdout) > DEFAULT_MAX_LOG_CHARS:
                logger.warning("GraphState execution log stdout too large (%d) — pruning (command %r)", len(entry.stdout), entry.command[:40])
                entry.stdout = entry.stdout[:DEFAULT_MAX_LOG_CHARS] + "\n... [log pruned]"
                entry.truncated = True
            if len(entry.stderr) > DEFAULT_MAX_LOG_CHARS:
                entry.stderr = entry.stderr[:DEFAULT_MAX_LOG_CHARS] + "\n... [log pruned]"
                entry.truncated = True
            if len(entry.raw) > DEFAULT_MAX_LOG_CHARS * 2:
                entry.raw = entry.raw[:DEFAULT_MAX_LOG_CHARS * 2] + "\n... [raw pruned]"

            self.execution_logs.append(entry)
            return True
        except Exception as e:
            logger.error("GraphState.add_execution_log failed (command %r): %s", command[:40], e, exc_info=True)
            return False

    def last_execution(self) -> ExecutionLog | None:
        return self.execution_logs[-1] if self.execution_logs else None

    @property
    def last_exit_code(self) -> int | None:
        log = self.last_execution()
        return log.exit_code if log is not None else None

    @property
    def last_succeeded(self) -> bool | None:
        log = self.last_execution()
        return log.succeeded if log is not None else None

    # ── Oscillation / loop detection ───────────────────────────────

    def _state_hash(self) -> str:
        """Stable hash of (code_files keys + messages tail + status) for oscillation detection."""
        try:
            parts = [
                ",".join(sorted(self.code_files.keys())),
                str(sorted((log.command, log.exit_code) for log in self.execution_logs[-5:])),
                str(self.status),
                json.dumps(self.messages[-2:], sort_keys=True, default=str) if self.messages else "",
            ]
            raw = "|".join(parts)
            return hashlib.sha256(raw.encode()).hexdigest()[:16]
        except Exception:
            return str(time.time())

    def check_oscillation(self, window: int = 3) -> bool:
        """Return True if oscillation (repeated identical state hash) is detected.

        If detected, the caller should break the loop gracefully. Never raises.
        """
        try:
            h = self._state_hash()
            self._recent_hashes.append(h)
            if len(self._recent_hashes) > window * 2:
                self._recent_hashes = self._recent_hashes[-(window * 2):]
            # Oscillation: same hash appears `window` times in the recent window
            if len(self._recent_hashes) >= window:
                recent = self._recent_hashes[-window:]
                if len(set(recent)) == 1:
                    logger.warning(
                        "GraphState oscillation detected: state hash %s repeated %d times (session %s, iter=%d) — breaker tripping",
                        h, window, self.session_id, self.iteration_count,
                    )
                    return True
            return False
        except Exception as e:
            logger.debug("GraphState.check_oscillation failed: %s", e, exc_info=True)
            return False

    def is_terminal(self) -> bool:
        return self.status in (GraphStatus.COMPLETED, GraphStatus.FAILED, GraphStatus.NEEDS_HUMAN_REVIEW)

    def is_failed(self) -> bool:
        return self.status == GraphStatus.FAILED

    # ── Human-approval helpers ─────────────────────────────────────

    def mark_needs_review(self, tool_name: str, args: dict[str, Any], reason: str = "") -> bool:
        """Enter NEEDS_HUMAN_REVIEW with a bookmarked tool call.

        Never raises.
        """
        try:
            self.pending_approval = {"name": str(tool_name), "arguments": dict(args or {}), "reason": str(reason)[:500]}
            return self.transition(GraphStatus.NEEDS_HUMAN_REVIEW)
        except Exception as e:
            logger.error("GraphState.mark_needs_review failed: %s", e, exc_info=True)
            return False

    def clear_review(self) -> None:
        try:
            self.pending_approval = None
            if self.status == GraphStatus.NEEDS_HUMAN_REVIEW:
                self.status = GraphStatus.IN_PROGRESS
                self.error = None
        except Exception as e:
            logger.warning("GraphState.clear_review failed: %s", e, exc_info=True)

    # ── Iteration budget helpers (reconcile spec vs config) ───────

    @staticmethod
    def resolve_max_iterations(config: Any | None, *, graph_default: int = 5) -> int:
        """Resolve the effective graph max_iterations from config or fallback.

        Spec says 5 (outer graph); engine config says 50 (inner turn). This helper
        prefers an explicit graph budget if present, else maps the engine budget
        down to a bounded outer budget to honor the spec without breaking existing
        config.max_iterations consumers.
        """
        if config is None:
            return graph_default
        # Prefer a dedicated graph budget if any caller sets it (future-proof).
        for attr in ("graph_max_iterations", "agent_graph_max_iterations"):
            try:
                v = getattr(config, attr, None)
                if v is not None:
                    return max(1, min(200, int(v)))
            except Exception:
                continue
        # Fall back to engine's max_iterations but clamp to spec's tighter budget
        # when the graph is the caller — avoids 50-iteration graph loops by accident.
        try:
            v = getattr(config, "max_iterations", None)
            if v is not None:
                # Reconcile: graph default 5 wins unless caller explicitly opts into a larger graph.
                return graph_default
        except Exception:
            pass
        return graph_default
