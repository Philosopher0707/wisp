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
