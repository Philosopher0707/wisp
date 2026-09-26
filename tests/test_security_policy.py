"""TDD for SecurityPolicy — unified security decision layer.

Replaces: permission_mode checks, trust manager, hook path blocking,
and ad-hoc audit logging scattered across the codebase.
"""

import pytest
from pathlib import Path


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def full_policy():
    from wisp.infra.security import SecurityPolicy, PermissionMode
    return SecurityPolicy(permission_mode=PermissionMode.FULL)


@pytest.fixture
def read_only_policy():
    from wisp.infra.security import SecurityPolicy, PermissionMode
    return SecurityPolicy(permission_mode=PermissionMode.READ_ONLY)


@pytest.fixture
def ask_all_policy():
    from wisp.infra.security import SecurityPolicy, PermissionMode
    return SecurityPolicy(permission_mode=PermissionMode.ASK_ALL)


@pytest.fixture
def auto_edit_policy():
    from wisp.infra.security import SecurityPolicy, PermissionMode
    return SecurityPolicy(permission_mode=PermissionMode.AUTO_EDIT)


# ═══════════════════════════════════════════════════════════════════
# 1. Permission modes (coarse layer)
# ═══════════════════════════════════════════════════════════════════

class TestPermissionModes:
    """Coarse-grained permission mode checks."""

    def test_full_allows_all_tools(self, full_policy):
        from wisp.infra.security import Action
        result = full_policy.check(Action("run_bash", {"command": "rm -rf /"}), _ctx("/tmp"))
        assert result.allowed is True

    def test_read_only_blocks_writes(self, read_only_policy):
        from wisp.infra.security import Action
        result = read_only_policy.check(Action("write_file", {"path": "x.py"}), _ctx("/tmp"))
        assert result.allowed is False
        assert "READ_ONLY" in result.reason

    def test_read_only_allows_reads(self, read_only_policy):
        from wisp.infra.security import Action
        result = read_only_policy.check(Action("read_file", {"path": "x.py"}), _ctx("/tmp"))
        assert result.allowed is True

    def test_ask_all_blocks_writes(self, ask_all_policy):
        from wisp.infra.security import Action
        result = ask_all_policy.check(Action("write_file", {"path": "x.py"}), _ctx("/tmp"))
        # 13F.1: ask_all block set is REQUIRE_APPROVAL (allowed + gated),
        # not hard DENY — the gate prompts instead of rejecting.
        assert result.allowed is True and result.approval_required is True
        assert "approval" in result.reason.lower()

    def test_ask_all_allows_reads(self, ask_all_policy):
        from wisp.infra.security import Action
        result = ask_all_policy.check(Action("read_file", {"path": "x.py"}), _ctx("/tmp"))
        assert result.allowed is True

    def test_auto_edit_allows_edits(self, auto_edit_policy):
        from wisp.infra.security import Action
        result = auto_edit_policy.check(Action("edit_file", {"path": "x.py"}), _ctx("/tmp"))
        assert result.allowed is True

    def test_auto_edit_allows_bash_through_the_tier_router(self, auto_edit_policy):
        """`run_bash` left the hard-deny set on 2026-09-27.

        It was the one member whose execution is **confinement-routed** (`tools/bash.py` -> the tier
        router: Docker -> isolated PTY -> host), so the hard deny was blocking a command path that
        is already bounded. The residual is stated at the set and warned about per call: the PTY
        tier bounds RESOURCE USE and CREDENTIAL EXPOSURE, not FILESYSTEM REACH.
        """
        from wisp.infra.security import Action
        result = auto_edit_policy.check(Action("run_bash", {"command": "ls"}), _ctx("/tmp"))
        assert result.allowed is True

    def test_auto_edit_still_blocks_the_remote_writes(self, auto_edit_policy):
        """The half that did NOT move. git/gh writes mutate a shared remote, which no local sandbox
        tier contains — so they stay hard-denied in AUTO_EDIT."""
        from wisp.infra.security import Action
        for tool in ("git_push", "git_commit", "gh_pr_create", "git_branch"):
            result = auto_edit_policy.check(Action(tool, {}), _ctx("/tmp"))
            assert result.allowed is False, f"{tool} must still be hard-denied in AUTO_EDIT"


# ═══════════════════════════════════════════════════════════════════
# 2. Workspace trust (layer 2)
# ═══════════════════════════════════════════════════════════════════

class TestWorkspaceTrust:
    """Untrusted workspaces are blocked regardless of permission mode."""

    def test_untrusted_workspace_blocked(self, full_policy):
        from wisp.infra.security import Action
        full_policy = full_policy.with_trusted_workspaces({Path("/trusted")})
        result = full_policy.check(Action("read_file", {"path": "x.py"}), _ctx("/untrusted"))
        assert result.allowed is False
        assert "untrusted" in result.reason.lower()

    def test_trusted_workspace_allowed(self, full_policy):
        from wisp.infra.security import Action
        full_policy = full_policy.with_trusted_workspaces({Path("/trusted")})
        result = full_policy.check(Action("read_file", {"path": "x.py"}), _ctx("/trusted"))
        assert result.allowed is True


# ═══════════════════════════════════════════════════════════════════
# 3. Hook interception (layer 3)
# ═══════════════════════════════════════════════════════════════════

class TestHookInterception:
    """Hooks can block or modify tool calls."""

    def test_hook_can_block(self, full_policy):
        from wisp.infra.security import Action
        blocked = False

        def my_hook(action, context):
            nonlocal blocked
            if action.name == "run_bash":
                blocked = True
                return {"action": "block", "reason": "bash disabled"}
            return {"action": "allow"}

        full_policy = full_policy.with_hook(my_hook)
        result = full_policy.check(Action("run_bash", {"command": "ls"}), _ctx("/tmp"))
        assert result.allowed is False
        assert "bash disabled" in result.reason

    def test_hook_can_modify_args(self, full_policy):
        from wisp.infra.security import Action
        def my_hook(action, context):
            if action.name == "write_file":
                action.args["path"] = "/safe/" + action.args["path"]
            return {"action": "allow"}

        full_policy = full_policy.with_hook(my_hook)
        action = Action("write_file", {"path": "test.py"})
        result = full_policy.check(action, _ctx("/tmp"))
        assert result.allowed is True
        assert action.args["path"] == "/safe/test.py"


# ═══════════════════════════════════════════════════════════════════
# 4. Audit trail (layer 4)
# ═══════════════════════════════════════════════════════════════════

class TestAuditTrail:
    """Every security decision is auditable."""

    def test_approved_actions_are_audited(self, full_policy):
        from wisp.infra.security import Action
        full_policy.check(Action("read_file", {"path": "x.py"}), _ctx("/tmp"))
        audit = full_policy.audit_log()
        assert len(audit) == 1
        assert audit[0]["action"] == "read_file"
        assert audit[0]["allowed"] is True

    def test_blocked_actions_are_audited(self, read_only_policy):
        from wisp.infra.security import Action
        read_only_policy.check(Action("write_file", {"path": "x.py"}), _ctx("/tmp"))
        audit = read_only_policy.audit_log()
        assert len(audit) == 1
        assert audit[0]["allowed"] is False

    def test_audit_includes_reason(self, read_only_policy):
        from wisp.infra.security import Action
        read_only_policy.check(Action("write_file", {"path": "x.py"}), _ctx("/tmp"))
        audit = read_only_policy.audit_log()
        assert "READ_ONLY" in audit[0]["reason"]


# ═══════════════════════════════════════════════════════════════════
# 5. Layer ordering
# ═══════════════════════════════════════════════════════════════════

class TestLayerOrdering:
    """Layers are evaluated in order: mode → trust → hooks → audit."""

    def test_mode_blocks_before_trust(self):
        from wisp.infra.security import SecurityPolicy, PermissionMode, Action
        policy = SecurityPolicy(
            permission_mode=PermissionMode.READ_ONLY,
            trusted_workspaces={Path("/tmp")},
        )
        result = policy.check(Action("write_file", {"path": "x.py"}), _ctx("/tmp"))
        assert result.allowed is False
        assert "READ_ONLY" in result.reason  # mode blocked, not trust

    def test_trust_blocks_before_hooks(self):
        from wisp.infra.security import SecurityPolicy, PermissionMode, Action
        def hook(action, context):
            return {"action": "allow"}  # would allow

        policy = SecurityPolicy(
            permission_mode=PermissionMode.FULL,
            trusted_workspaces={Path("/other")},
            hooks=[hook],
        )
        result = policy.check(Action("read_file", {"path": "x.py"}), _ctx("/tmp"))
        assert result.allowed is False
        assert "untrusted" in result.reason.lower()


# ── helpers ────────────────────────────────────────────────────────

def _ctx(workspace: str):
    from wisp.infra.security import Context
    return Context(workspace=Path(workspace))
