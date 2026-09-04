"""Compensation records (M3, pure). File edits keep patch/diff records with
rollback previews; external tools declare reversibility. No tool wiring —
execution layers consult these declarations in later milestones.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class EditRecord:
    """One applied file mutation, sufficient to preview its rollback."""

    path: str
    unified_diff: str = ""
    pre_image_hash: str = ""
    reversible: bool = True
    note: str = ""
    version: int = 1


def rollback_preview(record: EditRecord) -> str:
    """Human-readable rollback instructions (no side effects)."""
    if not record.reversible:
        return (
            f"{record.path}: cannot be rolled back automatically "
            f"({record.note or 'no compensation recorded'}) — "
            "manual intervention required."
        )
    lines = [f"{record.path}: revert with `git checkout -- {record.path}`"]
    if record.pre_image_hash:
        lines.append(f"pre-image sha256: {record.pre_image_hash}")
    if record.unified_diff:
        lines.append("recorded diff:")
        lines.append(record.unified_diff)
    return "\n".join(lines)


# Reversibility declarations by tool name. "unknown" means the executor
# must ask before assuming compensation exists.
_REVERSIBILITY: dict[str, str] = {
    "read_file": "reversible",
    "list_files": "reversible",
    "write_file": "reversible",  # patch/diff record + pre-image
    "edit_file": "reversible",
    "git_status": "reversible",
    "git_diff": "reversible",
    "git_commit": "reversible",  # revert commit
    "git_push": "irreversible",  # published history
}


def reversibility(tool_name: str) -> str:
    """reversible | irreversible | unknown."""
    return _REVERSIBILITY.get(tool_name, "unknown")
