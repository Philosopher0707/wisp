"""Signed policy bundle: format + Ed25519 verification (M4).

Bundle = canonical JSON (sorted keys, compact separators) + detached
base64 Ed25519 signature. Verification needs only the org public key —
fully offline/air-gap compatible. Unknown sections are preserved but
ignored (forward compatibility).
"""
from __future__ import annotations
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

BUNDLE_VERSION = 1

_KNOWN_TOP_LEVEL = frozenset({
    "bundle_version", "org_id", "issued_at", "expires_at", "revocation_seq",
    "approved_models", "approved_providers", "mcp_allowlist",
    "plugin_allowlist", "shell_restrictions", "network_policy",
    "redaction_rules", "approval_matrix", "telemetry_policy",
})


@dataclass(frozen=True)
class PolicyBundle:
    bundle_version: int = BUNDLE_VERSION
    org_id: str = ""
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    revocation_seq: int = 0
    approved_models: tuple[str, ...] = ()
    approved_providers: tuple[str, ...] = ()
    mcp_allowlist: tuple[str, ...] = ()
    plugin_allowlist: tuple[str, ...] = ()
    shell_restrictions: dict[str, Any] = field(default_factory=dict)
    network_policy: dict[str, Any] = field(default_factory=dict)
    redaction_rules: tuple[str, ...] = ()
    approval_matrix: dict[str, str] = field(default_factory=dict)
    telemetry_policy: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "bundle_version": self.bundle_version, "org_id": self.org_id,
            "issued_at": self.issued_at, "expires_at": self.expires_at,
            "revocation_seq": self.revocation_seq,
            "approved_models": list(self.approved_models),
            "approved_providers": list(self.approved_providers),
            "mcp_allowlist": list(self.mcp_allowlist),
            "plugin_allowlist": list(self.plugin_allowlist),
            "shell_restrictions": self.shell_restrictions,
            "network_policy": self.network_policy,
            "redaction_rules": list(self.redaction_rules),
            "approval_matrix": self.approval_matrix,
            "telemetry_policy": self.telemetry_policy,
        }
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyBundle":
        known = {k: v for k, v in d.items() if k in _KNOWN_TOP_LEVEL}
        extra = {k: v for k, v in d.items() if k not in _KNOWN_TOP_LEVEL}
        for k in ("approved_models", "approved_providers", "mcp_allowlist",
                  "plugin_allowlist", "redaction_rules"):
            if k in known:
                known[k] = tuple(known[k] or ())
        return cls(**known, extra=extra)  # type: ignore[arg-type]


def canonical_bytes(bundle: PolicyBundle) -> bytes:
    """Canonical encoding: sorted keys, compact separators, UTF-8."""
    return json.dumps(bundle.to_dict(), sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def generate_keypair() -> tuple[Any, str]:
    """Return (private_key, base64_public_key). Private key stays in the
    OS keychain / 0600 file; only the public half is distributed."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes_raw()).decode("ascii")
    return priv, pub_b64


def private_bytes_raw(private_key: Any) -> bytes:
    """32-byte seed for 0600-file / keychain storage."""
    return private_key.private_bytes_raw()


def sign_bundle(bundle: PolicyBundle, private_key: Any) -> str:
    """Detached base64 signature over the canonical bytes."""
    return base64.b64encode(
        private_key.sign(canonical_bytes(bundle))).decode("ascii")


def verify_bundle(bundle: PolicyBundle, signature_b64: str,
                  public_key_b64: str) -> bool:
    """True iff the signature is valid for these exact bytes. Any exception
    (bad key, bad encoding, bad signature) is a rejection, not an error."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        pub = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(signature_b64), canonical_bytes(bundle))
        return True
    except Exception:
        return False
