"""Thin harness primitives (3 execution boundaries, not 42 schemas).

Small models thrash on tool selection; frontier models burn attention on
schema bulk. These three primitives cover every effect the harness needs:

  1. ``exec_sandbox``  — unified command execution (timeout, ANSI strip).
  2. ``fs_mutate``     — surgical file read / write / search-replace.
  3. ``git_checkpoint`` — state capture view, diff, deterministic rewind.

Each primitive is a thin pydantic-validated wrapper over the existing
implementation (``wisp.tools.bash/filesystem/checkpoints`` + the sandbox
router) — no reimplemented semantics, so the tool contract (ToolError
shapes, output format, checkpointing) never changes. The 42-tool registry
stays the default; the core opts into this surface with
``thin_tools=True`` (``WispAgentCore._get_tool_schemas``).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from wisp.tools.errors import ToolError

logger = logging.getLogger(__name__)


# ── Argument models (pydantic = the arg validation) ────────────────────

class ExecSandboxArgs(BaseModel):
    command: str = Field(min_length=1, max_length=4096)
    timeout: int = Field(default=60, ge=1, le=3600)


class FsMutateArgs(BaseModel):
    op: Literal["read", "write", "edit"]
    path: str = Field(min_length=1)
    content: str = ""
    old_text: str = ""
    new_text: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=1_000_000, ge=1, le=1_000_000)


class GitCheckpointArgs(BaseModel):
    action: Literal["diff", "list", "restore"]
    seq: int = Field(default=0, ge=0)
    path: str = ""


def _validated(model: type[BaseModel], args: dict[str, Any], name: str) -> BaseModel:
    try:
        return model(**{k: v for k, v in args.items() if k in model.model_fields})
    except ValidationError as exc:
        raise ToolError(f"{name}: invalid arguments ({exc.errors()[0]['msg']})")


# ── Primitive 1: sandboxed execution ───────────────────────────────────

async def async_exec_sandbox(args: dict[str, Any], workspace: str) -> str:
    """Run a shell command through the sandbox router (Docker → PTY → host).

    Dangerous-command blocking and the model-facing output shape are
    inherited from ``async_tool_run_bash`` — confinement changes routing,
    never the contract.
    """
    from wisp.sandbox.router import get_router

    parsed = _validated(ExecSandboxArgs, args, "exec_sandbox")
    assert isinstance(parsed, ExecSandboxArgs)
    if "\x00" in parsed.command:
        raise ToolError("Null bytes not allowed in command")
    # Same deny-list gate as run_bash, before any provider is consulted.
    from wisp.tools._utils import check_dangerous_command

    danger = check_dangerous_command(parsed.command)
    if danger:
        raise ToolError(f"Dangerous command blocked: {danger}")
    from wisp.tools.bash import _format_bash_output

    returncode, stdout_str, stderr_str = await get_router(workspace).run(
        parsed.command, timeout=parsed.timeout)
    if returncode == -1 and "timed out" in stderr_str.lower():
        raise ToolError(f"Command timed out after {parsed.timeout}s")
    return _format_bash_output(returncode, stdout_str, stderr_str)


# ── Primitive 2: file mutation ─────────────────────────────────────────

async def async_fs_mutate(args: dict[str, Any], workspace: str) -> Any:
    """Read / write / surgical-edit one file (auto-checkpointed on mutate).

    Write/edit ops pass the in-memory post-image through the LSP compiler
    gate when a language-server manager is published on
    ``_lsp_manager_ctx`` (no manager = zero behavior change; gate failures
    fail open, only positive error evidence blocks).
    """
    from wisp.tools._utils import _lsp_manager_ctx, _resolve_path
    from wisp.tools.filesystem import tool_edit_file, tool_read_file, tool_write_file

    parsed = _validated(FsMutateArgs, args, "fs_mutate")
    assert isinstance(parsed, FsMutateArgs)
    if parsed.op == "read":
        return tool_read_file(path=parsed.path, workspace=workspace,
                              offset=parsed.offset, limit=parsed.limit)
    manager = _lsp_manager_ctx.get()
    if manager is not None:
        from wisp.core.lsp.gate import gate_from_manager, proposed_content

        gate = gate_from_manager(manager)
        if gate is not None:
            current: str | None = None
            if parsed.op == "edit":
                try:
                    current = _resolve_path(parsed.path, workspace).read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    current = None
            proposal = proposed_content(
                parsed.op, current, content=parsed.content,
                old_text=parsed.old_text, new_text=parsed.new_text)
            if proposal is not None:
                abs_path = str(_resolve_path(parsed.path, workspace))
                verdict = await gate.check(parsed.path, abs_path, proposal)
                if verdict.blocked:
                    raise ToolError(verdict.frame)
    if parsed.op == "write":
        return tool_write_file(path=parsed.path, workspace=workspace,
                               content=parsed.content)
    return tool_edit_file(path=parsed.path, workspace=workspace,
                          old_text=parsed.old_text, new_text=parsed.new_text)


# ── Primitive 3: checkpoint / diff / rewind ────────────────────────────

def _git(cmd: list[str], cwd: str) -> tuple[int, str]:
    import subprocess

    try:
        proc = subprocess.run(["git", *cmd], cwd=cwd, capture_output=True,
                              text=True, timeout=30)
        return proc.returncode, (proc.stdout or "")
    except Exception:
        return 1, ""


def git_diff_patch(workspace: str) -> str:
    """Unified diff of worktree vs HEAD (tracked + new files), never raises."""
    _git(["add", "-N", "."], workspace)  # intent-to-add: new files qualify
    rc, out = _git(["diff", "HEAD", "--", "."], workspace)
    if rc != 0 or not out.strip():
        return ""
    return out if out.endswith("\n") else out + "\n"


async def async_git_checkpoint(args: dict[str, Any], workspace: str) -> Any:
    """Inspect or restore agent-mutated state.

    ``diff`` = worktree patch (the SWE-bench ``model_patch`` surface);
    ``list`` = newest-first checkpoints; ``restore`` = rewind by seq/path.
    """
    from wisp.tools.checkpoints import tool_rewind

    parsed = _validated(GitCheckpointArgs, args, "git_checkpoint")
    assert isinstance(parsed, GitCheckpointArgs)
    if parsed.action == "diff":
        return git_diff_patch(workspace) or "(no changes)"
    if parsed.action == "list":
        return tool_rewind(workspace=workspace, list_only=True)
    return tool_rewind(workspace=workspace, seq=parsed.seq, path=parsed.path)


PRIMITIVE_IMPLS: dict[str, Any] = {
    "exec_sandbox": async_exec_sandbox,
    "fs_mutate": async_fs_mutate,
    "git_checkpoint": async_git_checkpoint,
}

PRIMITIVE_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "exec_sandbox",
            "description": (
                "Run a shell command (build, test, lint, git). Sandboxed "
                "(Docker, else isolated PTY, else host). Returns stdout with "
                "a stderr section; non-zero exits are prefixed [exit code: N]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "Shell command to execute"},
                    "timeout": {"type": "number",
                                "description": "Timeout in seconds",
                                "default": 60},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_mutate",
            "description": (
                "Read, create/overwrite, or surgically edit a file. Writes "
                "and edits auto-checkpoint for rewind. Returns file content "
                "with a header (read) or a diff receipt (write/edit)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string",
                           "enum": ["read", "write", "edit"]},
                    "path": {"type": "string",
                             "description": "Workspace-relative file path"},
                    "content": {"type": "string",
                                "description": "Full content (op=write)"},
                    "old_text": {"type": "string",
                                 "description": "Exact text to replace (op=edit)"},
                    "new_text": {"type": "string",
                                 "description": "Replacement (op=edit)"},
                },
                "required": ["op", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_checkpoint",
            "description": (
                "Inspect or restore mutated state. diff = worktree patch; "
                "list = checkpoints newest-first; restore = rewind one file "
                "by seq or path (rewind is itself rewindable)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["diff", "list", "restore"]},
                    "seq": {"type": "integer",
                            "description": "Checkpoint number (restore)"},
                    "path": {"type": "string",
                             "description": "File for latest-checkpoint restore"},
                },
                "required": ["action"],
            },
        },
    },
]


def thin_tool_names() -> list[str]:
    """The 3 primitive names — the model's entire effect surface in thin mode."""
    return list(PRIMITIVE_IMPLS)
