# tests/test_policy_cli.py — admin CLI golden outputs (M4 T3).
import io
import json
import time

import pytest
from wisp.policy.bundle import PolicyBundle, generate_keypair, sign_bundle
from wisp.policy.cli import main as policy_main


@pytest.fixture()
def env_bundle(tmp_path, monkeypatch):
    priv, pub = generate_keypair()
    now = time.time()
    payload = {"bundle_version": 1, "org_id": "acme", "issued_at": now,
               "expires_at": now + 3600, "revocation_seq": 3,
               "approved_models": ["ollama/qwen"], "mcp_allowlist": ["git"],
               "approval_matrix": {"run_bash": "deny"}}
    bundle = PolicyBundle.from_dict(payload)
    f = tmp_path / "bundle.json"
    f.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    f.with_suffix(".json.sig").write_text(sign_bundle(bundle, priv),
                                           encoding="utf-8")
    monkeypatch.setenv("WISP_POLICY_BUNDLE", str(f))
    monkeypatch.setenv("WISP_POLICY_PUBKEY", pub)
    return f


def _run(args):
    out = io.StringIO()
    code = policy_main(args, out=out)
    return code, out.getvalue()


def test_inspect(env_bundle):
    code, text = _run(["inspect"])
    assert code == 0
    assert "acme" in text and "ollama/qwen" in text and "revocation_seq=3" in text


def test_verify_ok(env_bundle):
    code, text = _run(["verify"])
    assert code == 0 and "valid" in text.lower()


def test_verify_tampered_fails(env_bundle):
    payload = json.loads(env_bundle.read_text())
    payload["org_id"] = "evil"
    env_bundle.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    code, _ = _run(["verify"])
    assert code == 1


def test_explain_names_layer(env_bundle):
    code, text = _run(["explain", "run_bash", "--args", '{"command": "rm -rf /"}'])
    assert code == 0
    assert "run_bash" in text and "deny" in text


def test_dry_run_reports_obligations(env_bundle):
    code, text = _run(["dry-run", "read_file", "--args", '{"path": "a.py"}'])
    assert code == 0 and "allowed" in text.lower()


def test_health_no_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WISP_POLICY_CACHE", str(tmp_path / "empty-cache"))
    monkeypatch.delenv("WISP_POLICY_BUNDLE", raising=False)
    code, text = _run(["health"])
    assert code == 0 and "no cached bundle" in text.lower()


def test_unknown_command():
    code, text = _run(["frobnicate"])
    assert code == 2 and "unknown" in text.lower()
