# tests/test_auth_decision.py
from wisp.auth.decision import AuthorizationDecision, authorize
from wisp.auth.principal import derive_subagent, local_principal
from wisp.auth.workspace_trust import WorkspaceTrust, classify_workspace


def test_quarantined_write_denied_despite_full_mode():
    p = local_principal(workspace="/evil", profile="p")
    d = authorize(p, "write_file", {"path": "x.py"},
                  workspace_trust=WorkspaceTrust.QUARANTINED,
                  permission_mode="full")
    assert d.allowed is False
    assert d.controlling_layer == "workspace"
    assert isinstance(d, AuthorizationDecision)


def test_trusted_read_allowed_without_approval():
    p = local_principal(workspace="/w", profile="p")
    d = authorize(p, "read_file", {"path": "a.py"},
                  workspace_trust=WorkspaceTrust.TRUSTED,
                  permission_mode="auto_edit")
    assert d.allowed is True and d.approval_required is False


def test_bash_requires_approval_outside_full():
    p = local_principal(workspace="/w", profile="p")
    d = authorize(p, "run_bash", {"command": "ls"},
                  workspace_trust=WorkspaceTrust.TRUSTED,
                  permission_mode="auto_edit")
    assert d.allowed is True and d.approval_required is True
    assert d.controlling_layer == "approval"


def test_subagent_capability_enforced():
    parent = local_principal(workspace="/w", profile="p")
    child = derive_subagent(parent, capabilities=frozenset({"read_file"}))
    d = authorize(child, "run_bash", {"command": "ls"},
                  workspace_trust=WorkspaceTrust.TRUSTED,
                  permission_mode="full")
    assert d.allowed is False and d.controlling_layer == "principal"


def test_classify_workspace(tmp_path):
    assert classify_workspace(tmp_path, trusted_roots=frozenset({tmp_path})) == WorkspaceTrust.TRUSTED
    assert classify_workspace(tmp_path / "child", trusted_roots=frozenset({tmp_path})) == WorkspaceTrust.TRUSTED
    assert classify_workspace("/definitely/not/trusted", trusted_roots=frozenset({tmp_path})) == WorkspaceTrust.REVIEW_REQUIRED
    q = tmp_path / "q"
    q.mkdir()
    (q / ".wisp-quarantine").write_text("untrusted checkout")
    assert classify_workspace(q, trusted_roots=frozenset()) == WorkspaceTrust.QUARANTINED
