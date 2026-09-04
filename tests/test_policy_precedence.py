# tests/test_policy_precedence.py — narrow-only merge + provenance (M4 T2).
import time

from wisp.policy.bundle import PolicyBundle
from wisp.policy.explain import dry_run, explain_denial
from wisp.policy.loader import (
    EffectivePolicy,
    merge_layers,
    trim_expired,
)


def _layer(**overrides):
    now = time.time()
    base = {"bundle_version": 1, "org_id": "t", "issued_at": now,
            "expires_at": now + 3600, "revocation_seq": 1}
    base.update(overrides)
    return PolicyBundle.from_dict(base)


def test_allowlist_intersection_narrows():
    org = _layer(mcp_allowlist=["a", "b"])
    ws = _layer(mcp_allowlist=["b", "c"])
    eff = merge_layers(org, ws)
    assert eff.mcp_allowlist == ("b",)
    assert eff.provenance["mcp_allowlist"] == "workspace+narrowed"


def test_restriction_union():
    org = _layer(network_policy={"egress": ["proxy.internal"]})
    ws = _layer(network_policy={"dns": ["corp"]})
    eff = merge_layers(org, ws)
    assert eff.network_policy == {"egress": ["proxy.internal"], "dns": ["corp"]}


def test_approval_strictest_wins():
    org = _layer(approval_matrix={"run_bash": "approve"})
    ws = _layer(approval_matrix={"run_bash": "deny"})
    eff = merge_layers(org, ws)
    assert eff.approval_matrix["run_bash"] == "deny"
    assert eff.provenance["approval:run_bash"] == "workspace"


def test_lower_layer_cannot_widen():
    org = _layer(mcp_allowlist=["a"])
    ws = _layer(mcp_allowlist=["a", "evil"])
    eff = merge_layers(org, ws)
    assert "evil" not in eff.mcp_allowlist


def test_narrow_only_property():
    import random
    rng = random.Random(42)
    tools = ["t1", "t2", "t3"]
    for _ in range(50):
        higher = _layer(
            mcp_allowlist=rng.sample(["a", "b", "c"], rng.randint(0, 3)),
            approval_matrix={t: rng.choice(["allow", "approve", "deny"]) for t in tools})
        lower = _layer(
            mcp_allowlist=rng.sample(["a", "b", "c", "x"], rng.randint(0, 4)),
            approval_matrix={t: rng.choice(["allow", "approve", "deny"]) for t in tools})
        eff = merge_layers(higher, lower)
        if higher.mcp_allowlist:
            assert set(eff.mcp_allowlist) <= set(higher.mcp_allowlist)
        else:
            # higher silent: lower's own restriction stands, verbatim
            assert set(eff.mcp_allowlist) == set(lower.mcp_allowlist)
        strict = {"allow": 0, "approve": 1, "deny": 2}
        for t in tools:
            if t in higher.approval_matrix:
                assert strict[eff.approval_matrix[t]] >= strict[higher.approval_matrix[t]]


def test_explain_denial_names_layer():
    eff = EffectivePolicy(
        mcp_allowlist=(), approval_matrix={"run_bash": "deny"},
        provenance={"approval:run_bash": "organization"})
    msg = explain_denial("run_bash", {"command": "rm -rf /"}, eff)
    assert "organization" in msg and "run_bash" in msg


def test_dry_run_no_execution():
    eff = EffectivePolicy(approval_matrix={"run_bash": "approve"})
    d = dry_run("run_bash", {"command": "ls"}, eff)
    assert d["allowed"] is True
    assert any("approval" in o for o in d["obligations"])


def test_trim_expired_denies_exec():
    eff = EffectivePolicy(approval_matrix={"run_bash": "allow"},
                          network_policy={"mode": "allowlisted"})
    trimmed = trim_expired(eff)
    assert trimmed.approval_matrix["run_bash"] == "deny"
    assert trimmed.network_policy["mode"] == "off"
    assert trimmed.provenance.get("expired-trim") == "built-in floor"
