"""Filesystem tools for Wisp — read, write, edit, list files.

All operations respect workspace boundaries and file size limits.
"""

import logging
from pathlib import Path

from wisp.tools._utils import (
    _resolve_path,
    _validate_string,
    _validate_int,
    _MAX_READ_SIZE,
    _MAX_WRITE_SIZE,
    _MAX_OLD_TEXT_LENGTH,
    _file_lock_ctx,
    _change_tracker_ctx,
    _get_dependents,
    _fuzzy_find_text,
    _is_hook_controlled_path,
)
from wisp.tools.errors import ToolError

logger = logging.getLogger(__name__)


def tool_read_file(path: str, workspace: str, offset: int = 0, limit: int = 1_000_000) -> str:
    """Read the contents of a file within the workspace. Returns entire file by default."""
    _validate_string(path, "path")
    offset = _validate_int(offset, "offset", 0)
    limit = _validate_int(limit, "limit", 1, 1_000_000)
    full_path = _resolve_path(path, workspace)

    # ── Security: block edits to hook-controlled directories ──
    if _is_hook_controlled_path(str(full_path)):
        raise ToolError(
            f"Access denied: {path} is inside a hook-controlled directory. "
            f"Hooks are executed with the full process environment and cannot "
            f"be created or modified by agent tools to prevent privilege escalation."
        )

    if not full_path.exists():
        raise ToolError(f"File not found: {path}")
    if not full_path.is_file():
        raise ToolError(f"Not a file: {path}")

    # Check file size before reading
    size = full_path.stat().st_size
    if size > _MAX_READ_SIZE:
        raise ToolError(
            f"File too large: {path} is {size / 1024 / 1024:.1f} MB "
            f"(max read: {_MAX_READ_SIZE / 1024 / 1024:.0f} MB). "
            f"Use offset/limit to read portions."
        )

    content = full_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    total = len(lines)

    selected = lines[offset:offset + limit]
    shown = min(offset + limit, total)
    # Always include a header with file metadata so the agent knows
    # the actual size and whether truncation occurred (either here or
    # later in the pipeline).
    header = f"--- FILE: {path} | LINES: {total} | SHOWING: {offset+1}-{shown} ---\n"
    result = header + "".join(selected)
    logger.debug("Read %s (%d/%d lines)", path, len(selected), total)
    return result


def tool_write_file(path: str, workspace: str, content: str, file_lock=None) -> dict:
    """Write content to a file (creates or overwrites).

    Returns a structured dict with diff metadata for CLI rendering.
    """
    _validate_string(path, "path")
    _validate_string(content, "content", _MAX_WRITE_SIZE, allow_empty=True)
    full_path = _resolve_path(path, workspace)

    # ── Security: block writes to hook-controlled directories ──
    if _is_hook_controlled_path(str(full_path)):
        raise ToolError(
            f"Access denied: {path} is inside a hook-controlled directory. "
            f"Hooks are executed with the full process environment and cannot "
            f"be created or modified by agent tools to prevent privilege escalation."
        )

    # Size check
    if len(content) > _MAX_WRITE_SIZE:
        raise ToolError(
            f"Content too large: {len(content)} bytes "
            f"(max write: {_MAX_WRITE_SIZE / 1024 / 1024:.0f} MB)"
        )

    # ── Collaborative editing: check lock ──
    lock = file_lock or _file_lock_ctx.get()
    if lock and not lock.acquire(path):
        lock_info = lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    # Read old content for diff (before overwriting)
    old_content = None
    if full_path.exists():
        logger.warning("Overwriting existing file: %s (%d bytes)", path, full_path.stat().st_size)
        try:
            old_content = full_path.read_text(encoding="utf-8")
        except Exception:
            pass  # Binary or unreadable — skip diff

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    logger.info("Wrote %d bytes to %s", len(content), path)

    # ── Collaborative editing: record change ──
    tracker = _change_tracker_ctx.get()
    if tracker:
        tracker.record_write(path, content)

    # Release lock after write
    if lock:
        lock.release(path)

    # Generate diff: for new files, all lines are additions;
    # for overwrites, show the actual LCS diff.
    diff = ""
    if old_content is not None and old_content == content:
        pass  # No changes
    else:
        try:
            from wisp.diff import generate_diff_string
            if old_content is not None:
                # Overwrite — show actual diff
                result = generate_diff_string(old_content, content, context_lines=3)
                diff = result.diff
            else:
                # New file — all lines are additions
                lines = content.split("\n")
                diff = "\n".join(
                    f"+{i+1} {line}" for i, line in enumerate(lines)
                )
        except Exception:
            pass  # Diff generation failure is non-critical

    return {
        "status": "ok",
        "data": f"✓ Wrote {len(content)} bytes to {path}",
        "metadata": {
            "path": path,
            "size": len(content),
            "bytes_written": len(content),
            "diff": diff,
        },
    }


def tool_edit_file(path: str, workspace: str, old_text: str, new_text: str, file_lock=None) -> dict:
    """Replace exact text in a file (surgical edit).

    Uses Unicode-aware fuzzy matching (smart quotes, dashes, special spaces)
    when exact matching fails. Returns a structured JSON result with diff.
    """
    from wisp.diff import EditOp, apply_edit_with_diff

    _validate_string(path, "path")
    _validate_string(old_text, "old_text", _MAX_OLD_TEXT_LENGTH)
    _validate_string(new_text, "new_text", _MAX_WRITE_SIZE, allow_empty=True)
    full_path = _resolve_path(path, workspace)

    # ── Security: block edits to hook-controlled directories ──
    if _is_hook_controlled_path(str(full_path)):
        raise ToolError(
            f"Access denied: {path} is inside a hook-controlled directory. "
            f"Hooks are executed with the full process environment and cannot "
            f"be created or modified by agent tools to prevent privilege escalation."
        )

    if not full_path.exists():
        raise ToolError(f"File not found: {path}")

    size = full_path.stat().st_size
    if size > _MAX_READ_SIZE:
        raise ToolError(
            f"File too large: {path} is {size / 1024 / 1024:.1f} MB "
            f"(max edit: {_MAX_READ_SIZE / 1024 / 1024:.0f} MB)."
        )

    # ── Collaborative editing: check lock ──
    lock = file_lock or _file_lock_ctx.get()
    if lock and not lock.acquire(path):
        lock_info = lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    try:
        result = apply_edit_with_diff(path, [EditOp(old_text=old_text, new_text=new_text)], workspace)

        if not result.success:
            raise ToolError(result.error or "Edit failed")

        # ── Collaborative editing: record change ──
        tracker = _change_tracker_ctx.get()
        if tracker:
            tracker.record_edit(path, old_text, new_text)

        logger.info(
            "Edited %s — %d chars replaced with %d chars%s",
            path, result.old_length, result.new_length,
            " (fuzzy)" if result.used_fuzzy_match else "",
        )

        dependents = _get_dependents(path, workspace)
        dep_note = ""
        if dependents:
            dep_note = f"\n⚠️  {len(dependents)} file(s) depend on this file: {', '.join(dependents[:5])}"
            if len(dependents) > 5:
                dep_note += f" and {len(dependents) - 5} more"

        return {
            "status": "ok",
            "data": f"✓ Edited {path} — {result.old_length} chars replaced with {result.new_length} chars{dep_note}",
            "metadata": {
                "path": path,
                "old_length": result.old_length,
                "new_length": result.new_length,
                "edits_applied": result.edits_applied,
                "used_fuzzy_match": result.used_fuzzy_match,
                "diff": result.diff,
                "first_changed_line": result.first_changed_line,
                "dependents": dependents,
            },
        }

    finally:
        if lock:
            lock.release(path)


def tool_edit_file_multi(path: str, workspace: str, edits: list[dict], file_lock=None) -> dict:
    """Make multiple precise edits to a single file in one call.

    All edits[].old_text values are matched against the ORIGINAL file content
    (not incrementally). Edits must not overlap. Uses Unicode-aware fuzzy
    matching when exact matching fails.

    Args:
        path: Path to the file to edit.
        edits: List of {"old_text": str, "new_text": str} objects.
    """
    from wisp.diff import EditOp, apply_edit_with_diff

    _validate_string(path, "path")
    if not isinstance(edits, list) or len(edits) == 0:
        raise ToolError("edits must be a non-empty array of {old_text, new_text} objects")
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ToolError(f"edits[{i}] must be an object with old_text and new_text")
        _validate_string(edit.get("old_text", ""), f"edits[{i}].old_text", _MAX_OLD_TEXT_LENGTH)
        _validate_string(edit.get("new_text", ""), f"edits[{i}].new_text", _MAX_WRITE_SIZE, allow_empty=True)

    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        raise ToolError(f"File not found: {path}")

    size = full_path.stat().st_size
    if size > _MAX_READ_SIZE:
        raise ToolError(
            f"File too large: {path} is {size / 1024 / 1024:.1f} MB "
            f"(max edit: {_MAX_READ_SIZE / 1024 / 1024:.0f} MB)."
        )

    # ── Collaborative editing: check lock ──
    lock = file_lock or _file_lock_ctx.get()
    if lock and not lock.acquire(path):
        lock_info = lock.lock_info(path)
        holder = lock_info.get("agent", "unknown") if lock_info else "unknown"
        raise ToolError(f"File {path} is locked by {holder}. Wait or coordinate before editing.")

    try:
        ops = [EditOp(old_text=e["old_text"], new_text=e["new_text"]) for e in edits]
        result = apply_edit_with_diff(path, ops, workspace)

        if not result.success:
            raise ToolError(result.error or "Edit failed")

        # ── Collaborative editing: record changes ──
        tracker = _change_tracker_ctx.get()
        if tracker:
            for edit in edits:
                tracker.record_edit(path, edit["old_text"], edit["new_text"])

        logger.info(
            "Multi-edited %s — %d edits, %d→%d chars",
            path, result.edits_applied, result.old_length, result.new_length,
        )

        dependents = _get_dependents(path, workspace)
        dep_note = ""
        if dependents:
            dep_note = f"\n⚠️  {len(dependents)} file(s) depend on this file: {', '.join(dependents[:5])}"
            if len(dependents) > 5:
                dep_note += f" and {len(dependents) - 5} more"

        return {
            "status": "ok",
            "data": f"✓ Applied {result.edits_applied} edit(s) to {path} — {result.old_length} chars replaced with {result.new_length} chars{dep_note}",
            "metadata": {
                "path": path,
                "old_length": result.old_length,
                "new_length": result.new_length,
                "edits_applied": result.edits_applied,
                "used_fuzzy_match": result.used_fuzzy_match,
                "diff": result.diff,
                "first_changed_line": result.first_changed_line,
                "dependents": dependents,
            },
        }

    finally:
        if lock:
            lock.release(path)


def tool_list_files(path: str, workspace: str, pattern: str = "*") -> str:
    """List files and directories, optionally matching a pattern."""
    _validate_string(path, "path")
    _validate_string(pattern, "pattern", 200)
    full_path = _resolve_path(path, workspace)
    if not full_path.exists():
        raise ToolError(f"Path not found: {path}")
    if not full_path.is_dir():
        return f"(not a directory) {path}"

    # Prevent glob traversal with '..' or absolute patterns
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ToolError(f"Invalid pattern: '{pattern}' — path traversal not allowed")

    try:
        entries = list(full_path.glob(pattern))
    except (ValueError, OSError) as e:
        raise ToolError(f"Invalid glob pattern '{pattern}': {e}")

    if not entries:
        return f"(no matches for '{pattern}' in {path})"

    result = []
    max_entries = 500  # prevent huge directories from blowing context
    ws_path = Path(workspace).resolve()
    for e in sorted(entries)[:max_entries]:
        kind = "📁" if e.is_dir() else "📄"
        size = e.stat().st_size if e.is_file() else 0
        name = str(e.relative_to(ws_path))
        if e.is_dir():
            result.append(f"{kind} {name}/")
        else:
            result.append(f"{kind} {name} ({size:,} bytes)")

    if len(entries) > max_entries:
        result.append(f"... and {len(entries) - max_entries} more entries")

    logger.debug("Listed %s with pattern '%s' — %d entries", path, pattern, len(entries))
    return "\n".join(result)
