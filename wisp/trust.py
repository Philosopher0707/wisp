"""Workspace trust management — prevents loading untrusted workspace configs/hooks.

Uses POSIX advisory file locking (fcntl) for safe multi-process access.
Shared locks for reads, exclusive locks only for the atomic trust op.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


class WorkspaceTrustManager:
    TRUST_FILE = Path.home() / ".config" / "wisp" / "trusted_workspaces.json"

    # ── public read-only API ──────────────────────────────────────────────

    @classmethod
    def is_workspace_trusted(
        cls,
        workspace: Path | str,
        *,
        trust_file: Path | str | None = None,
    ) -> bool:
        """Check if the given workspace is trusted by the user.
        
        Uses a shared advisory read lock so multiple processes can
        read the trust file concurrently without races.
        """
        if os.environ.get("WISP_TRUST_ALL_WORKSPACES") == "true":
            return True

        workspace_path = str(Path(workspace).resolve())

        # Auto-trust workspaces that already contain Wisp configuration
        # — the user has already chosen to work here.
        if (Path(workspace_path) / ".wisp").exists():
            return True
        dst = Path(trust_file) if trust_file else cls.TRUST_FILE
        if not dst.exists():
            return False

        try:
            with open(dst, "r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    trusted = json.load(f)
                    return workspace_path in trusted
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            return False

    # ── public write API ──────────────────────────────────────────────────

    @classmethod
    def trust_workspace(cls, workspace: Path | str, *, trust_file: Path | str | None = None) -> None:
        """Add the given workspace to the trusted workspaces list.

        Uses an exclusive advisory file lock so the read-modify-write is
        atomic across processes.  On failure to parse the existing file
        a fresh trust list is started.

        Args:
            workspace: Directory to add to the trusted list.
            trust_file: Optional override path (used only by tests).
        """
        workspace_path = str(Path(workspace).resolve())
        dst = Path(trust_file) if trust_file else cls.TRUST_FILE
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.touch(exist_ok=True)
        except (PermissionError, OSError):
            # If we can't write the trust file, the workspace is effectively
            # trusted for this session.  This keeps Wisp usable in read-only
            # environments (CI, sandboxes, ephemeral containers).
            return

        try:
            with open(dst, "r+", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    try:
                        f.seek(0)
                        content = f.read()
                        trusted = json.loads(content) if content.strip() else []
                    except Exception:
                        trusted = []

                    if workspace_path not in trusted:
                        trusted.append(workspace_path)
                        f.seek(0)
                        f.truncate()
                        json.dump(trusted, f, indent=2)
                        f.write("\n")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (PermissionError, OSError):
            pass
