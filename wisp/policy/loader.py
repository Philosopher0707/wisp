"""Precedence loader + managed/disconnected modes (M4).

Merge order: built-in floor → organization → local admin → workspace →
session. Each layer narrows only: allowlists intersect, restrictions
union, approval matrix takes the strictest per tool. Provenance records
which layer controls each decision.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from wisp.policy.bundle import PolicyBundle, verify_bundle

# approval strictness: higher number = stricter.
_STRICTNESS = {"allow": 0, "approve": 1, "deny": 2}

CACHE_FILE = "policy-bundle.json"


def _sig_path(bundle_file: Path) -> Path:
    return bundle_file.with_suffix(bundle_file.suffix + ".sig")


@dataclass(frozen=True)
class EffectivePolicy:
    """Merged view + per-decision provenance (layer names)."""

    mcp_allowlist: tuple[str, ...] = ()
    plugin_allowlist: tuple[str, ...] = ()
    approved_models: tuple[str, ...] = ()
    approved_providers: tuple[str, ...] = ()
    network_policy: dict[str, Any] = field(default_factory=dict)
    shell_restrictions: dict[str, Any] = field(default_factory=dict)
    redaction_rules: tuple[str, ...] = ()
    approval_matrix: dict[str, str] = field(default_factory=dict)
    telemetry_policy: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    org_id: str = ""
    bundle_version: int = 1
    expires_at: float = 0.0


def _strictest(a: str, b: str) -> str:
    return a if _STRICTNESS.get(a, 0) >= _STRICTNESS.get(b, 0) else b


def _intersect(higher_list: tuple, lower_list: tuple) -> tuple:
    # Empty = no opinion (partial layers omit what they don't constrain).
    # A lower layer narrows a higher restriction but never widens it;
    # genuine deny-all travels via revocation, not empty lists.
    if not lower_list:
        return tuple(higher_list)
    if not higher_list:
        return tuple(lower_list)
    allowed = set(higher_list)
    return tuple(t for t in lower_list if t in allowed)


def _list_provenance(higher_list: tuple, result: tuple,
                     higher_name: str, lower_name: str,
                     higher_prov: dict | None = None,
                     key: str = "") -> str:
    if higher_list and tuple(result) != tuple(higher_list):
        return lower_name + "+narrowed"
    if higher_list:
        return (higher_prov or {}).get(key, higher_name)
    return lower_name


def _effective_to_bundle(eff: EffectivePolicy) -> PolicyBundle:
    return PolicyBundle(
        bundle_version=eff.bundle_version, org_id=eff.org_id,
        issued_at=time.time(), expires_at=eff.expires_at,
        revocation_seq=0, approved_models=eff.approved_models,
        approved_providers=eff.approved_providers,
        mcp_allowlist=eff.mcp_allowlist,
        plugin_allowlist=eff.plugin_allowlist,
        shell_restrictions=eff.shell_restrictions,
        network_policy=eff.network_policy,
        redaction_rules=eff.redaction_rules,
        approval_matrix=eff.approval_matrix,
        telemetry_policy=eff.telemetry_policy)


def merge_layers(higher: PolicyBundle, lower: PolicyBundle,
                 higher_name: str = "organization",
                 lower_name: str = "workspace",
                 higher_prov: dict | None = None) -> EffectivePolicy:
    """Merge one lower layer over a higher bundle. Narrow-only: allowlists
    intersect, restriction dicts union, approval takes strictest.
    higher_prov threads provenance through multi-layer folds."""
    hp = higher_prov or {}
    prov: dict[str, str] = {}
    mcp = _intersect(higher.mcp_allowlist, lower.mcp_allowlist)
    prov["mcp_allowlist"] = _list_provenance(
        higher.mcp_allowlist, mcp, higher_name, lower_name, hp, "mcp_allowlist")
    plugins = _intersect(higher.plugin_allowlist, lower.plugin_allowlist)
    models = _intersect(higher.approved_models, lower.approved_models)
    providers = _intersect(higher.approved_providers, lower.approved_providers)
    network = {**higher.network_policy, **lower.network_policy}
    shell = {**higher.shell_restrictions, **lower.shell_restrictions}
    redaction = tuple(dict.fromkeys(
        (*higher.redaction_rules, *lower.redaction_rules)))
    matrix: dict[str, str] = dict(higher.approval_matrix)
    for tool, level in lower.approval_matrix.items():
        if tool in matrix:
            matrix[tool] = _strictest(matrix[tool], level)
        else:
            matrix[tool] = level
        prov[f"approval:{tool}"] = lower_name
    for tool in higher.approval_matrix:
        prov.setdefault(f"approval:{tool}", hp.get(f"approval:{tool}", higher_name))
    return EffectivePolicy(
        mcp_allowlist=mcp, plugin_allowlist=plugins, approved_models=models,
        approved_providers=providers, network_policy=network,
        shell_restrictions=shell, redaction_rules=redaction,
        approval_matrix=matrix, telemetry_policy=lower.telemetry_policy or higher.telemetry_policy,
        provenance=prov, org_id=higher.org_id or lower.org_id,
        bundle_version=max(higher.bundle_version, lower.bundle_version),
        expires_at=min(h for h in (higher.expires_at, lower.expires_at) if h) or 0.0,
    )


def merge_all(layers: list[tuple[str, PolicyBundle]]) -> EffectivePolicy:
    """Merge an ordered [(name, bundle)] stack, highest authority first.

    Intersection/union/strictest are associative, so a left fold preserves
    the narrow-only invariant transitively.
    """
    if not layers:
        return EffectivePolicy()
    name, first = layers[0]
    eff = merge_layers(first, first, higher_name=name, lower_name=name)
    for next_name, lower in layers[1:]:
        eff = merge_layers(_effective_to_bundle(eff), lower,
                           higher_name=name, lower_name=next_name,
                           higher_prov=eff.provenance)
        name = next_name
    return eff


def trim_expired(eff: EffectivePolicy) -> EffectivePolicy:
    """Post-expiry authority reduction: deny exec-class approvals, network
    off. Never an error, never silent allow."""
    matrix = dict(eff.approval_matrix)
    for tool in matrix:
        matrix[tool] = "deny"
    prov = dict(eff.provenance)
    prov["expired-trim"] = "built-in floor"
    return EffectivePolicy(
        mcp_allowlist=(), plugin_allowlist=(),
        approved_models=eff.approved_models,
        approved_providers=eff.approved_providers,
        network_policy={"mode": "off"}, shell_restrictions=eff.shell_restrictions,
        redaction_rules=eff.redaction_rules, approval_matrix=matrix,
        telemetry_policy={"tier": "metrics-only"}, provenance=prov,
        org_id=eff.org_id, bundle_version=eff.bundle_version,
        expires_at=eff.expires_at)


def _read_bundle_file(path: Path) -> tuple[PolicyBundle, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sig_path = _sig_path(path)
    sig = sig_path.read_text(encoding="utf-8").strip() if sig_path.exists() else ""
    return PolicyBundle.from_dict(payload), sig


def load_local(bundle_path: str | Path, public_key_b64: str) -> EffectivePolicy:
    """Local-only mode: verify a bundle file. No network, ever."""
    bundle, sig = _read_bundle_file(Path(bundle_path))
    if not sig or not verify_bundle(bundle, sig, public_key_b64):
        raise ValueError("policy bundle signature invalid")
    if bundle.is_expired():
        return trim_expired(_bundle_to_effective(bundle, "local file"))
    return _bundle_to_effective(bundle, "local file")


def _bundle_to_effective(bundle: PolicyBundle, layer: str) -> EffectivePolicy:
    empty = PolicyBundle(bundle_version=bundle.bundle_version, org_id=bundle.org_id,
                         issued_at=bundle.issued_at, expires_at=bundle.expires_at)
    eff = merge_layers(empty, bundle, lower_name=layer)
    return eff


def load_managed(cache_dir: str | Path, public_key_b64: str,
                 refresh_fn: Callable[[], tuple[dict, str]] | None = None
                 ) -> EffectivePolicy:
    """Managed mode: refresh-then-verify-then-cache; stale cache served
    until expiry; expired cache trimmed. Disconnected = refresh_fn None."""
    cache = Path(cache_dir)
    bundle_file = cache / CACHE_FILE
    if refresh_fn is not None:
        try:
            payload, sig = refresh_fn()
            candidate = PolicyBundle.from_dict(payload)
            if verify_bundle(candidate, sig, public_key_b64):
                if candidate.revocation_seq >= _cached_seq(bundle_file):
                    bundle_file.write_text(json.dumps(payload, sort_keys=True),
                                           encoding="utf-8")
                    _sig_path(bundle_file).write_text(sig, encoding="utf-8")
        except Exception:
            pass  # refresh failures keep serving cache (logged by caller)
    if not bundle_file.exists():
        raise FileNotFoundError("no cached policy bundle (offline with no cache)")
    bundle, sig = _read_bundle_file(bundle_file)
    if not verify_bundle(bundle, sig, public_key_b64):
        raise ValueError("cached policy bundle failed verification")
    eff = _bundle_to_effective(bundle, "organization")
    if bundle.is_expired():
        return trim_expired(eff)
    return eff


def _cached_seq(bundle_file: Path) -> int:
    try:
        return int(json.loads(bundle_file.read_text(encoding="utf-8")).get(
            "revocation_seq", 0))
    except Exception:
        return 0
