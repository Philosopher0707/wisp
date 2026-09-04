# tests/test_release_health.py — health checks + diagnostics bundle (M7 T2).
import json

from wisp.release.diagnostics import support_bundle
from wisp.release.health import health_check


def test_health_check_all_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("WISP_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    results = health_check()
    assert results, "expected at least one check"
    assert all(r["ok"] for r in results), results
    names = {r["name"] for r in results}
    assert {"python-version", "store-writable", "audit-chain",
            "cryptography-present", "disk-space"} <= names


def test_support_bundle_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("WISP_AUDIT_LOG", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("WISP_POLICY_PUBKEY", "")
    bundle = support_bundle(config={"model": "m",
                                    "api_key": "AKIAIOSFODNN7EXAMPLE",
                                    "nested": {"token": "ghp_abcdefghijklmnopqrstuvwxyZ1234567890"}})
    blob = json.dumps(bundle)
    assert "AKIA" not in blob and "ghp_" not in blob
    assert bundle["config"]["model"] == "m"
    assert bundle["redacted"] is True
    assert "health" in bundle and "wisp_version" in bundle
