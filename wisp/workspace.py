"""Workspace isolation, ChangeSets, conflicts, merge, atomic apply (Phase 12).

Invariant: no parallel mutating worker silently mutates the canonical
workspace. Workers mutate isolated state (git worktrees via the existing
WorktreeManager, or bounded temp-dir copies); convergence goes through
explicit ChangeSets → deterministic conflict analysis → merge → atomic
apply with journal recovery.

Reuse: _resolve_path containment, snapshot_before_mutation checkpoints,
diff.parse_hunks, code_index symbols, ArtifactStore (large patches),
UnifiedStore DB file (journal table), GraphSecurityAuditor (merge
decisions). This module grants no authority: application still requires
the caller's normal ToolExecutor/approval path.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_CS_FILES = 100
MAX_FILE_BYTES = 1_000_000
MAX_PATCH_BYTES = 4_000_000
SNAPSHOT_MAX_FILES = 2000

CREATE, MODIFY, DELETE, RENAME = "CREATE", "MODIFY", "DELETE", "RENAME"
OPS = (CREATE, MODIFY, DELETE, RENAME)

NO_CONFLICT, SAME_RESULT, TEXT_CONFLICT, DELETE_MODIFY, RENAME_CONFLICT, \
    CREATE_COLLISION, STALE_BASE, INVALID = (
        "NO_CONFLICT", "SAME_RESULT", "TEXT_CONFLICT", "DELETE_MODIFY",
        "RENAME_CONFLICT", "CREATE_COLLISION", "STALE_BASE", "INVALID")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", "replace"))


def _contain(root: str, rel: str) -> str:
    """Realpath containment mirroring tools._utils._resolve_path."""
    base = os.path.realpath(root)
    if os.path.isabs(rel):
        raise ValueError(f"absolute path forbidden: {rel!r}")
    path = os.path.realpath(os.path.join(base, rel))
    if path != base and not path.startswith(base + os.sep):
        raise ValueError(f"path escapes workspace: {rel!r}")
    return path


def _norm_rel(path: str) -> str:
    if not isinstance(path, str):
        raise ValueError("path must be text")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise ValueError(f"control characters forbidden: {path!r}")
    rel = os.path.normpath(path).replace(os.sep, "/")
    if rel.startswith(("../", "/", "..")) or rel in (".", ""):
        raise ValueError(f"invalid relative path: {path!r}")
    if len(rel) > 512:
        raise ValueError("path too long")
    return rel


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Deterministic workspace state identity over a tracked file set."""

    id: str
    root: str
    files: dict[str, str]  # rel path -> sha256 hex
    base_revision: str = ""  # git HEAD or ""
    created_at: float = 0.0

    def file_hash(self, rel: str) -> str | None:
        return self.files.get(rel)


def snapshot_workspace(root: str, files: list[str] | None = None,
                       base_revision: str = "") -> WorkspaceSnapshot:
    """Hash tracked files (explicit list, or bounded top-level scan)."""
    root = os.path.abspath(root)
    explicit = files is not None
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in (".git", ".wisp", "__pycache__",
                                              "node_modules", ".venv", "target"))
            if os.path.relpath(dirpath, root).count(os.sep) > 6:
                dirnames.clear()
                continue
            for fn in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                files.append(rel.replace(os.sep, "/"))
                if len(files) >= SNAPSHOT_MAX_FILES:
                    break
            if len(files) >= SNAPSHOT_MAX_FILES:
                break
    hashes: dict[str, str] = {}
    for rel in files:
        try:
            rel = _norm_rel(rel)
        except ValueError:
            if explicit:
                raise
            continue
        # lstat BEFORE realpath: symlinks must never be tracked.
        if os.path.islink(os.path.join(root, rel)):
            if explicit:
                raise ValueError(f"symlink forbidden: {rel!r}")
            continue
        try:
            path = _contain(root, rel)
            if not os.path.isfile(path):
                if explicit:
                    raise ValueError(f"not a file: {rel!r}")
                continue
            if os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            with open(path, "rb") as fh:
                hashes[rel] = sha256_bytes(fh.read())
        except (OSError, ValueError):
            if explicit:
                raise
            continue
    sid = sha256_text(json.dumps({"root": root, "files": sorted(hashes.items()),
                                  "rev": base_revision}, sort_keys=True))[:32]
    return WorkspaceSnapshot(id=f"snap-{sid}", root=root, files=hashes,
                             base_revision=base_revision, created_at=time.time())


@dataclass(frozen=True)
class Change:
    path: str
    op: str
    base_hash: str = ""       # sha of base content ("" if CREATEd)
    content: str = ""         # new content (CREATE/MODIFY) or "" (DELETE)
    rename_from: str = ""     # RENAME only
    artifact: str = ""        # artifact:// ref for oversized content

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"bad op {self.op!r}")
        # Canonicalize once: containment and dedup must agree on identity.
        object.__setattr__(self, "path", _norm_rel(self.path))
        if self.op == RENAME:
            object.__setattr__(self, "rename_from", _norm_rel(self.rename_from))
        if len(self.content.encode()) > MAX_FILE_BYTES:
            raise ValueError("change content too large (use artifact ref)")


@dataclass(frozen=True)
class ChangeSet:
    """Immutable proposed mutation. Producer identity is host-assigned."""

    id: str
    base_snapshot: str
    producer: dict[str, str]  # run_id/node_id/worker (host, never model)
    changes: tuple[Change, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.changes) > MAX_CS_FILES:
            raise ValueError("too many changes")
        seen = set()
        for c in self.changes:
            key = (c.op, c.path)
            if key in seen:
                raise ValueError(f"duplicate change {key}")
            seen.add(key)

    @property
    def affected_files(self) -> tuple[str, ...]:
        return tuple(sorted({c.path for c in self.changes} |
                            {c.rename_from for c in self.changes if c.rename_from}))

    def affected_symbols(self, workspace: str) -> dict[str, list[str]]:
        """Best-effort symbol attribution via existing code index."""
        try:
            from wisp.code_index import build_index
            index = build_index(workspace)
        except Exception:
            return {}
        out: dict[str, list[str]] = {}
        for c in self.changes:
            syms = sorted({s.name for syms in index.symbols.values() for s in syms
                           if getattr(s, "file", "") == c.path} |
                          {s.name for s in index.symbols.get(c.path, [])})
            out[c.path] = syms[:32]
        return out


def make_changeset(base_snapshot: str, producer: dict[str, str],
                   changes: list[Change], metadata: dict | None = None) -> ChangeSet:
    """Content-addressed ID. Producer keys restricted to host identity."""
    prod = {k: str(v)[:128] for k, v in (producer or {}).items()
            if k in ("run_id", "node_id", "worker")}
    meta = {str(k)[:64]: str(v)[:256] for k, v in (metadata or {}).items()}
    body = json.dumps({"base": base_snapshot, "producer": prod,
                       "changes": [(c.op, c.path, c.base_hash,
                                    sha256_text(c.content), c.rename_from)
                                   for c in changes],
                       "meta": meta}, sort_keys=True)
    return ChangeSet(id=f"cs-{sha256_text(body)[:16]}", base_snapshot=base_snapshot,
                     producer=prod, changes=tuple(changes), metadata=meta)


def diff_copy_against_snapshot(copy_root: str, snap: WorkspaceSnapshot,
                               worker: str) -> list[Change]:
    """Collect an isolated copy's drift as Change list (bounded)."""
    changes: list[Change] = []
    present = [rel for rel in snap.files
               if os.path.isfile(os.path.join(copy_root, *rel.split("/")))]
    current = snapshot_workspace(copy_root, files=present)
    for rel, h in sorted(current.files.items()):
        if rel not in snap.files:
            changes.append(_read_change(copy_root, rel, CREATE, ""))
        elif snap.files[rel] != h:
            changes.append(_read_change(copy_root, rel, MODIFY, snap.files[rel]))
    for rel in sorted(set(snap.files) - set(current.files)):
        changes.append(Change(path=rel, op=DELETE, base_hash=snap.files[rel]))
    # New files absent from the snapshot file list (bounded scan).
    if len(changes) < MAX_CS_FILES:
        for dirpath, dirnames, filenames in os.walk(copy_root):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            if os.path.relpath(dirpath, copy_root).count(os.sep) > 6:
                dirnames.clear()
                continue
            for fn in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, fn),
                                      copy_root).replace(os.sep, "/")
                if rel in snap.files or rel in current.files:
                    continue
                full = os.path.join(dirpath, fn)
                if os.path.islink(full):
                    continue
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    continue
                changes.append(Change(path=rel, op=CREATE, content=content))
                if len(changes) >= MAX_CS_FILES:
                    break
            if len(changes) >= MAX_CS_FILES:
                break
    _ = worker
    return changes


def _read_change(root: str, rel: str, op: str, base_hash: str) -> Change:
    with open(_contain(root, rel), "r", encoding="utf-8", errors="replace") as fh:
        return Change(path=rel, op=op, base_hash=base_hash, content=fh.read())


def isolated_copy(snap: WorkspaceSnapshot, worker: str) -> str:
    """Bounded temp-dir copy of snapshot files. Caller cleans up."""
    dest = tempfile.mkdtemp(prefix=f"wisp-worker-{worker}-")
    for rel in sorted(snap.files):
        try:
            src = _contain(snap.root, rel)
            dst = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        except (OSError, ValueError):
            continue
    return dest


# ── conflict engine (12D) ─────────────────────────────────────────────

@dataclass(frozen=True)
class Conflict:
    kind: str
    path: str
    detail: str = ""
    parties: tuple[str, ...] = ()


def classify_pair(base: WorkspaceSnapshot, a: ChangeSet, b: ChangeSet) -> list[Conflict]:
    """Deterministic pairwise classification. LLM never decides conflicts."""
    out: list[Conflict] = []
    if a.base_snapshot != base.id or b.base_snapshot != base.id:
        stale = a.id if a.base_snapshot != base.id else b.id
        return [Conflict(STALE_BASE, "", f"changeset {stale} not based on {base.id}")]
    by_path_a = {c.path: c for c in a.changes}
    by_path_b = {c.path: c for c in b.changes}
    renames_a = {c.rename_from: c for c in a.changes if c.op == RENAME}
    renames_b = {c.rename_from: c for c in b.changes if c.op == RENAME}
    for path in sorted(set(by_path_a) | set(by_path_b)):
        ca, cb = by_path_a.get(path), by_path_b.get(path)
        if ca is None or cb is None:
            continue
        out.extend(_classify_change_pair(base, path, ca, cb, a.id, b.id))
    # Rename overlap: same source renamed differently, or rename target clash.
    for src in sorted(set(renames_a) & set(renames_b)):
        ra, rb = renames_a[src], renames_b[src]
        if (ra.path, sha256_text(ra.content)) != (rb.path, sha256_text(rb.content)):
            out.append(Conflict(RENAME_CONFLICT, src,
                                f"{a.id}→{ra.path} vs {b.id}→{rb.path}",
                                (a.id, b.id)))
    return out


def _classify_change_pair(base: WorkspaceSnapshot, path: str, ca: Change,
                          cb: Change, aid: str, bid: str) -> list[Conflict]:
    parties = (aid, bid)
    base_h = base.files.get(path)
    # CREATE/CREATE.
    if ca.op == CREATE and cb.op == CREATE:
        if sha256_text(ca.content) == sha256_text(cb.content):
            return [Conflict(SAME_RESULT, path, "identical creation", parties)]
        return [Conflict(CREATE_COLLISION, path, "divergent creation", parties)]
    # DELETE vs anything.
    if (ca.op == DELETE) != (cb.op == DELETE):
        other = cb if ca.op == DELETE else ca
        if other.op == CREATE and base_h is None:
            return [Conflict(CREATE_COLLISION, path, "create vs delete of absent file", parties)]
        return [Conflict(DELETE_MODIFY, path,
                         f"delete vs {other.op.lower()}", parties)]
    if ca.op == DELETE and cb.op == DELETE:
        return [Conflict(SAME_RESULT, path, "both deleted", parties)]
    # MODIFY/MODIFY (RENAME target handled as modify of new path).
    if sha256_text(ca.content) == sha256_text(cb.content):
        return [Conflict(SAME_RESULT, path, "identical result", parties)]
    base_text = _base_text(base, path)
    if base_text is not None and _disjoint_symbols(base, path, base_text,
                                                   ca.content, cb.content):
        return []  # NO_CONFLICT: disjoint symbol ranges.
    if base_text is not None and _disjoint_hunks(base_text, ca.content, cb.content):
        return []  # NO_CONFLICT: disjoint line ranges.
    return [Conflict(TEXT_CONFLICT, path, "overlapping edits", parties)]


def _base_text(base: WorkspaceSnapshot, path: str) -> str | None:
    try:
        with open(_contain(base.root, path), "r", encoding="utf-8",
                   errors="replace") as fh:
            content = fh.read()
    except (OSError, ValueError):
        return None
    if sha256_text(content) != base.files.get(path, ""):
        return None  # canonical moved under us: not this pair's call
    return content


def _symbol_of(symbols: list, line: int, total_lines: int) -> str:
    """Innermost symbol containing a 1-based line, else module scope.

    The index carries start lines only; spans are derived from the next
    sibling (or EOF), which is exact for non-nested defs and conservative
    otherwise (nested defs share the outer span).
    """
    ordered = sorted(symbols, key=lambda s: int(getattr(s, "line", 1) or 1))
    best, best_start = "<module>", 0
    for i, s in enumerate(ordered):
        start = int(getattr(s, "line", 1) or 1)
        if i + 1 < len(ordered):
            end = int(getattr(ordered[i + 1], "line", total_lines + 1) or 1) - 1
        else:
            end = total_lines
        if start <= line <= max(end, start) and start >= best_start:
            best, best_start = getattr(s, "name", "") or "<module>", start
    return best


def _disjoint_symbols(base: WorkspaceSnapshot, path: str, base_text: str,
                      a_text: str, b_text: str) -> bool:
    """Same file mergeable when edits fall in distinct top-level symbols."""
    try:
        from wisp.code_index import build_index
        syms = build_index(base.root).symbols.get(path, [])
    except Exception:
        return False
    if not syms:
        return False
    base_lines = base_text.splitlines()
    lines_a = _changed_lines(base_lines, a_text.splitlines())
    lines_b = _changed_lines(base_lines, b_text.splitlines())
    if not lines_a or not lines_b:
        return False
    total = len(base_lines)
    syms_a = {_symbol_of(syms, ln, total) for ln in lines_a}
    syms_b = {_symbol_of(syms, ln, total) for ln in lines_b}
    return bool(syms_a and syms_b and not (syms_a & syms_b)
                and "<module>" not in syms_a | syms_b)


def _changed_lines(base_lines: list[str], new_lines: list[str]) -> set[int]:
    out: set[int] = set()
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(a=base_lines, b=new_lines,
                                                     autojunk=False).get_opcodes():
        if tag != "equal":
            out.update(range(i1 + 1, i2 + 1))
    return out


def _disjoint_hunks(base_text: str, a_text: str, b_text: str) -> bool:
    sm_a = difflib.SequenceMatcher(a=base_text.splitlines(), b=a_text.splitlines(),
                                   autojunk=False)
    sm_b = difflib.SequenceMatcher(a=base_text.splitlines(), b=b_text.splitlines(),
                                   autojunk=False)
    ranges_a = [(i1, i2) for tag, i1, i2, _, _ in sm_a.get_opcodes() if tag != "equal"]
    ranges_b = [(i1, i2) for tag, i1, i2, _, _ in sm_b.get_opcodes() if tag != "equal"]
    if not ranges_a or not ranges_b:
        return False
    for a1, a2 in ranges_a:
        for b1, b2 in ranges_b:
            if a1 < b2 + 3 and b1 < a2 + 3:  # 3-line context guard
                return False
    return True


# ── deterministic merge (12E) ─────────────────────────────────────────

@dataclass(frozen=True)
class MergeResult:
    outcome: str  # MERGED | CONFLICT | STALE | INVALID
    merged: ChangeSet | None = None
    conflicts: tuple[Conflict, ...] = ()
    detail: str = ""


def merge_changesets(base: WorkspaceSnapshot, sets: list[ChangeSet],
                     producer: dict[str, str] | None = None) -> MergeResult:
    """Order by changeset id. Never last-writer-wins: any destructive overlap
    is CONFLICT. Same-result duplicates collapse deterministically."""
    for cs in sets:
        if not isinstance(cs, ChangeSet):
            return MergeResult("INVALID", detail="non-changeset input")
        if cs.base_snapshot != base.id:
            return MergeResult("STALE", detail=f"{cs.id} based on {cs.base_snapshot}")
    ordered = sorted(sets, key=lambda c: c.id)
    conflicts: list[Conflict] = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            conflicts.extend(classify_pair(base, ordered[i], ordered[j]))
    hard = [c for c in conflicts if c.kind not in (SAME_RESULT,)]
    if any(c.kind in (STALE_BASE, INVALID) for c in conflicts):
        return MergeResult("STALE", conflicts=tuple(conflicts),
                           detail="stale or invalid input")
    if hard:
        return MergeResult("CONFLICT", conflicts=tuple(conflicts),
                           detail=f"{len(hard)} conflicting paths")
    merged_changes: dict[tuple[str, str], Change] = {}
    for cs in ordered:
        for c in cs.changes:
            merged_changes.setdefault((c.op, c.path), c)
    try:
        merged = make_changeset(
            base.id, {**(producer or {}), "merged_from": ",".join(
                sorted(cs.id for cs in ordered))[:512]},
            [merged_changes[k] for k in sorted(merged_changes)])
    except ValueError as exc:
        return MergeResult("INVALID", detail=str(exc))
    return MergeResult("MERGED", merged=merged, conflicts=tuple(conflicts))


# ── atomic apply + rollback + recovery (12H) ──────────────────────────

JOURNAL_DIRNAME = "apply-journal"


def _journal_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), ".wisp", JOURNAL_DIRNAME)


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    applied: tuple[str, ...] = ()
    reason: str = ""  # EXTERNAL_MODIFICATION | VALIDATION | IO | ""


def _current_hashes(root: str, rels: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for rel in rels:
        try:
            path = _contain(root, rel)
            if os.path.isfile(path) and not os.path.islink(path):
                with open(path, "rb") as fh:
                    out[rel] = sha256_bytes(fh.read())
            else:
                out[rel] = None
        except (OSError, ValueError):
            out[rel] = None
    return out


def apply_changeset(root: str, base: WorkspaceSnapshot, cs: ChangeSet,
                    fail_at: str = "") -> ApplyResult:
    """Atomic-from-user-view apply: prepare → validate → stage → commit.

    Detects external modification (canonical drifted from base on any
    touched or tracked file) and refuses instead of overwriting. Journal
    enables crash recovery (recover() completes or rolls back).
    `fail_at` is a test-only fault injector ("stage"|"commit").
    """
    from wisp.tools.checkpoints import snapshot_before_mutation
    root = os.path.abspath(root)
    if cs.base_snapshot != base.id or base.root != root:
        return ApplyResult(False, reason="STALE")
    rels = sorted({c.path for c in cs.changes} | set(base.files))
    now = _current_hashes(root, rels)
    # Touched files must still match base; tracked-but-untouched files must
    # also match base (external edits anywhere in scope refuse the apply).
    for rel, h in base.files.items():
        if now.get(rel) != h:
            # Any drift in scope refuses the apply: never overwrite external
            # changes, whether on touched or untouched files.
            return ApplyResult(False, reason="EXTERNAL_MODIFICATION")
    for c in cs.changes:
        if c.op == CREATE and now.get(c.path) is not None:
            return ApplyResult(False, reason="EXTERNAL_MODIFICATION")
        if c.op in (MODIFY, DELETE) and now.get(c.path) != (c.base_hash or None):
            return ApplyResult(False, reason="EXTERNAL_MODIFICATION")
    journal = os.path.join(_journal_dir(root), f"{cs.id}.json")
    os.makedirs(os.path.dirname(journal), exist_ok=True)
    plan = [{"op": c.op, "path": c.path, "rename_from": c.rename_from,
             "content": c.content, "artifact": c.artifact} for c in cs.changes]
    pre = _read_pre_images(root, plan)
    _write_journal(journal, cs.id, base.id, plan, [], pre)
    if fail_at == "stage":
        raise RuntimeError("injected failure at stage")
    try:
        try:
            snapshot_before_mutation(root, "__apply__", "apply")
        except Exception:
            pass
        for c in cs.changes:
            try:
                if c.op != CREATE:
                    snapshot_before_mutation(root, c.path, "apply")
            except Exception:
                continue
        done = _commit_plan(root, journal, cs.id, base.id, plan, fail_at)
    except RuntimeError:
        raise  # test-only fault injector always propagates
    except Exception as exc:
        return ApplyResult(False, reason=f"IO: {exc}"[:256])
    _write_journal(journal, cs.id, base.id, plan, done, pre, completed=True)
    return ApplyResult(True, tuple(c.path for c in cs.changes))


def _read_pre_images(root: str, plan: list) -> dict[str, str | None]:
    """Exact pre-state per path (bounded by file caps). None = absent."""
    pre: dict[str, str | None] = {}
    for item in plan:
        for rel in ({item["path"], item["rename_from"]} - {""}):
            if rel in pre:
                continue
            try:
                path = _contain(root, rel)
                if os.path.isfile(path) and not os.path.islink(path):
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        pre[rel] = fh.read()
                else:
                    pre[rel] = None
            except (OSError, ValueError):
                pre[rel] = None
    return pre


def _post_hashes(plan: list) -> dict[str, str | None]:
    """Expected post-apply content hash per path (None = absent)."""
    post: dict[str, str | None] = {}
    for item in plan:
        if not isinstance(item, dict):
            continue
        if item.get("op") == DELETE:
            post[str(item.get("path", ""))] = None
        elif item.get("op") == RENAME:
            post[str(item.get("rename_from", ""))] = None
        else:
            content = item.get("content", "")
            post[str(item.get("path", ""))] = sha256_text(content) if isinstance(
                content, str) else None
    return post


def _write_journal(journal: str, cs_id: str, base_id: str, plan: list,
                   done: list[str], pre: dict | None = None,
                   completed: bool = False) -> None:
    with open(journal, "w", encoding="utf-8") as fh:
        json.dump({"changeset": cs_id, "base": base_id, "plan": plan,
                   "done": done, "pre": pre or {},
                   "post": _post_hashes(plan),
                   "completed": completed}, fh)
        fh.flush()
        os.fsync(fh.fileno())


def _op_applied(root: str, item: dict) -> bool:
    """Idempotence check: is this op's end state already present?"""
    try:
        if item["op"] == DELETE:
            return not os.path.exists(_contain(root, item["path"]))
        if item["op"] == RENAME:
            # Post-state: source gone, destination present. No content to
            # compare (bytes moved); pre-images live in checkpoints.
            return (not os.path.exists(_contain(root, item["rename_from"]))
                    and os.path.isfile(_contain(root, item["path"])))
        with open(_contain(root, item["path"]), "r", encoding="utf-8",
                   errors="replace") as fh:
            content = item["content"]
            if item["artifact"]:
                content = _load_artifact_content(item["artifact"], root)
            return fh.read() == content
    except (OSError, ValueError):
        return False


def _apply_op(root: str, tmpdir: str, item: dict) -> None:
    rel = item["path"]
    if item["op"] == DELETE:
        os.unlink(_contain(root, rel))
        return
    if item["op"] == RENAME:
        os.replace(_contain(root, item["rename_from"]), _contain(root, rel))
        return
    content = item["content"]
    if item["artifact"]:
        content = _load_artifact_content(item["artifact"], root)
    tmp = os.path.join(tmpdir, sha256_text(rel))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.makedirs(os.path.dirname(_contain(root, rel)), exist_ok=True)
    os.replace(tmp, _contain(root, rel))


def _commit_plan(root: str, journal: str, cs_id: str, base_id: str,
                 plan: list, fail_at: str = "") -> list[str]:
    """Idempotent commit: completed ops are skipped, so crash recovery
    converges to complete-new state by re-running this function."""
    try:
        with open(journal, encoding="utf-8") as fh:
            saved = json.load(fh)
            done = set(saved.get("done", []))
            pre = saved.get("pre", {})
    except (OSError, ValueError):
        done, pre = set(), {}
    tmpdir = tempfile.mkdtemp(prefix="wisp-apply-")
    try:
        for item in plan:
            if item["path"] in done or _op_applied(root, item):
                done.add(item["path"])
                continue
            _apply_op(root, tmpdir, item)
            done.add(item["path"])
            _write_journal(journal, cs_id, base_id, plan, sorted(done), pre)
            if fail_at == "commit" and len(done) == 1:
                raise RuntimeError("injected failure at commit")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return sorted(done)


def _load_artifact_content(uri: str, workspace: str) -> str:
    """Large payloads ride artifacts; resolved here, never trusted blindly."""
    from wisp.graph.artifacts import ArtifactStore
    from wisp.graph.store import GraphStore
    content = ArtifactStore(workspace=workspace,
                            store=GraphStore(workspace=workspace)).get(uri, "")
    if not isinstance(content, str):
        content = json.dumps(content)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise ValueError("artifact payload exceeds file limit")
    return content


def recover(root: str) -> str:
    """Crash recovery over journals. Pending journals complete forward
    (idempotent commit); completed journals are skipped; corrupt journals
    imply a crash during journal write — always before the first replace
    (journal is fsynced first) — so canonical is untouched and they drop.
    Returns disposition.
    """
    root = os.path.abspath(root)
    jdir = _journal_dir(root)
    if not os.path.isdir(jdir):
        return "clean"
    journals = sorted(f for f in os.listdir(jdir) if f.endswith(".json"))
    if not journals:
        return "clean"
    done, dropped = 0, 0
    for fn in journals:
        path = os.path.join(jdir, fn)
        try:
            with open(path, encoding="utf-8") as fh:
                journal = json.load(fh)
        except (OSError, ValueError):
            try:
                os.unlink(path)
            except OSError:
                pass
            dropped += 1
            continue
        if not isinstance(journal.get("plan"), list) or journal.get("completed"):
            if not isinstance(journal.get("plan"), list):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                dropped += 1
            continue  # completed application record, not pending work
        plan = journal["plan"]
        try:
            _commit_plan(root, path, str(journal.get("changeset", "")),
                         str(journal.get("base", "")), plan)
            _write_journal(path, str(journal.get("changeset", "")),
                           str(journal.get("base", "")), plan,
                           [p["path"] for p in plan if isinstance(p, dict)],
                           journal.get("pre", {}), completed=True)
            done += 1
        except (OSError, ValueError):
            dropped += 1
    if done and not dropped:
        return f"completed:{done}"
    if dropped and not done:
        return f"rolled-back:{dropped}"
    if not done and not dropped:
        return "clean"
    return f"completed:{done}+rolled-back:{dropped}"


def prune_journals(root: str, keep: int = 50) -> int:
    """Bound journal growth: keep newest application records."""
    jdir = _journal_dir(os.path.abspath(root))
    if not os.path.isdir(jdir):
        return 0
    entries = []
    for fn in os.listdir(jdir):
        if not fn.endswith(".json"):
            continue
        try:
            entries.append((os.path.getmtime(os.path.join(jdir, fn)), fn))
        except OSError:
            continue
    entries.sort()
    pruned = 0
    for _, fn in entries[:max(0, len(entries) - keep)]:
        try:
            os.unlink(os.path.join(jdir, fn))
            pruned += 1
        except OSError:
            pass
    return pruned


def rollback_changeset(root: str, base: WorkspaceSnapshot, cs: ChangeSet) -> ApplyResult:
    """Restore journal pre-images for a changeset applied from `base`.

    Refuses (rather than guessing) when: no completed journal for cs.id,
    journal pre-images incomplete, or canonical drifted since application.
    Rollback itself is a journaled idempotent apply, so it is crash-safe.
    """
    root = os.path.abspath(root)
    path = os.path.join(_journal_dir(root), f"{cs.id}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            journal = json.load(fh)
    except (OSError, ValueError):
        return ApplyResult(False, reason="no application record")
    if not journal.get("completed"):
        return ApplyResult(False, reason="application incomplete; recover first")
    pre = journal.get("pre", {})
    if not isinstance(pre, dict):
        return ApplyResult(False, reason="no pre-images")
    current = snapshot_workspace(root, files=list(base.files))
    # External-modification guard: every changed path must still hold exactly
    # what this changeset applied; otherwise refuse (never blindly overwrite).
    post = journal.get("post", {}) if isinstance(journal.get("post"), dict) else {}
    for c in cs.changes:
        if c.op == RENAME:
            if current.files.get(c.path) is None or \
                    current.files.get(c.rename_from) is not None:
                return ApplyResult(False, reason="EXTERNAL_MODIFICATION")
            continue
        if current.files.get(c.path) != post.get(c.path):
            return ApplyResult(False, reason="EXTERNAL_MODIFICATION")
    inverse: list[Change] = []
    for c in cs.changes:
        if c.path not in pre:
            return ApplyResult(False, reason=f"no pre-image for {c.path}")
        if c.op == CREATE:
            if current.files.get(c.path) is not None:
                inverse.append(Change(path=c.path, op=DELETE))
        elif c.op == DELETE:
            if pre[c.path] is None:
                return ApplyResult(False, reason=f"no pre-image for {c.path}")
            if current.files.get(c.path) is None:
                inverse.append(Change(path=c.path, op=CREATE, content=pre[c.path]))
        elif c.op == RENAME:
            if current.files.get(c.path) is not None:
                inverse.append(Change(path=c.path, op=DELETE))
            if pre.get(c.rename_from) is not None and \
                    current.files.get(c.rename_from) is None:
                inverse.append(Change(path=c.rename_from, op=CREATE,
                                      content=pre[c.rename_from]))
        else:
            if pre[c.path] is None or current.files.get(c.path) is None:
                continue  # already absent on both sides: nothing to restore
            inverse.append(Change(path=c.path, op=MODIFY,
                                  base_hash=current.files[c.path],
                                  content=pre[c.path]))
    if not inverse:
        return ApplyResult(True, (), reason="already reverted")
    try:
        inv = make_changeset(current.id, {"worker": "rollback"}, inverse)
    except ValueError as exc:
        return ApplyResult(False, reason=str(exc)[:256])
    return apply_changeset(root, current, inv)


# ── graph merge boundary (12F) ────────────────────────────────────────

def isolation_functions(workspace: str) -> dict[str, Any]:
    """Host function registry entries, workspace-bound at registration.

    The merge boundary trusts snapshot.root ONLY when it realpath-equals
    the executor workspace captured here — a forged snapshot pointing at
    /etc (or anywhere else) is INVALID, never applied.
    """
    ws = os.path.abspath(workspace)

    def prepare_isolation(inputs: dict[str, Any]) -> dict[str, Any]:
        files = inputs.get("files", [])
        workers = inputs.get("workers", [])
        if not isinstance(files, list) or not isinstance(workers, list):
            return {"error": "files[] and workers[] required"}
        files = sorted({str(f)[:512] for f in files[:200]})
        workers = [str(w)[:64] for w in workers[:16]]
        if not workers:
            return {"error": "no workers"}
        try:
            # Cap BEFORE hashing so the id covers exactly the tracked set.
            snap = snapshot_workspace(ws, files=files or None)
            if len(snap.files) > 200:
                snap = snapshot_workspace(ws, files=sorted(snap.files)[:200])
        except (OSError, ValueError) as exc:
            return {"error": f"snapshot failed: {exc}"[:256]}
        copies = {}
        for w in workers:
            try:
                copies[w] = isolated_copy(snap, w)
            except (OSError, ValueError) as exc:
                return {"error": f"isolation failed for {w}: {exc}"[:256]}
        return {"snapshot": {"id": snap.id, "root": snap.root,
                             "files": dict(sorted(snap.files.items())),
                             "base_revision": snap.base_revision},
                "copies": copies}

    def merge_changesets(inputs: dict[str, Any]) -> dict[str, Any]:
        snap_d = (inputs or {}).get("snapshot", {})
        root = ""
        try:
            root = os.path.realpath(str(snap_d.get("root", "")))
        except (ValueError, TypeError, AttributeError):
            pass
        if root != os.path.realpath(ws):
            return {"label": "invalid",
                    "reason": "snapshot root != executor workspace"}
        out = merge_workspace(dict(inputs or {}))
        _cleanup_copies(inputs)
        return out

    def report_conflict(inputs: dict[str, Any]) -> dict[str, Any]:
        # Honest terminal failure: conflicts unresolved after bounded repair.
        # Raising yields NodeResult FAILURE so the run never looks complete.
        details = str((inputs or {}).get("conflicts", ""))[:400]
        raise ValueError(f"unresolved workspace conflicts: {details}")

    def _cleanup_copies(inputs: dict[str, Any]) -> None:
        # Only tempdirs this module created: prefix + tempdir guard.
        tmp = os.path.realpath(tempfile.gettempdir())
        branches = inputs.get("branches", [])
        if not isinstance(branches, list):
            return
        for b in branches:
            if not isinstance(b, dict):
                continue
            d = os.path.realpath(str(b.get("copy_dir", "")))
            base = os.path.basename(d)
            if d.startswith(tmp + os.sep) and base.startswith("wisp-worker-"):
                shutil.rmtree(d, ignore_errors=True)

    return {"prepare_isolation": prepare_isolation,
            "merge_changesets": merge_changesets,
            "report_conflict": report_conflict}

def merge_workspace(inputs: dict[str, Any]) -> dict[str, Any]:
    """Host-owned merge function for ROUTER merge nodes.

    Inputs (all untrusted except snapshot identity, which is re-verified):
      snapshot: {id, root} — base; canonical is re-snapshotted and compared
      branches: [{worker, changes?|copy_dir?}] — worker outputs
      mode: "apply" (default) | "check" (classify only, no mutation)
      run_id/node_id: informational correlation only (producer identity is
        host-assigned per branch worker label, never model fields)

    Returns {"label": merged|conflict|stale|invalid, ...details} for
    deterministic router dispatch. Mutations execute under operator graph-run
    authority with containment + checkpoints + journal; per-call
    ToolExecutor authorization of merge-apply is an explicit deferred item
    (see report). Never raises: failures are INVALID/STALE outcomes.
    """
    try:
        return _merge_workspace_inner(dict(inputs or {}))
    except Exception as exc:
        logger.exception("merge boundary failed")
        return {"label": "invalid", "reason": f"merge error: {exc}"[:500]}


def _merge_workspace_inner(inputs: dict[str, Any]) -> dict[str, Any]:
    snap_d = inputs.get("snapshot")
    if not isinstance(snap_d, dict):
        return {"label": "invalid", "reason": "missing snapshot"}
    try:
        root = os.path.abspath(str(snap_d.get("root", "")))
        base = WorkspaceSnapshot(id=str(snap_d.get("id", ""))[:64], root=root,
                                 files={str(k): str(v)
                                        for k, v in (snap_d.get("files", {}) or {}).items()
                                        if isinstance(k, str) and isinstance(v, str)},
                                 base_revision=str(snap_d.get("base_revision", ""))[:128])
    except (ValueError, TypeError, AttributeError) as exc:
        return {"label": "invalid", "reason": f"bad snapshot: {exc}"[:256]}
    if not base.id or not base.files:
        return {"label": "invalid", "reason": "empty snapshot"}
    mode = inputs.get("mode", "apply")
    if mode not in ("apply", "check"):
        return {"label": "invalid", "reason": f"bad mode {mode!r}"}
    branches = inputs.get("branches")
    copies = inputs.get("copies")
    if branches is None and isinstance(copies, dict):
        # Setup-provided copy map {worker: dir} -> branch list.
        branches = [{"worker": str(w)[:64], "copy_dir": str(d)[:1024]}
                    for w, d in copies.items()]
    if not isinstance(branches, list) or not branches or len(branches) > 32:
        return {"label": "invalid", "reason": "branches must be a 1..32 list"}
    # Stale/external check FIRST: re-snapshot canonical over tracked files.
    current = snapshot_workspace(root, files=list(base.files))
    if current.files != base.files:
        drifted = sorted(set(base.files) ^ set(current.files))[:16]
        return {"label": "stale",
                "reason": f"canonical drifted from {base.id}: {drifted}"}
    sets: list[ChangeSet] = []
    for i, b in enumerate(branches):
        if not isinstance(b, dict):
            return {"label": "invalid", "reason": f"branch {i} malformed"}
        worker = str(b.get("worker", f"worker-{i}"))[:64]
        try:
            changes = _branch_changes(root, base, b)
        except (ValueError, TypeError, OSError) as exc:
            return {"label": "invalid", "reason": f"branch {worker}: {exc}"[:256]}
        try:
            sets.append(make_changeset(base.id, {"worker": worker}, changes))
        except ValueError as exc:
            return {"label": "invalid", "reason": f"branch {worker}: {exc}"[:256]}
    merged = merge_changesets(base, sets, {"worker": "merge-node"})
    if merged.outcome == "STALE":
        return {"label": "stale", "reason": merged.detail[:500]}
    if merged.outcome == "INVALID":
        return {"label": "invalid", "reason": merged.detail[:500]}
    if merged.outcome == "CONFLICT":
        return {"label": "conflict",
                "conflicts": [{"kind": c.kind, "path": c.path, "detail": c.detail,
                               "parties": list(c.parties)} for c in merged.conflicts],
                "non_conflicting": [{"op": c.op, "path": c.path, "content": c.content,
                                     "base_hash": c.base_hash}
                                    for c in _non_conflicting(base, sets, merged.conflicts)],
                "snapshot": {"id": base.id, "root": base.root}}
    assert merged.merged is not None
    if mode == "check":
        return {"label": "merged", "applied": [],
                "reason": "check mode: no mutation",
                "changeset": merged.merged.id}
    applied = apply_changeset(root, base, merged.merged)
    if not applied.ok:
        return {"label": "stale" if "EXTERNAL" in applied.reason or "STALE" in applied.reason
                else "invalid",
                "reason": f"apply refused: {applied.reason}"}
    try:
        from wisp.graph.security import scrub
        evidence: Any = scrub({"changeset": merged.merged.id,
                               "applied": list(applied.applied)})
    except Exception:
        evidence = {"changeset": merged.merged.id}
    return {"label": "merged", "applied": list(applied.applied),
            "changeset": merged.merged.id, "evidence": evidence}


def _branch_changes(root: str, base: WorkspaceSnapshot, branch: dict) -> list[Change]:
    """Materialize one branch as Changes. copy_dir is read-only diffed;
    explicit change dicts are strictly validated (model-supplied)."""
    if branch.get("copy_dir"):
        copy_dir = str(branch["copy_dir"])
        if not os.path.isdir(copy_dir):
            raise ValueError(f"copy_dir missing: {copy_dir[:128]}")
        # Read-only: never write through this path; diff only tracked files.
        return diff_copy_against_snapshot(copy_dir, base,
                                          str(branch.get("worker", "?"))[:64])
    raw = branch.get("changes")
    if not isinstance(raw, list) or len(raw) > MAX_CS_FILES:
        raise ValueError("branch needs changes[] (≤100) or copy_dir")
    out: list[Change] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("malformed change")
        op = str(item.get("op", ""))
        if op not in OPS:
            raise ValueError(f"bad op {op!r}")
        content = item.get("content", "")
        if not isinstance(content, str):
            raise ValueError("content must be text")
        artifact = str(item.get("artifact", ""))
        if artifact and not artifact.startswith("artifact://"):
            raise ValueError("bad artifact ref")
        if op == RENAME:
            out.append(Change(path=str(item.get("path", ""))[:512], op=op,
                              rename_from=str(item.get("rename_from", ""))[:512]))
        elif op == DELETE:
            out.append(Change(path=str(item.get("path", ""))[:512], op=op,
                              base_hash=str(item.get("base_hash", ""))[:64]))
        else:
            out.append(Change(path=str(item.get("path", ""))[:512], op=op,
                              base_hash=str(item.get("base_hash", ""))[:64],
                              content=content, artifact=artifact))
    return out


def _non_conflicting(base: WorkspaceSnapshot, sets: list[ChangeSet],
                     conflicts: tuple) -> list[Change]:
    bad_paths = {c.path for c in conflicts}
    seen: dict[tuple[str, str], Change] = {}
    for cs in sorted(sets, key=lambda c: c.id):
        for c in cs.changes:
            if c.path not in bad_paths:
                seen.setdefault((c.op, c.path), c)
    return [seen[k] for k in sorted(seen)]
