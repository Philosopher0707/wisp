# tests/test_auth_principal.py
import pytest
from wisp.auth.principal import (
    Principal,
    PrincipalKind,
    derive_subagent,
    local_principal,
)


def test_local_principal_from_environment(tmp_path):
    p = local_principal(workspace=str(tmp_path), profile="personal")
    assert p.kind == PrincipalKind.HUMAN
    assert p.workspace == str(tmp_path) and p.profile == "personal"
    assert p.os_user  # non-empty on any OS
    assert p.principal_id  # stable non-empty id


def test_principal_id_stable():
    a = Principal(os_user="u", workspace="/w", profile="p")
    b = Principal(os_user="u", workspace="/w", profile="p")
    assert a.principal_id == b.principal_id


def test_derive_subagent_narrows():
    parent = local_principal(workspace="/w", profile="p")
    child = derive_subagent(parent, capabilities=frozenset({"read_file"}))
    assert child.kind == PrincipalKind.SUBAGENT
    assert child.parent_principal_id == parent.principal_id
    assert child.capabilities == frozenset({"read_file"})
    assert all(parent.allows_tool(t) for t in child.capabilities)
    assert not child.allows_tool("run_bash")


def test_derive_cannot_widen():
    parent = local_principal(workspace="/w", profile="p")
    child = derive_subagent(parent, capabilities=frozenset({"read_file"}))
    with pytest.raises(ValueError, match="narrow"):
        derive_subagent(child, capabilities=frozenset({"run_bash"}))
