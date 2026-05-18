"""Adapter layer — bridges old entry points to the new CompositionRoot system.

Allows gradual migration without breaking existing interfaces:
  - Old config objects -> new CompositionRoot
  - Old session API -> new UnifiedStore
  - Old tool API -> new ExtensionHost
  - Old security API -> new SecurityPolicy

Usage:
    from wisp.adapters import create_runtime
    runtime = create_runtime(old_config)
    # Use runtime just like the old session manager
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from wisp.composition import CompositionRoot
from wisp.infra.security import PermissionMode

logger = logging.getLogger(__name__)


def create_runtime(config: Any) -> Any:
    """Create an AgentRuntime from an old-style config object.

    Accepts any object with attributes:
      - model: str
      - workspace: str
      - permission_mode: str ("full", "read_only", "ask")
      - db_path: str
    """
    # Normalize config
    db_path = getattr(config, "db_path", "")
    if not db_path:
        db_path = Path.home() / ".config" / "wisp" / "wisp.db"
    else:
        db_path = Path(db_path)

    permission_mode_str = getattr(config, "permission_mode", "full").lower()
    try:
        permission_mode = PermissionMode(permission_mode_str)
    except ValueError:
        permission_mode = PermissionMode.FULL
        logger.warning(
            "Unknown permission mode '%s', defaulting to FULL",
            permission_mode_str,
        )

    # Create a new-style config dataclass
    from dataclasses import dataclass

    @dataclass
    class _NewConfig:
        db_path: Path
        permission_mode: PermissionMode
        model: str

    new_config = _NewConfig(
        db_path=db_path,
        permission_mode=permission_mode,
        model=getattr(config, "model", "qwen2.5-coder"),
    )

    root = CompositionRoot(new_config)
    return root.runtime
