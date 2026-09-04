"""Redacted support bundle (M7). Versions, health, policy status, and
redacted config — never secrets. A security team can identify what
version, policies, model, and tools produced a change without receiving
key material.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any

from wisp.auth.secrets import redact_record
from wisp.release.health import health_check


def _wisp_version() -> str:
    try:
        from importlib.metadata import version
        return version("wisp")
    except Exception:
        pass
    try:
        import re
        text = (Path(__file__).resolve().parent.parent.parent
                / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def _policy_status() -> dict[str, Any]:
    cache = Path(os.environ.get("WISP_POLICY_CACHE",
                                Path.home() / ".wisp" / "policy"))
    bundle_file = cache / "policy-bundle.json"
    if not bundle_file.exists():
        return {"mode": "local-only", "cached": False}
    try:
        payload = json.loads(bundle_file.read_text(encoding="utf-8"))
        return {"mode": "managed", "cached": True,
                "org_id": payload.get("org_id", ""),
                "revocation_seq": payload.get("revocation_seq", 0)}
    except Exception:
        return {"mode": "managed", "cached": True, "unreadable": True}


def support_bundle(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the bundle. Config is redacted at construction (M2 rule)."""
    return {
        "wisp_version": _wisp_version(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "health": health_check(),
        "policy": _policy_status(),
        "config": redact_record(dict(config or {})),
        "redacted": True,
        "version": 1,
    }


def write_bundle(path: str | Path,
                 config: dict[str, Any] | None = None) -> Path:
    dest = Path(path)
    dest.write_text(json.dumps(support_bundle(config), indent=2, sort_keys=True),
                    encoding="utf-8")
    return dest
