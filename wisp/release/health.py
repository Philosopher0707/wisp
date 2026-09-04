"""Local health checks (M7, stdlib only). Each check returns
(name, ok, detail); the CLI renders them and exits nonzero on any failure.
"""
from __future__ import annotations
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _check_python() -> dict[str, Any]:
    ok = sys.version_info >= (3, 11)
    return {"name": "python-version", "ok": ok,
            "detail": sys.version.split()[0]}


def _check_store_writable() -> dict[str, Any]:
    db = os.environ.get("WISP_DB", "")
    target = Path(db) if db else Path.cwd() / ".wisp" / "wisp.db"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), timeout=5.0)
        conn.execute("CREATE TABLE IF NOT EXISTS _health_probe (id INTEGER)")
        conn.execute("DROP TABLE _health_probe")
        conn.close()
        return {"name": "store-writable", "ok": True, "detail": str(target)}
    except Exception as e:
        return {"name": "store-writable", "ok": False, "detail": str(e)}


def _check_audit_chain() -> dict[str, Any]:
    try:
        from wisp.infra.audit import AuditTrail
        bad = AuditTrail().verify()
        if bad is None:
            return {"name": "audit-chain", "ok": True, "detail": "intact"}
        return {"name": "audit-chain", "ok": False,
                "detail": f"first bad entry at line {bad}"}
    except Exception as e:
        return {"name": "audit-chain", "ok": False, "detail": str(e)}


def _check_crypto() -> dict[str, Any]:
    try:
        import cryptography
        return {"name": "cryptography-present", "ok": True,
                "detail": cryptography.__version__}
    except Exception:
        return {"name": "cryptography-present", "ok": False,
                "detail": "policy bundle verification unavailable"}


def _check_disk() -> dict[str, Any]:
    try:
        free_gb = shutil.disk_usage(Path.cwd()).free / 1e9
        return {"name": "disk-space", "ok": free_gb > 0.5,
                "detail": f"{free_gb:.1f} GB free"}
    except Exception as e:
        return {"name": "disk-space", "ok": False, "detail": str(e)}


def health_check() -> list[dict[str, Any]]:
    """Run all checks (order stable)."""
    return [_check_python(), _check_store_writable(), _check_audit_chain(),
            _check_crypto(), _check_disk()]
