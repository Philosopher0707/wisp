# tests/test_policy_modes.py — local/managed/disconnected behavior (M4 T2).
import json
import time

import pytest
from wisp.policy.bundle import (
    PolicyBundle,
    generate_keypair,
    sign_bundle,
)
from wisp.policy.loader import load_local, load_managed


def _keys():
    return generate_keypair()


def _payload(**overrides):
    now = time.time()
    base = {"bundle_version": 1, "org_id": "acme", "issued_at": now,
            "expires_at": now + 3600, "revocation_seq": 1,
            "approval_matrix": {"run_bash": "approve"}}
    base.update(overrides)
    return base


def _write_bundle_file(path, payload, priv):
    bundle = PolicyBundle.from_dict(payload)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    path.with_suffix(path.suffix + ".sig").write_text(
        sign_bundle(bundle, priv), encoding="utf-8")


def test_load_local_verifies_and_merges(tmp_path):
    priv, pub = _keys()
    f = tmp_path / "bundle.json"
    _write_bundle_file(f, _payload(), priv)
    eff = load_local(f, pub)
    assert eff.approval_matrix["run_bash"] == "approve"
    assert eff.org_id == "acme"


def test_load_local_rejects_tampered(tmp_path):
    priv, pub = _keys()
    f = tmp_path / "bundle.json"
    _write_bundle_file(f, _payload(), priv)
    evil = dict(json.loads(f.read_text()), org_id="evil")
    f.write_text(json.dumps(evil, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="signature invalid"):
        load_local(f, pub)


def test_load_local_expired_trims(tmp_path):
    priv, pub = _keys()
    f = tmp_path / "bundle.json"
    _write_bundle_file(f, _payload(expires_at=time.time() - 5,
                                   approval_matrix={"run_bash": "allow"}), priv)
    eff = load_local(f, pub)
    assert eff.approval_matrix["run_bash"] == "deny"
    assert eff.network_policy == {"mode": "off"}


def test_managed_caches_and_serves_stale(tmp_path):
    priv, pub = _keys()
    payload = _payload()
    calls = {"n": 0}

    def refresh():
        calls["n"] += 1
        return payload, sign_bundle(PolicyBundle.from_dict(payload), priv)

    eff = load_managed(tmp_path, pub, refresh_fn=refresh)
    assert eff.org_id == "acme" and calls["n"] == 1

    def failing():
        raise ConnectionError("offline")

    eff2 = load_managed(tmp_path, pub, refresh_fn=failing)
    assert eff2.org_id == "acme"  # stale cache served


def test_managed_rejects_rollback(tmp_path):
    priv, pub = _keys()
    new = _payload(revocation_seq=5)
    load_managed(tmp_path, pub, refresh_fn=lambda: (
        new, sign_bundle(PolicyBundle.from_dict(new), priv)))
    old = _payload(revocation_seq=2)
    load_managed(tmp_path, pub, refresh_fn=lambda: (
        old, sign_bundle(PolicyBundle.from_dict(old), priv)))
    from wisp.policy.loader import CACHE_FILE
    import json as _json
    cached = _json.loads((tmp_path / CACHE_FILE).read_text())
    assert cached["revocation_seq"] == 5


def test_managed_no_cache_offline_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_managed(tmp_path, _keys()[1], refresh_fn=None)


def test_disconnected_expired_trims(tmp_path):
    priv, pub = _keys()
    _write_bundle_file(tmp_path / "policy-bundle.json",
                       _payload(expires_at=time.time() - 5), priv)
    # seed the cache the way a prior managed refresh would have
    import shutil
    src = tmp_path / "policy-bundle.json"
    (tmp_path / "cache").mkdir()
    shutil.copy(src, tmp_path / "cache" / "policy-bundle.json")
    shutil.copy(src.with_suffix(".json.sig"),
                tmp_path / "cache" / "policy-bundle.json.sig")
    eff = load_managed(tmp_path / "cache", pub, refresh_fn=None)
    assert eff.approval_matrix.get("run_bash", "deny") == "deny"
