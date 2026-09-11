"""G0 replay-bundle schema (test-only): build / validate / round-trip.

Implements PHASE13_REPLAY_BUNDLE_SPEC.md g0.1. Uses auth.secrets for
redaction scanning; fails validation on any secret hit.
"""
from __future__ import annotations

import hashlib
import json

BUNDLE_VERSION = "g0.1"

REQUIRED = (
    "bundle_version", "wisp_version", "commit_sha", "request",
    "provider", "model", "model_configuration", "execution_configuration",
    "observed_decisions", "timestamps",
)
GRAPH_REQUIRED = ("graph_hash", "graph_definition", "policy_fingerprint")
TRUNCATION_STATES = ("complete", "truncated", "stalled", "UNKNOWN")


def _redact_text(s: str) -> str:
    from wisp.auth.secrets import redact
    return redact(s)


def make_bundle(**fields) -> dict:
    """Assemble a bundle: redacts free text (recursive), stamps version."""
    from wisp.auth.secrets import redact_record
    bundle = {"bundle_version": BUNDLE_VERSION}
    for k, v in fields.items():
        bundle[k] = redact_record(v)
    if bundle.get("graph_definition") and not bundle.get("graph_hash"):
        canon = json.dumps(bundle["graph_definition"], sort_keys=True)
        bundle["graph_hash"] = hashlib.sha256(canon.encode()).hexdigest()[:16]
    return bundle


def bundle_hash(bundle: dict) -> str:
    canon = json.dumps(bundle, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def validate(bundle: dict) -> list[str]:
    """Return error strings; [] means valid."""
    errs = []
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        errs.append(f"bundle_version must be {BUNDLE_VERSION!r}")
    for f in REQUIRED:
        if f not in bundle:
            errs.append(f"missing required field {f}")
    if "graph_definition" in bundle or "graph_hash" in bundle:
        for f in GRAPH_REQUIRED:
            if f not in bundle:
                errs.append(f"missing graph field {f}")
    if ("truncation_state" in bundle
            and bundle["truncation_state"] not in TRUNCATION_STATES):
        errs.append("bad truncation_state")
    # secrets must never be present, even redacted-shape is fine but raw is not
    from wisp.auth.secrets import scan_for_secrets
    hits = scan_for_secrets(json.dumps(bundle, default=str))
    if hits:
        errs.append(f"secret scan hits: {hits[:3]}")
    return errs


def round_trip(bundle: dict) -> dict:
    """JSON serialize/parse; hash must be stable across the trip."""
    h0 = bundle_hash(bundle)
    back = json.loads(json.dumps(bundle, default=str))
    assert bundle_hash(back) == h0, "hash unstable across round-trip"
    return back
