# tests/test_auth_policy.py — bundle enforcement in authorize() (seam closure).
from wisp.auth.decision import authorize
from wisp.auth.principal import local_principal
from wisp.auth.workspace_trust import WorkspaceTrust
from wisp.policy.loader import EffectivePolicy


def _policy(**overrides):
    base = {"mcp_allowlist": (), "approval_matrix": {}, "provenance": {}}
    base.update(overrides)
    return EffectivePolicy(**base)


def _principal():
    return local_principal(workspace="/w", profile="personal")


def test_matrix_deny_wins_over_full_mode():
    pol = _policy(approval_matrix={"run_bash": "deny"},
                  provenance={"approval:run_bash": "organization"})
    d = authorize(_principal(), "run_bash", {"command": "ls"},
                  WorkspaceTrust.TRUSTED, permission_mode="full",
                  effective_policy=pol)
    assert d.allowed is False
    assert d.controlling_layer == "organization"
    assert "run_bash" in d.reason


def test_matrix_approve_forces_approval():
    pol = _policy(approval_matrix={"read_file": "approve"},
                  provenance={"approval:read_file": "workspace"})
    d = authorize(_principal(), "read_file", {"path": "a.py"},
                  WorkspaceTrust.TRUSTED, permission_mode="full",
                  effective_policy=pol)
    assert d.allowed is True and d.approval_required is True


def test_matrix_allow_no_constraint():
    pol = _policy(approval_matrix={"read_file": "allow"})
    d = authorize(_principal(), "read_file", {"path": "a.py"},
                  WorkspaceTrust.TRUSTED, permission_mode="full",
                  effective_policy=pol)
    assert d.allowed is True and d.approval_required is False


def test_mcp_allowlist_enforced():
    pol = _policy(mcp_allowlist=("git",))
    d = authorize(_principal(), "mcp:evil/exfiltrate", {},
                  WorkspaceTrust.TRUSTED, permission_mode="full",
                  effective_policy=pol)
    assert d.allowed is False and d.controlling_layer == "organization"
    ok = authorize(_principal(), "mcp:git/status", {},
                   WorkspaceTrust.TRUSTED, permission_mode="full",
                   effective_policy=pol)
    assert ok.allowed is True


def test_mcp_underscore_namespace_enforced():
    pol = _policy(mcp_allowlist=("git",))
    d = authorize(_principal(), "mcp__evil__tool", {},
                  WorkspaceTrust.TRUSTED, permission_mode="full",
                  effective_policy=pol)
    assert d.allowed is False


def test_empty_allowlist_means_no_opinion():
    pol = _policy(mcp_allowlist=())
    d = authorize(_principal(), "mcp:anything/tool", {},
                  WorkspaceTrust.TRUSTED, permission_mode="full",
                  effective_policy=pol)
    assert d.allowed is True


def test_no_policy_unchanged():
    d = authorize(_principal(), "run_bash", {"command": "ls"},
                  WorkspaceTrust.TRUSTED, permission_mode="full")
    assert d.allowed is True and d.approval_required is False
