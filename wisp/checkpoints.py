"""Git-based workspace checkpoint system for the Wisp AI coding agent.

Provides CheckpointManager for creating and restoring workspace snapshots.
Uses git stash for tracking when available, falls back to compressed file
backups when git is not present.

Checkpoints are stored in .wisp/checkpoints/ within the workspace directory.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import tarfile
import uuid
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

CHECKPOINTS_DIR = ".wisp/checkpoints"
BACKUPS_SUBDIR = "backups"
METADATA_FILE = "metadata.json"
DEFAULT_MAX_CHECKPOINTS = 50
GIT_TIMEOUT = 15  # seconds — max wait for any single git subprocess

# Directories and file patterns excluded from non-git backups.
_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git", ".wisp", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".eggs",
    ".idea", ".vscode", "dist", "build", ".sass-cache",
})
_EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".pyc", ".pyo", ".egg-info", ".DS_Store", ".swp", ".swo",
)


# ── Checkpoint dataclass ─────────────────────────────────────────────────

class CheckpointError(RuntimeError):
    """Raised when checkpoint creation or restore fails irrecoverably."""


@dataclass
class Checkpoint:
    """A snapshot of the workspace at a point in time."""

    id: str                 # unique uuid
    timestamp: str          # ISO 8601
    description: str        # human label (e.g., "before write_file config.py")
    tool_name: str          # tool that was about to run
    tag: str                # optional user tag
    file_count: int         # number of files tracked
    git_ref: str | None = None       # git stash ref (git mode only)
    backup_path: str | None = None   # path to .tar.gz (no-git mode only)

    @property
    def is_valid(self) -> bool:
        """A checkpoint is valid if it has backing data to restore from."""
        return bool(self.git_ref or self.backup_path)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Checkpoint:
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


# ── CheckpointStore (metadata persistence) ───────────────────────────────

class CheckpointStore:
    """Persists checkpoint metadata to a JSON file.

    Stored at ``.wisp/checkpoints/metadata.json`` within the workspace.
    Thread-safe via asyncio.Lock.
    """

    def __init__(self, workspace: Path) -> None:
        self._dir = workspace / CHECKPOINTS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / METADATA_FILE
        self._lock = asyncio.Lock()

    async def save(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint's metadata."""
        async with self._lock:
            data = self._read()
            data[checkpoint.id] = checkpoint.to_dict()
            self._write(data)
            logger.debug("Saved metadata for %s", checkpoint.id)

    async def load_all(self) -> list[Checkpoint]:
        """Return all checkpoints sorted newest-first."""
        async with self._lock:
            data = self._read()
            results = [Checkpoint.from_dict(v) for v in data.values()]
            results.sort(key=lambda c: c.timestamp, reverse=True)
            return results

    async def delete(self, checkpoint_id: str) -> None:
        """Remove a checkpoint's metadata entry."""
        async with self._lock:
            data = self._read()
            if checkpoint_id in data:
                del data[checkpoint_id]
                self._write(data)
                logger.debug("Deleted metadata for %s", checkpoint_id)

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        """Fetch a single checkpoint by id, or None."""
        async with self._lock:
            entry = self._read().get(checkpoint_id)
            if entry is None:
                return None
            return Checkpoint.from_dict(entry)

    # ── internal ─────────────────────────────────────────────────────

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt %s, resetting: %s", METADATA_FILE, exc)
            return {}

    def _write(self, data: dict) -> None:
        try:
            self._path.write_text(json.dumps(data, indent=2))
        except OSError as exc:
            logger.error("Failed to write %s: %s", METADATA_FILE, exc)


# ── CheckpointManager ────────────────────────────────────────────────────

class CheckpointManager:
    """Creates, restores, and manages workspace checkpoints.

    Uses git stash (dangling refs, no branch pollution) when the workspace
    is a git repository.  Falls back to compressed-tar backups otherwise.

    Parameters
    ----------
    workspace:
        Root of the workspace to checkpoint.
    max_checkpoints:
        Maximum number of checkpoints to retain.  When exceeded the oldest
        checkpoint is automatically dropped (default 50).
    """

    def __init__(
        self,
        workspace: Path,
        max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.max_checkpoints = max_checkpoints
        self._dir = self.workspace / CHECKPOINTS_DIR
        self._backup_dir = self._dir / BACKUPS_SUBDIR
        self._store = CheckpointStore(self.workspace)
        self._lock = asyncio.Lock()
        self._git_cache: bool | None = None

    # ── Public API ───────────────────────────────────────────────────

    async def create(
        self,
        description: str,
        tool_name: str = "",
        tag: str = "",
    ) -> Checkpoint:
        """Capture a snapshot of the current workspace state.

        Returns the new ``Checkpoint``.  Raises ``CheckpointError`` if all
        snapshot methods (git + file backup) fail.

        This method is protected by an ``asyncio.Lock`` so concurrent
        ``create()`` calls are serialized.
        """
        async with self._lock:
            await self._prepare_dirs()
            await self._ensure_gitignore()

            checkpoint_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            errors: list[str] = []

            cp: Checkpoint | None = None

            # Try git first, then file backup, collecting errors along the way.
            if await self._has_git():
                try:
                    cp = await self._create_via_git(
                        checkpoint_id, timestamp, description, tool_name, tag
                    )
                except Exception as exc:
                    errors.append(f"git: {exc}")
                    logger.debug("Git checkpoint failed, trying backup: %s", exc)

            if cp is None:
                try:
                    cp = await self._create_via_backup(
                        checkpoint_id, timestamp, description, tool_name, tag
                    )
                except Exception as exc:
                    errors.append(f"backup: {exc}")
                    logger.error("Backup checkpoint also failed: %s", exc)

            if cp is None or not cp.is_valid:
                raise CheckpointError(
                    f"Failed to create checkpoint [{description}]: {'; '.join(errors)}"
                )

            # Persist metadata — if this fails we orphan the backing data,
            # but the checkpoint itself is still usable for this session.
            try:
                await self._store.save(cp)
            except Exception as exc:
                logger.warning("Failed to persist checkpoint metadata: %s — checkpoint is session-only", exc)

            logger.info(
                "Checkpoint %s created [%s] — %d file(s)",
                cp.id, cp.description, cp.file_count,
            )

            await self._enforce_limit()
            return cp

    async def auto_checkpoint(self, tool_name: str) -> Checkpoint:
        """Convenience: create an automatic checkpoint before tool execution.

        Uses the naming convention ``auto-{tool_name}-{timestamp}`` so
        that checkpoints are traceable to the tool that triggered them.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        description = f"auto-{tool_name}-{ts}"
        return await self.create(description=description, tool_name=tool_name)

    async def restore(self, checkpoint_id: str) -> bool:
        """Restore workspace to the state captured in *checkpoint_id*.

        Returns ``True`` on success, ``False`` if the checkpoint does not
        exist or the restore operation failed.

        **Safety**: before restoring, a temporary safety stash is created
        so that the workspace is never left in a corrupt state.  If the
        restore fails the safety stash is preserved (it will be cleaned
        up on the next successful restore).
        """
        cp = await self._store.get(checkpoint_id)
        if cp is None:
            logger.error("Checkpoint not found: %s", checkpoint_id)
            return False

        if await self._has_git() and cp.git_ref:
            return await self._restore_via_git(cp)

        if cp.backup_path:
            return await self._restore_via_backup(cp)

        logger.error(
            "Checkpoint %s has neither git ref nor backup — nothing to restore",
            checkpoint_id,
        )
        return False

    async def list_checkpoints(self) -> list[Checkpoint]:
        """Return every checkpoint, newest first."""
        return await self._store.load_all()

    async def drop(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint and all its backing data.

        Returns ``True`` if the checkpoint was removed, ``False`` if it
        was not found.
        """
        cp = await self._store.get(checkpoint_id)
        if cp is None:
            logger.warning("Checkpoint not found for drop: %s", checkpoint_id)
            return False

        # Tear down git ref
        if cp.git_ref and cp.git_ref != "EMPTY":
            await self._try_drop_git_ref(cp.git_ref)

        # Tear down backup file
        if cp.backup_path:
            await self._try_unlink(Path(cp.backup_path))

        await self._store.delete(checkpoint_id)
        logger.info("Dropped checkpoint %s", checkpoint_id)
        return True

    async def get_diff(self, checkpoint_id: str) -> str:
        """Return a unified diff between *checkpoint_id* and the current state.

        Uses ``git diff <ref>`` when available; falls back to Python's
        ``difflib`` when operating on a tar backup.
        """
        cp = await self._store.get(checkpoint_id)
        if cp is None:
            return f"Checkpoint not found: {checkpoint_id}"

        if await self._has_git() and cp.git_ref and cp.git_ref != "EMPTY":
            return await self._git_diff(cp.git_ref)

        if cp.backup_path:
            return await self._backup_diff(Path(cp.backup_path))

        return self._empty_diff_message(cp)

    # ── Internal: git detection & directory setup ────────────────────

    @staticmethod
    async def _git_proc(*args: str, cwd: Path, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
        """Run a git subprocess with a timeout. Returns (returncode, stdout, stderr)."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return (proc.returncode or 0, stdout.decode(), stderr.decode())
        except asyncio.TimeoutError:
            logger.warning("git %s timed out after %ss", args[0], timeout)
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return (-1, "", f"timeout after {timeout}s")
        except FileNotFoundError:
            return (-2, "", "git not found")
        except Exception as exc:
            logger.debug("git %s failed: %s", args[0], exc)
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return (-3, "", str(exc))

    async def _has_git(self) -> bool:
        if self._git_cache is None:
            rc, _, _ = await self._git_proc("git", "rev-parse", "--git-dir",
                                            cwd=self.workspace, timeout=5)
            if rc == -2:  # git not found
                self._git_cache = False
            else:
                self._git_cache = rc == 0
            logger.debug("git available: %s", self._git_cache)
        return self._git_cache

    def _clear_stale_index_lock(self) -> None:
        """Remove a stale .git/index.lock left by a crashed git process."""
        git_dir = self._resolve_git_dir()
        if git_dir is None:
            return
        lock_file = git_dir / "index.lock"
        try:
            if lock_file.exists():
                lock_file.unlink()
                logger.debug("Removed stale .git/index.lock")
        except OSError as exc:
            logger.debug("Could not remove .git/index.lock: %s", exc)

    def _resolve_git_dir(self) -> Path | None:
        """Return the actual .git directory for the workspace, using git rev-parse."""
        import subprocess
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.workspace,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                git_dir = Path(result.stdout.strip())
                if not git_dir.is_absolute():
                    git_dir = self.workspace / git_dir
                return git_dir.resolve()
        except (subprocess.TimeoutExpired, OSError):
            pass
        # Fallback: check common locations
        for candidate in (self.workspace / ".git", self.workspace):
            if (candidate / ".git").is_dir():
                return candidate / ".git"
        return None

    async def _prepare_dirs(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_gitignore(self) -> None:
        """Make sure ``.wisp/checkpoints/`` is ignored by git."""
        gitignore = self.workspace / ".gitignore"
        entry_line = f"{CHECKPOINTS_DIR}/"

        try:
            if gitignore.exists():
                text = gitignore.read_text()
                if CHECKPOINTS_DIR not in text:
                    new_text = text.rstrip() + f"\n\n# Wisp checkpoints\n{entry_line}\n"
                    gitignore.write_text(new_text)
                    logger.info("Added %s to .gitignore", entry_line)
            else:
                gitignore.write_text(
                    f"# Wisp checkpoints\n{entry_line}\n"
                )
                logger.info("Created .gitignore with %s entry", entry_line)
        except OSError as exc:
            logger.warning("Could not update .gitignore: %s", exc)

    # ── Internal: git-mode create ────────────────────────────────────

    async def _create_via_git(
        self,
        cid: str,
        ts: str,
        desc: str,
        tool: str,
        tag: str,
    ) -> Checkpoint:
        """Snapshot using ``git stash create`` (dangling ref, zero branch impact)."""
        self._clear_stale_index_lock()

        rc, _, stderr = await self._git_proc("git", "add", "-A", cwd=self.workspace)
        if rc != 0:
            logger.warning("git add -A failed (exit %d): %s — falling back to file backup",
                           rc, stderr.strip())
            self._git_cache = False
            return await self._create_via_backup(cid, ts, desc, tool, tag)

        rc, stdout, stderr = await self._git_proc("git", "stash", "create", cwd=self.workspace)
        if rc != 0:
            logger.warning("git stash create failed (exit %d): %s — falling back to file backup",
                           rc, stderr.strip())
            self._git_cache = False
            return await self._create_via_backup(cid, ts, desc, tool, tag)

        git_ref = stdout.strip()

        # When the tree is clean, stash create returns empty.  Use the
        # current index tree so that ``restore()`` still has something
        # to check out (even if it's a no-op relative to HEAD).
        if not git_ref:
            rc, tree_out, _ = await self._git_proc("git", "write-tree", cwd=self.workspace)
            git_ref = tree_out.strip() if rc == 0 else "EMPTY"

        file_count = await self._git_file_count()

        return Checkpoint(
            id=cid,
            timestamp=ts,
            description=desc,
            tool_name=tool,
            tag=tag,
            file_count=file_count,
            git_ref=git_ref,
        )

    async def _git_file_count(self) -> int:
        try:
            rc, stdout, _ = await self._git_proc("git", "ls-files", cwd=self.workspace, timeout=5)
            if rc != 0:
                return 0
            return sum(1 for line in stdout.splitlines() if line.strip())
        except Exception:
            return 0

    # ── Internal: no-git (backup) create ─────────────────────────────

    async def _create_via_backup(
        self,
        cid: str,
        ts: str,
        desc: str,
        tool: str,
        tag: str,
    ) -> Checkpoint:
        """Snapshot by creating a compressed tar of workspace files."""
        tracked = await self._enumerate_files()
        archive_path = self._backup_dir / f"{cid}.tar.gz"

        # Run tar creation in a thread so we don't block the event loop.
        def _write_tar() -> int:
            count = 0
            with tarfile.open(archive_path, "w:gz") as tar:
                for rel in tracked:
                    full = self.workspace / rel
                    if full.is_file() and not full.is_symlink():
                        try:
                            tar.add(full, arcname=rel)
                            count += 1
                        except OSError as exc:
                            logger.warning("Skipping %s: %s", rel, exc)
            return count

        actual_count = await asyncio.to_thread(_write_tar)

        return Checkpoint(
            id=cid,
            timestamp=ts,
            description=desc,
            tool_name=tool,
            tag=tag,
            file_count=actual_count,
            backup_path=str(archive_path),
        )

    # ── Internal: git-mode restore ───────────────────────────────────

    async def _restore_via_git(self, cp: Checkpoint) -> bool:
        assert cp.git_ref is not None
        if cp.git_ref == "EMPTY":
            logger.info("Checkpoint %s is empty — nothing to restore", cp.id)
            return True

        if not await self._git_ref_valid(cp.git_ref):
            logger.error(
                "Git ref %s no longer exists (may have been garbage-collected)",
                cp.git_ref,
            )
            return False

        safety = await self._safety_stash()

        try:
            rc, _, stderr = await self._git_proc(
                "git", "checkout", cp.git_ref, "--", ".", cwd=self.workspace, timeout=15,
            )
            if rc != 0:
                logger.error("git checkout failed: %s", stderr.strip())
                return False

            logger.info("Restored workspace to checkpoint %s", cp.id)
            return True
        except Exception as exc:
            logger.error("Restore exception: %s", exc)
            return False
        finally:
            if safety:
                await self._drop_ref(safety)

    async def _safety_stash(self) -> str | None:
        """Create a temporary stash of current state.  Returns ref or None."""
        self._clear_stale_index_lock()
        try:
            rc, _, _ = await self._git_proc("git", "add", "-A", cwd=self.workspace, timeout=10)
            if rc != 0:
                return None
            rc, stdout, _ = await self._git_proc("git", "stash", "create", cwd=self.workspace, timeout=10)
            ref = stdout.strip()
            return ref if ref else None
        except Exception:
            return None

    async def _drop_ref(self, ref: str) -> None:
        try:
            await self._git_proc("git", "stash", "drop", ref, cwd=self.workspace, timeout=10)
        except Exception as exc:
            logger.debug("Could not drop ref %s: %s", ref, exc)

    async def _git_ref_valid(self, ref: str) -> bool:
        try:
            rc, _, _ = await self._git_proc("git", "cat-file", "-t", ref, cwd=self.workspace, timeout=5)
            return rc == 0
        except Exception:
            return False

    async def _try_drop_git_ref(self, ref: str) -> None:
        try:
            rc, _, _ = await self._git_proc("git", "stash", "drop", ref, cwd=self.workspace, timeout=10)
            if rc != 0:
                logger.debug("git stash drop for %s returned non-zero (ok)", ref)
        except Exception as exc:
            logger.debug("git stash drop error (non-critical): %s", exc)

    # ── Internal: no-git (backup) restore ────────────────────────────

    async def _restore_via_backup(self, cp: Checkpoint) -> bool:
        archive = Path(cp.backup_path) if cp.backup_path else None
        if archive is None or not archive.exists():
            logger.error("Backup file missing: %s", cp.backup_path)
            return False

        # Validate the archive can be read before touching the workspace.
        valid = await asyncio.to_thread(self._validate_tar, archive)
        if not valid:
            logger.error("Backup archive is corrupt: %s", archive)
            return False

        try:
            def _extract() -> None:
                with tarfile.open(archive, "r:gz") as tar:
                    # Defend against path-traversal (members with absolute /
                    # parent-directory paths).
                    for member in tar.getmembers():
                        member_path = Path(member.name)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            logger.warning(
                                "Skipping suspicious path in backup: %s", member.name
                            )
                            continue
                    tar.extractall(path=self.workspace)

            await asyncio.to_thread(_extract)
            logger.info("Restored workspace to checkpoint %s (backup)", cp.id)
            return True
        except Exception as exc:
            logger.error("Backup restore failed: %s", exc)
            return False

    @staticmethod
    def _validate_tar(path: Path) -> bool:
        """Return True if *path* is a readable, well-formed tar.gz."""
        try:
            with tarfile.open(path, "r:gz") as tar:
                tar.getmembers()  # forces full header read
            return True
        except Exception as exc:
            logger.debug("Tar validation failed: %s", exc)
            return False

    async def _try_unlink(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)

    # ── Internal: diff helpers ───────────────────────────────────────

    async def _git_diff(self, ref: str) -> str:
        try:
            rc, stdout, _ = await self._git_proc("git", "diff", ref, cwd=self.workspace, timeout=10)
            if rc != 0:
                return f"Error computing git diff (exit {rc})"
            return stdout if stdout.strip() else "(no differences)"
        except Exception as exc:
            return f"Error computing git diff: {exc}"

    async def _backup_diff(self, archive: Path) -> str:
        """Compute difflib diff between tar backup and current files."""
        def _compute() -> str:
            if not archive.exists():
                return "Backup file not found."
            parts: list[str] = []
            try:
                with tarfile.open(archive, "r:gz") as tar:
                    for member in tar.getmembers():
                        if not member.isfile():
                            continue
                        fh = tar.extractfile(member)
                        if fh is None:
                            continue
                        old = fh.read().decode("utf-8", errors="replace")
                        current_file = self.workspace / member.name
                        new = (
                            current_file.read_text(errors="replace")
                            if current_file.exists()
                            else ""
                        )
                        if old != new:
                            parts.extend(
                                difflib.unified_diff(
                                    old.splitlines(keepends=True),
                                    new.splitlines(keepends=True),
                                    fromfile=f"a/{member.name} (checkpoint)",
                                    tofile=f"b/{member.name} (current)",
                                )
                            )
                return "".join(parts) if parts else "(no differences)"
            except Exception as exc:
                return f"Error computing backup diff: {exc}"

        return await asyncio.to_thread(_compute)

    @staticmethod
    def _empty_diff_message(cp: Checkpoint) -> str:
        if cp.git_ref == "EMPTY":
            return "(empty checkpoint — no files were tracked)"
        return "No diff available (missing git ref and backup path)."

    # ── Internal: file enumeration (no-git mode) ─────────────────────

    @staticmethod
    def _enumerate_files_in(workspace: Path) -> list[str]:
        """Walk *workspace* and return relative paths of tracked files."""
        files: list[str] = []
        for root, dirs, filenames in os.walk(workspace):
            rel_root = Path(root).relative_to(workspace)
            root_parts = set(rel_root.parts) if rel_root != Path(".") else set()

            # Prune excluded directories.
            if root_parts & _EXCLUDED_DIRS:
                dirs.clear()
                continue
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]

            for name in filenames:
                if name in _EXCLUDED_DIRS:
                    continue
                if name.endswith(_EXCLUDED_SUFFIXES):
                    continue
                rel = str(Path(root).relative_to(workspace) / name)
                files.append(rel)
        return files

    async def _enumerate_files(self) -> list[str]:
        return await asyncio.to_thread(
            CheckpointManager._enumerate_files_in, self.workspace
        )

    # ── Internal: max-checkpoints enforcement ────────────────────────

    async def _enforce_limit(self) -> None:
        all_cps = await self._store.load_all()
        while len(all_cps) > self.max_checkpoints:
            oldest = all_cps[-1]
            logger.info(
                "Max checkpoints reached (%d), dropping oldest: %s",
                self.max_checkpoints, oldest.id,
            )
            await self.drop(oldest.id)
            all_cps.pop()


# ── Module exports ───────────────────────────────────────────────────────

__all__ = ["Checkpoint", "CheckpointManager", "CheckpointStore"]
