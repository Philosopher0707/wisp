"""Cross-session memory — persistent facts that survive across conversations.

Stores learned preferences, decisions, and project-specific knowledge in
~/.config/wisp/memory.json. Facts are injected into the system prompt
so the LLM remembers context across sessions.

Each fact tracks: content, when added, last accessed, access count,
and importance (important facts resist eviction).

Structure:
  {
    "version": 2,
    "global_facts": [
      {
        "content": "user prefers tabs over spaces",
        "added": "2026-05-07T10:00:00Z",
        "last_accessed": "2026-05-07T12:00:00Z",
        "access_count": 5,
        "important": false
      }
    ],
    "workspace_facts": {
      "/path/to/project": [...]
    }
  }
"""

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

try:
    import fcntl
except ImportError:  # Windows: in-process locking only
    fcntl = None  # type: ignore[assignment]

from wisp.config import WISP_CONFIG_DIR

logger = logging.getLogger(__name__)

_MAX_FACTS = 100  # Max total facts before LRU eviction kicks in
MAX_FACT_CHARS = 500  # Longest fact the `remember` tool accepts; stated in its schema
_STORE_VERSION = 2
_LOCK_TIMEOUT_SECONDS = 10.0

# Serializes this process's threads; the file lock serializes processes. One lock file
# descriptor per process at a time, because flock() conflicts between two descriptors
# of the same process.
_THREAD_LOCK = threading.Lock()
_warned_fallback: set[Path] = set()

_T = TypeVar("_T")


class MemoryStoreError(RuntimeError):
    """The store could not be read or locked safely, so nothing was written."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(content: str) -> str:
    """Normalize fact text for dedup: strip, collapse whitespace."""
    return " ".join(content.strip().split())


def _resolve_workspace(workspace: str) -> str:
    """Canonical path with symlink resolution for stable workspace keys.

    Uses os.path.realpath so that /tmp/project and /private/tmp/project
    (macOS) resolve to the same key, preventing memory fragmentation.
    """
    return os.path.realpath(workspace)


def _empty() -> dict:
    return {"version": _STORE_VERSION, "global_facts": [], "workspace_facts": {}}


# ── File I/O ────────────────────────────────────────────────────────────
#
# Every change is a read-modify-write of the file as it is NOW, under a lock, written to a
# unique temp file that is fsynced and renamed into place. The store used to keep a
# write-behind copy: a prompt build read the store, and two seconds later wrote that copy
# back over whatever another call or process had saved in between (six concurrent writers
# lost 88 of 90 facts). A store that cannot be read is never written over.


def _get_memory_file() -> Path:
    try:
        WISP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Verify the directory is actually writable — it may exist but be read-only in
        # sandboxed/CI environments. The probe name is unique: a fixed name raced between
        # callers (one unlinked the other's probe) and sent the loser to the temp store.
        fd, probe = tempfile.mkstemp(dir=WISP_CONFIG_DIR, prefix=".write_probe-")
        os.close(fd)
        os.unlink(probe)
        return WISP_CONFIG_DIR / "memory.json"
    except OSError:
        # Fallback to temp directory when ~/.config/wisp is not writable
        # (CI, sandboxes, ephemeral containers).
        fallback = Path(tempfile.gettempdir()) / "wisp_memory" / "memory.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        if fallback not in _warned_fallback:
            _warned_fallback.add(fallback)
            logger.warning("memory: %s is not writable; facts go to %s, which a reboot may clear",
                           WISP_CONFIG_DIR, fallback)
        return fallback


def _unreadable(path: Path, reason: str) -> MemoryStoreError:
    return MemoryStoreError(
        f"memory store {path} is unreadable ({reason}); it was left as it is and nothing was "
        f"written. Fix it or move it aside, then try again."
    )


def _validated(data: object, path: Path) -> dict:
    if not isinstance(data, dict):
        raise _unreadable(path, f"top level is {type(data).__name__}, not an object")
    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise _unreadable(path, f"version {version!r} is not a number")
    if version > _STORE_VERSION:
        raise _unreadable(path, f"version {version} is newer than this Wisp understands "
                                f"({_STORE_VERSION})")
    global_facts = data.get("global_facts", [])
    workspace_facts = data.get("workspace_facts", {})
    if not isinstance(global_facts, list):
        raise _unreadable(path, "global_facts is not a list")
    if not isinstance(workspace_facts, dict) or not all(
            isinstance(k, str) and isinstance(v, list) for k, v in workspace_facts.items()):
        raise _unreadable(path, "workspace_facts is not a map of lists")
    for facts in [global_facts, *workspace_facts.values()]:
        for fact in facts:
            # Version 1 stored bare strings; anything else would be dropped on the next save.
            if isinstance(fact, str):
                continue
            if not isinstance(fact, dict) or not isinstance(fact.get("content"), str):
                raise _unreadable(path, "a fact is not an object with text content")
    if version < _STORE_VERSION:
        data["global_facts"] = _migrate_facts(global_facts)
        data["workspace_facts"] = {ws: _migrate_facts(f) for ws, f in workspace_facts.items()}
    data["version"] = _STORE_VERSION
    data.setdefault("global_facts", [])
    data.setdefault("workspace_facts", {})
    return data


def _read(path: Path) -> dict:
    """The store as it is on disk; MemoryStoreError when the file exists but cannot be trusted."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _empty()
    except OSError as e:
        raise _unreadable(path, str(e))
    if not raw.strip():
        # Zero bytes hold no facts (a `touch`, or a crash between create and write), so
        # replacing the file loses nothing.
        return _empty()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise _unreadable(path, f"not valid JSON: {e}")
    return _validated(data, path)


def _write_atomic(path: Path, text: str) -> None:
    """Replace *path* with *text*: unique temp file, fsync, rename, fsync the directory.

    mkstemp creates the file with mode 0600: facts are the owner's private notes.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _dumps(memory: dict) -> str:
    return json.dumps(memory, indent=2, ensure_ascii=False) + "\n"


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold this store's lock, waiting at most _LOCK_TIMEOUT_SECONDS."""
    if not _THREAD_LOCK.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
        raise MemoryStoreError(f"memory store {path} is busy (waited {_LOCK_TIMEOUT_SECONDS:g}s); "
                               f"nothing was written")
    try:
        fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise MemoryStoreError(
                                f"memory store {path} is locked by another process (waited "
                                f"{_LOCK_TIMEOUT_SECONDS:g}s); nothing was written")
                        time.sleep(0.01)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the flock
    finally:
        _THREAD_LOCK.release()


def _mutate(change: Callable[[dict], "tuple[_T, bool]"]) -> _T:
    """Apply *change* to the store as it is now; write only if it reports a change.

    Raises MemoryStoreError when the store is unreadable or busy, OSError when the write
    fails. Either way the file on disk is unchanged.
    """
    path = _get_memory_file()
    with _locked(path):
        memory = _read(path)
        result, changed = change(memory)
        if changed:
            _write_atomic(path, _dumps(memory))
        return result


def load_memory(strict: bool = False) -> dict:
    """The store for reading. An unreadable store reads as empty (and is logged), or raises
    MemoryStoreError when *strict*: a listing for the owner must not show "no facts" for a
    store it could not read."""
    path = _get_memory_file()
    try:
        return _read(path)
    except MemoryStoreError as e:
        if strict:
            raise
        logger.warning("%s", e)
        return _empty()


def _persist_touches(touched: "Counter[tuple[Optional[str], str]]", when: str) -> None:
    """Record reads in the access stats of the store as it is now (best effort).

    Keys are (workspace key or None for global, lower-cased normalized content). A read
    must not fail because its bookkeeping could not be saved, and must never write over a
    store it could not read: _mutate refuses that.
    """
    if not touched:
        return

    def apply(memory: dict) -> "tuple[None, bool]":
        changed = False
        scopes: list[tuple[Optional[str], list]] = [(None, memory["global_facts"])]
        scopes += list(memory["workspace_facts"].items())
        for scope, facts in scopes:
            for fact in facts:
                n = touched.get((scope, _normalize(_fact_content(fact)).lower()), 0)
                if n:
                    fact["last_accessed"] = when
                    fact["access_count"] = fact.get("access_count", 0) + n
                    changed = True
        return None, changed

    try:
        _mutate(apply)
    except (MemoryStoreError, OSError) as e:
        logger.debug("memory: access stats not saved: %s", e)


def _migrate_facts(old: list) -> list[dict]:
    """Migrate list of strings → list of fact dicts."""
    now = _now_iso()
    result = []
    for item in old:
        if isinstance(item, dict):
            item.setdefault("added", now)
            item.setdefault("last_accessed", now)
            item.setdefault("access_count", 0)
            item.setdefault("important", False)
            result.append(item)
        elif isinstance(item, str):
            result.append({
                "content": item,
                "added": now,
                "last_accessed": now,
                "access_count": 0,
                "important": False,
            })
    return result


# ── Fact dict helpers ───────────────────────────────────────────────────


def _make_fact(content: str, important: bool = False) -> dict:
    now = _now_iso()
    return {
        "content": _normalize(content),
        "added": now,
        "last_accessed": now,
        "access_count": 0,
        "important": important,
    }


def _fact_content(fact: dict) -> str:
    return fact.get("content", "")


def _match(fact: dict, normalized: str) -> bool:
    """Case-insensitive match against a normalized query."""
    return _normalize(_fact_content(fact)).lower() == normalized.lower()


def _touch(fact: dict):
    fact["last_accessed"] = _now_iso()
    fact["access_count"] = fact.get("access_count", 0) + 1


def _sort_key(fact: dict):
    """Sort: important first, then most recently accessed."""
    return (fact.get("important", False), fact.get("last_accessed", ""))


# ── Fact CRUD ───────────────────────────────────────────────────────────


def add_fact(content: str, workspace: Optional[str] = None,
             important: bool = False) -> bool:
    """Add a fact. Dedup is case-insensitive on normalized text.

    Returns True if added, False if it already existed (it is refreshed) or every fact is
    important at capacity. At capacity, evicts the least-recently-used non-important fact.
    Raises MemoryStoreError or OSError when the fact could not be saved.
    """
    norm = _normalize(content)
    if not norm:
        return False

    def change(memory: dict) -> "tuple[bool, bool]":
        facts = _get_fact_list(memory, workspace)
        for f in facts:
            if _match(f, norm):
                _touch(f)
                return False, True  # Not a new fact, but refreshed

        # At capacity — evict LRU non-important fact
        total = _count_facts(memory)
        while total >= _MAX_FACTS:
            if not _evict_one(memory):
                logger.warning("Memory at capacity (%d), all facts important", total)
                return False, False
            total = _count_facts(memory)
        # Eviction can drop this workspace's last fact and its key with it.
        facts = _get_fact_list(memory, workspace)
        facts.append(_make_fact(content, important=important))
        return True, True

    added = _mutate(change)
    if added:
        logger.info("Added fact: %s", content[:80])
    return added


def remove_fact(content: str, workspace: Optional[str] = None) -> bool:
    """Remove a fact by content (case-insensitive match)."""
    norm = _normalize(content)

    def change(memory: dict) -> "tuple[bool, bool]":
        if workspace:
            ws_path = _resolve_workspace(workspace)
            ws_facts = memory["workspace_facts"]
            if ws_path not in ws_facts:
                return False, False
            facts = ws_facts[ws_path]
        else:
            facts = memory["global_facts"]
        for i, f in enumerate(facts):
            if _match(f, norm):
                facts.pop(i)
                if workspace and not facts:
                    del memory["workspace_facts"][ws_path]
                return True, True
        return False, False

    return _mutate(change)


def _touch_all(facts: "list[tuple[Optional[str], dict]]") -> list[dict]:
    """Touch each fact (in the returned copies and on disk) and return them."""
    when = _now_iso()
    touched: Counter[tuple[Optional[str], str]] = Counter()
    for scope, fact in facts:
        fact["last_accessed"] = when
        fact["access_count"] = fact.get("access_count", 0) + 1
        touched[(scope, _normalize(_fact_content(fact)).lower())] += 1
    _persist_touches(touched, when)
    return [fact for _, fact in facts]


def list_facts(workspace: Optional[str] = None) -> list[dict]:
    """Return global + workspace facts, sorted by importance → recency.

    Updates last_accessed and access_count on read (touch on access).
    """
    memory = load_memory()
    scoped: list[tuple[Optional[str], dict]] = [(None, f) for f in memory.get("global_facts", [])]
    if workspace:
        ws_path = _resolve_workspace(workspace)
        scoped += [(ws_path, f) for f in memory.get("workspace_facts", {}).get(ws_path, [])]
    results = _touch_all(scoped)
    results.sort(key=_sort_key, reverse=True)
    return results


def list_all_facts() -> list[dict]:
    """Return ALL facts across global + every workspace, sorted by importance → recency.

    This makes memory work globally regardless of which directory the agent
    is currently running in. Updates last_accessed and access_count on read.
    """
    memory = load_memory()
    scoped: list[tuple[Optional[str], dict]] = [(None, f) for f in memory.get("global_facts", [])]
    for ws_path, facts in memory.get("workspace_facts", {}).items():
        scoped += [(ws_path, f) for f in facts]
    results = _touch_all(scoped)
    results.sort(key=_sort_key, reverse=True)
    return results


def facts_grouped_by_scope(workspace: Optional[str] = None) -> dict[str, list[dict]]:
    """Facts bucketed by where they were recorded: ``here`` / ``global`` / ``elsewhere``.

    `list_all_facts()` returns everything on purpose — *"memory works globally regardless of which
    directory the agent is currently running in"*. That is right for **recall** and wrong for
    **injection**, because the prompt gave the facts no provenance: a fact asserting an environment
    property of one project was read by the model as a statement about the project it was actually
    in, and it acted on it.

    Observed: a fact reading *"run_bash runs in a container rooted at /workspace, NOT the macOS path;
    `cd` to /Users/philosopher/... fails"* — true for the project it was recorded in — made the agent
    run **every** tool against ``/workspace`` in a repository whose workspace is elsewhere. Each call
    failed with *"outside workspace"* and the turn could not proceed.

    Bucketing lets the caller keep the global recall and still tell the model which facts are about
    *this* workspace. Duplicates collapse: one fact recorded in twenty workspaces is one fact.
    """
    memory = load_memory()
    here_key = _resolve_workspace(workspace) if workspace else None
    out: dict[str, list[dict]] = {"here": [], "global": [], "elsewhere": []}
    seen: set[str] = set()
    shown: list[tuple[Optional[str], dict]] = []

    def _add(bucket: str, scope: Optional[str], fact: dict) -> None:
        key = _normalize(_fact_content(fact)).lower()
        if not key or key in seen:
            return
        seen.add(key)
        shown.append((scope, fact))
        out[bucket].append(fact)

    for fact in memory.get("global_facts", []):
        _add("global", None, fact)
    for ws_path, facts in memory.get("workspace_facts", {}).items():
        if here_key and os.path.realpath(ws_path) == here_key:
            for fact in facts:
                _add("here", ws_path, fact)
        else:
            for fact in facts:
                _add("elsewhere", ws_path, fact)
    _touch_all(shown)
    # Carry the origin WITH the fact. A heading saying "these are from elsewhere" is a
    # caveat the model can read past; `[from /path/to/other-project]` on the line itself is
    # not, because it names the project the fact is about. A copy, so the key never reaches
    # the store.
    origins = {id(fact): scope for scope, fact in shown}
    out["elsewhere"] = [{**fact, "_origin": origins[id(fact)]} for fact in out["elsewhere"]]

    for bucket in out.values():
        bucket.sort(key=_sort_key, reverse=True)
    return out


def set_importance(content: str, important: bool,
                   workspace: Optional[str] = None) -> bool:
    """Mark or unmark a fact as important. Returns True if found."""
    norm = _normalize(content)

    def change(memory: dict) -> "tuple[bool, bool]":
        for f in _get_fact_list(memory, workspace):
            if _match(f, norm):
                f["important"] = important
                _touch(f)
                return True, True
        return False, False

    return _mutate(change)


def clear_memory(workspace: Optional[str] = None):
    """Clear all facts, optionally for a specific workspace.

    What is cleared is first saved next to the store as ``memory.json.cleared-<UTC time>``,
    so a clear can be undone by hand.
    """
    path = _get_memory_file()

    def change(memory: dict) -> "tuple[None, bool]":
        ws_path = _resolve_workspace(workspace) if workspace else None
        if ws_path is not None and not memory["workspace_facts"].get(ws_path):
            return None, False
        if _count_facts(memory) == 0:
            return None, False
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        _write_atomic(path.with_name(f"{path.name}.cleared-{stamp}"), _dumps(memory))
        if ws_path is not None:
            del memory["workspace_facts"][ws_path]
        else:
            memory["global_facts"] = []
            memory["workspace_facts"] = {}
        return None, True

    _mutate(change)


# ── System prompt formatting ────────────────────────────────────────────


def format_memory_block(workspace: Optional[str] = None, include_all: bool = True) -> str:
    """Format memory facts as a system prompt block.

    By default (include_all=True) includes ALL facts across every workspace
    so memory works globally no matter which directory the agent is in.
    Set include_all=False to scope to just the current workspace + global.
    """
    if include_all:
        facts = list_all_facts()
    else:
        facts = list_facts(workspace)

    if not facts:
        return ""

    lines = ["## Learned Preferences (Global Memory)"]
    shown = 0
    for f in facts:
        content = _fact_content(f)
        marker = "⭐ " if f.get("important") else ""
        added = f.get("added", "")
        # Render YYYY-MM-DD prefix so model knows recency
        date_prefix = ""
        if added and len(added) >= 10:
            date_prefix = f"[{added[:10]}] "
        lines.append(f"- {marker}{date_prefix}{content}")
        shown += 1
        if shown >= 20:
            break

    lines.append("")
    lines.append("(Use `remember` to add facts, `forget` to remove)")
    return "\n".join(lines)


# ── Internal helpers ────────────────────────────────────────────────────


def _get_fact_list(memory: dict, workspace: Optional[str] = None) -> list[dict]:
    if workspace:
        ws_path = _resolve_workspace(workspace)
        return memory.setdefault("workspace_facts", {}).setdefault(ws_path, [])
    return memory.setdefault("global_facts", [])


def _count_facts(memory: dict) -> int:
    count = len(memory.get("global_facts", []))
    for facts in memory.get("workspace_facts", {}).values():
        count += len(facts)
    return count


def _evict_one(memory: dict) -> bool:
    """Evict the least-recently-used fact, with important facts getting a bonus.

    Important facts receive a 30-day recency bonus: they are treated as if
    they were accessed 30 days more recently than their actual last_accessed.
    This means important facts survive longer but are NOT immortal.
    """
    all_facts: list[tuple[dict, str | None]] = []
    for f in memory.get("global_facts", []):
        all_facts.append((f, None))
    for ws_path, facts in memory.get("workspace_facts", {}).items():
        for f in facts:
            all_facts.append((f, ws_path))

    if not all_facts:
        return False

    now = datetime.now(timezone.utc)
    bonus = timedelta(days=30)

    def _effective_age(item: tuple[dict, str | None]) -> timedelta:
        fact, _ = item
        last_str = fact.get("last_accessed", "")
        if not last_str:
            return timedelta.max
        try:
            last = datetime.fromisoformat(last_str)
        except ValueError:
            return timedelta.max
        age = now - last
        # Important facts appear younger by 30 days
        if fact.get("important"):
            age -= bonus
        return age

    # Evict the fact with the largest effective age (oldest)
    oldest = max(all_facts, key=_effective_age)
    fact, ws = oldest

    if ws:
        ws_list = memory["workspace_facts"][ws]
        ws_list.remove(fact)
        if not ws_list:
            del memory["workspace_facts"][ws]
    else:
        memory["global_facts"].remove(fact)

    logger.info("Evicted LRU fact: %s", _fact_content(fact)[:80])
    return True
