"""File checkpoints — pre-mutation snapshots with bounded retention (GH#15).

Every write_file/edit_file/edit_file_multi snapshots the prior content
BEFORE mutating, so a bad model edit is rewindable without git. Restore
snapshots the pre-restore state first, so rewind itself is rewindable.

In-memory, per workspace, dual-bounded (count + bytes, oldest evicted
first). Session-scoped by design: undo is a live-session operation, and
the durable record of WHAT changed already lives in ChangeTracker.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "get_checkpoint_store",
    "reset_checkpoint_stores",
    "DEFAULT_MAX_PER_FILE",
    "DEFAULT_MAX_BYTES",
]

DEFAULT_MAX_PER_FILE = 20
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Checkpoint:
    """One pre-mutation snapshot."""

    seq: int
    path: str  # workspace-relative, as the tool received it
    content: str | None  # None = file did not exist (restore deletes it)
    kind: str  # write | edit | edit_multi | rewind
    t: float = field(default_factory=time.time)

    @property
    def bytes(self) -> int:
        return len(self.content.encode("utf-8", "replace")) if self.content else 0


class CheckpointStore:
    """Thread-safe bounded snapshot ring for one workspace."""

    def __init__(self, max_per_file: int = DEFAULT_MAX_PER_FILE,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._max_per_file = max(1, max_per_file)
        self._max_bytes = max(1, max_bytes)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._by_file: dict[str, list[Checkpoint]] = {}
        self._bytes = 0

    def snapshot(self, path: str, content: str | None, kind: str) -> Checkpoint:
        """Record pre-mutation content; evict oldest-first past either bound."""
        cp = Checkpoint(next(self._seq), path, content, kind)
        with self._lock:
            ring = self._by_file.setdefault(path, [])
            ring.append(cp)
            self._bytes += cp.bytes
            while len(ring) > self._max_per_file:
                self._bytes -= ring.pop(0).bytes
            while self._bytes > self._max_bytes and self._total() > 1:
                self._evict_oldest_locked()
        return cp

    def drop(self, seq: int) -> bool:
        """Remove one snapshot (mutation failed — checkpoint must not outlive it)."""
        with self._lock:
            for path, ring in self._by_file.items():
                for i, cp in enumerate(ring):
                    if cp.seq == seq:
                        self._bytes -= cp.bytes
                        del ring[i]
                        if not ring:
                            del self._by_file[path]
                        return True
        return False

    def list(self, path: str | None = None) -> list[Checkpoint]:
        """Newest-first; optionally filtered to one file."""
        with self._lock:
            if path is not None:
                return list(reversed(self._by_file.get(path, [])))
            out = [cp for ring in self._by_file.values() for cp in ring]
            return sorted(out, key=lambda c: c.seq, reverse=True)

    def get(self, seq: int) -> Checkpoint | None:
        with self._lock:
            for ring in self._by_file.values():
                for cp in ring:
                    if cp.seq == seq:
                        return cp
        return None

    def _total(self) -> int:
        return sum(len(r) for r in self._by_file.values())

    def _evict_oldest_locked(self) -> None:
        oldest: Checkpoint | None = None
        oldest_path = ""
        for path, ring in self._by_file.items():
            if ring and (oldest is None or ring[0].seq < oldest.seq):
                oldest, oldest_path = ring[0], path
        if oldest is not None:
            self._by_file[oldest_path].pop(0)
            self._bytes -= oldest.bytes
            if not self._by_file[oldest_path]:
                del self._by_file[oldest_path]


_stores: dict[str, CheckpointStore] = {}
_stores_lock = threading.Lock()


def get_checkpoint_store(workspace: str) -> CheckpointStore:
    """Process-global registry keyed by resolved workspace (mirrors sandbox)."""
    key = str(Path(workspace).resolve())
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = _stores[key] = CheckpointStore()
        return store


def reset_checkpoint_stores() -> None:
    """Drop all stores (tests)."""
    with _stores_lock:
        _stores.clear()


def snapshot_before_mutation(workspace: str, path: str, kind: str) -> Checkpoint | None:
    """Read-and-stash current content; None only when the read itself fails.

    Best-effort by contract: a checkpoint must never break the mutation it
    guards — read failures (binary, permissions) yield no snapshot, and the
    write proceeds. Callers drop the snapshot if the mutation then fails.
    """
    from wisp.tools._utils import _resolve_path

    try:
        full = _resolve_path(path, workspace)
        content = full.read_text(encoding="utf-8") if full.is_file() else None
    except Exception:
        logger.debug("Checkpoint read failed for %s — proceeding without", path)
        return None
    try:
        return get_checkpoint_store(workspace).snapshot(path, content, kind)
    except Exception:
        logger.debug("Checkpoint store failed for %s — proceeding without", path)
        return None


def tool_rewind(workspace: str, seq: int = 0, path: str = "",
                list_only: bool = False) -> dict:
    """List checkpoints or restore one (agent-callable undo).

    No target → list newest-first. ``seq`` restores that checkpoint;
    ``path`` restores the latest checkpoint for the file. Restore first
    snapshots the pre-restore state (kind=rewind), so rewind is rewindable.
    A checkpoint with content None means "did not exist" → restore deletes.
    """
    from wisp.tools._utils import (
        _is_hook_controlled_path,
        _resolve_path,
        _safe_write_text,
    )
    from wisp.tools.errors import ToolError

    store = get_checkpoint_store(workspace)
    if list_only or (not seq and not path):
        entries = store.list(path or None)[:50]
        lines = [f"#{c.seq} {c.kind:10s} {c.path} "
                 f"({'deleted' if c.content is None else f'{c.bytes}B'})" for c in entries]
        return {
            "status": "ok",
            "data": "\n".join(lines) if lines else "(no checkpoints)",
            "metadata": {"count": len(entries)},
        }

    cp = store.get(int(seq)) if seq else None
    if cp is None and path:
        cands = store.list(path)
        cp = cands[0] if cands else None
    if cp is None:
        raise ToolError(f"No checkpoint found (seq={seq or '-'} path={path or '-'})")

    full = _resolve_path(cp.path, workspace)
    if _is_hook_controlled_path(str(full)):
        raise ToolError(f"Access denied: {cp.path} is inside a hook-controlled directory.")

    # Snapshot pre-restore state first — rewind stays rewindable.
    try:
        current = full.read_text(encoding="utf-8") if full.is_file() else None
    except Exception:
        current = None
    store.snapshot(cp.path, current, "rewind")

    if cp.content is None:
        try:
            full.unlink(missing_ok=True)
        except Exception as e:
            raise ToolError(f"Rewind delete failed: {e}")
        action = "deleted (file did not exist at checkpoint)"
    else:
        full.parent.mkdir(parents=True, exist_ok=True)
        _safe_write_text(cp.path, workspace, cp.content, encoding="utf-8")
        action = f"restored {len(cp.content)} chars"
    logger.info("Rewound %s to checkpoint #%d (%s)", cp.path, cp.seq, cp.kind)
    return {
        "status": "ok",
        "data": f"✓ Rewound {cp.path} to checkpoint #{cp.seq} — {action}",
        "metadata": {"path": cp.path, "seq": cp.seq, "kind": cp.kind},
    }
