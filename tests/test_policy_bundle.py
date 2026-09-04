# tests/test_policy_bundle.py — signed bundle create/verify (M4 T1).
import base64
import time

from wisp.policy.bundle import (
    PolicyBundle,
    canonical_bytes,
    generate_keypair,
    sign_bundle,
    verify_bundle,
)


def _bundle(**overrides):
    now = time.time()
    base = {"bundle_version": 1, "org_id": "acme", "issued_at": now,
            "expires_at": now + 3600, "revocation_seq": 1,
            "approved_models": ["ollama/qwen"], "mcp_allowlist": ["git"]}
    base.update(overrides)
    return PolicyBundle.from_dict(base)


def test_sign_verify_round_trip():
    priv, pub = generate_keypair()
    b = _bundle()
    sig = sign_bundle(b, priv)
    assert verify_bundle(b, sig, pub) is True


def test_tamper_rejected():
    priv, pub = generate_keypair()
    b = _bundle()
    sig = sign_bundle(b, priv)
    tampered = PolicyBundle.from_dict({**b.to_dict(), "org_id": "evil"})
    assert verify_bundle(tampered, sig, pub) is False


def test_wrong_key_rejected():
    priv, _ = generate_keypair()
    _, pub2 = generate_keypair()
    b = _bundle()
    assert verify_bundle(b, sign_bundle(b, priv), pub2) is False


def test_expired_bundle_flagged():
    b = _bundle(expires_at=time.time() - 10)
    assert b.is_expired() is True
    assert _bundle().is_expired() is False


def test_unknown_sections_tolerated():
    b = PolicyBundle.from_dict({**_bundle().to_dict(), "future_section": {"x": 1}})
    assert b.extra.get("future_section") == {"x": 1}
    # round-trips losslessly (forward compatibility)
    assert PolicyBundle.from_dict(b.to_dict()).extra == b.extra


def test_canonical_bytes_stable():
    b = _bundle()
    assert canonical_bytes(b) == canonical_bytes(PolicyBundle.from_dict(b.to_dict()))
    assert b"  " not in canonical_bytes(b) and b"\n" not in canonical_bytes(b)


def test_keypair_serialization():
    from wisp.policy.bundle import private_bytes_raw
    priv, pub = generate_keypair()
    assert len(private_bytes_raw(priv)) == 32
    assert len(base64.b64decode(pub)) == 32
