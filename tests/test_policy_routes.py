# tests/test_policy_routes.py — minimal control-plane API (M4 T4).
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wisp.policy.bundle import PolicyBundle, generate_keypair, sign_bundle


@pytest.fixture(autouse=True)
def _isolated_rate_limiter(tmp_path, monkeypatch):
    """Route tests share a home-DB 30-req/min limiter; isolate per module."""
    from wisp.server import deps
    monkeypatch.setattr(
        deps, "_rate_limiter_instance",
        deps.SQLiteRateLimiter(db_path=tmp_path / "rl.db",
                               max_requests=1000, window_seconds=60))


def _payload(**overrides):
    now = time.time()
    base = {"bundle_version": 1, "org_id": "acme", "issued_at": now,
            "expires_at": now + 3600, "revocation_seq": 1}
    base.update(overrides)
    return base


def _app(pubkey=None):
    from wisp.server.routes.policy import router
    app = FastAPI()
    app.include_router(router)
    app.state.policy_pubkey = pubkey
    app.state.policy_bundle = None
    app.state.policy_signature = ""
    return app


def _signed(payload, priv):
    bundle = PolicyBundle.from_dict(payload)
    return {"payload": payload, "signature": sign_bundle(bundle, priv)}


def test_publish_current_flow():
    priv, pub = generate_keypair()
    client = TestClient(_app(pub))
    assert client.get("/api/policy/current").status_code == 404
    r = client.post("/api/policy/publish", json=_signed(_payload(), priv))
    assert r.status_code == 200, r.text
    cur = client.get("/api/policy/current")
    assert cur.status_code == 200
    assert cur.json()["bundle"]["org_id"] == "acme"


def test_tampered_publish_rejected():
    priv, pub = generate_keypair()
    client = TestClient(_app(pub))
    body = _signed(_payload(), priv)
    body["payload"]["org_id"] = "evil"
    r = client.post("/api/policy/publish", json=body)
    assert r.status_code == 422


def test_rollback_publish_rejected():
    priv, pub = generate_keypair()
    client = TestClient(_app(pub))
    client.post("/api/policy/publish", json=_signed(_payload(revocation_seq=5), priv))
    r = client.post("/api/policy/publish", json=_signed(_payload(revocation_seq=2), priv))
    assert r.status_code == 422


def test_revoke_bumps_sequence():
    priv, pub = generate_keypair()
    client = TestClient(_app(pub))
    client.post("/api/policy/publish", json=_signed(_payload(), priv))
    r = client.post("/api/policy/revoke", json={"revocation_seq": 9})
    assert r.status_code == 200
    assert client.get("/api/policy/current").json()["bundle"]["revocation_seq"] == 9
    # stale revoke rejected
    assert client.post("/api/policy/revoke", json={"revocation_seq": 3}).status_code == 422


def test_no_pubkey_configured():
    client = TestClient(_app(None))
    assert client.get("/api/policy/current").status_code == 503
    priv, _ = generate_keypair()
    assert client.post("/api/policy/publish",
                       json=_signed(_payload(), priv)).status_code == 503


def test_health():
    priv, pub = generate_keypair()
    client = TestClient(_app(pub))
    assert client.get("/api/policy/health").json()["configured"] is True
    assert TestClient(_app(None)).get("/api/policy/health").json()["configured"] is False
