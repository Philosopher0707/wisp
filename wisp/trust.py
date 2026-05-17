"""Workspace trust management — prevents loading untrusted workspace configs/hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path

class WorkspaceTrustManager:
    TRUST_FILE = Path.home() / ".config" / "wisp" / "trusted_workspaces.json"

    @classmethod
    def is_workspace_trusted(cls, workspace: Path | str) -> bool:
        """Check if the given workspace is trusted by the user."""
        # For testing, CI, or explicit override, we check environment variables
        if os.environ.get("WISP_TRUST_ALL_WORKSPACES") == "true":
            return True
        
        workspace_path = str(Path(workspace).resolve())
        
        # Default trust list check
        if not cls.TRUST_FILE.exists():
            return False
            
        try:
            trusted = json.loads(cls.TRUST_FILE.read_text(encoding="utf-8"))
            return workspace_path in trusted
        except Exception:
            return False

    @classmethod
    def trust_workspace(cls, workspace: Path | str) -> None:
        """Add the given workspace to the trusted workspaces list."""
        workspace_path = str(Path(workspace).resolve())
        cls.TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
        trusted = []
        if cls.TRUST_FILE.exists():
            try:
                trusted = json.loads(cls.TRUST_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        if workspace_path not in trusted:
            trusted.append(workspace_path)
            cls.TRUST_FILE.write_text(json.dumps(trusted, indent=2), encoding="utf-8")
