"""Workspace trust classification (M2 authority layer, pure)."""
from __future__ import annotations
from enum import StrEnum
from pathlib import Path
from typing import FrozenSet


class WorkspaceTrust(StrEnum):
    TRUSTED = "trusted"
    REVIEW_REQUIRED = "review_required"
    READ_ONLY = "read_only"
    QUARANTINED = "quarantined"


QUARANTINE_MARKER = ".wisp-quarantine"


def refuses(trust: WorkspaceTrust, *, is_read: bool) -> str | None:
    """The L2 refusal reason for this trust level, or `None` when it permits.

    **One implementation, two callers** (ADR-0068 R1). `auth/decision`'s L2 and the REST
    gate's `require_tool_allowed` must not drift on *what a quarantined workspace refuses* —
    they are the same rule, and before this function the REST gate did not apply it at all
    unless an organization bundle happened to be loaded.

    Takes `is_read` rather than a tool name so this module stays pure and keeps its
    dependency-light claim: the risk table lives in `wisp.core.contracts`, and importing it
    here would make the trust classifier depend on the tool vocabulary.
    """
    if is_read:
        return None
    if trust == WorkspaceTrust.QUARANTINED:
        return "quarantined workspace: non-read tools denied"
    if trust == WorkspaceTrust.READ_ONLY:
        return "read-only workspace: mutation denied"
    return None


def classify_workspace(path: str | Path,
                       trusted_roots: FrozenSet[str | Path] = frozenset(),
                       read_only_roots: FrozenSet[str | Path] = frozenset()) -> WorkspaceTrust:
    """Classify a workspace path. Pure function of path + policy sets.

    Order: quarantine marker > trusted roots > read-only roots > default
    review-required. Quarantine is sticky: a marker file opts out of trust
    regardless of roots.
    """
    p = Path(path).resolve()
    if (p / QUARANTINE_MARKER).exists():
        return WorkspaceTrust.QUARANTINED
    for root in trusted_roots:
        r = Path(root).resolve()
        if p == r or r in p.parents:
            return WorkspaceTrust.TRUSTED
    for root in read_only_roots:
        r = Path(root).resolve()
        if p == r or r in p.parents:
            return WorkspaceTrust.READ_ONLY
    return WorkspaceTrust.REVIEW_REQUIRED
