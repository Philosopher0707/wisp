# tests/test_task_review.py — plan review render + approval capture (M6 T2/T3).
from wisp.task.review import approve_scope, render_review


def _plan():
    return {
        "goal": "Add login page",
        "files": ["web/login.py", "web/login_test.py"],
        "actions": [
            {"tool": "write_file",
             "args": {"path": "web/login.py", "content": "API_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"}},
            {"tool": "run_bash",
             "args": {"command": "pytest web/login_test.py"}},
            {"tool": "read_file", "args": {"path": "web/app.py"}},
        ],
        "test_plan": ["pytest web/login_test.py"],
    }


def test_render_covers_files_risks_budget():
    text = render_review(_plan())
    assert "web/login.py" in text
    assert "write" in text and "exec" in text  # risk classes
    assert "cost budget" in text.lower()
    assert "pytest web/login_test.py" in text  # test plan surfaced


def test_render_warns_secrets_and_policy():
    text = render_review(_plan(), approval_matrix={"run_bash": "deny"})
    assert "AKIA" not in text  # secret material never rendered
    assert "secret" in text.lower()  # warning present
    assert "run_bash" in text and "deny" in text  # policy obligation


def test_approve_whole_plan():
    decision = approve_scope(_plan(), scope="all", approver="dev")
    assert decision["approved"] is True
    assert decision["scope"] == "all"
    assert set(decision["action_classes"]) == {"write", "exec", "read"}


def test_approve_single_class():
    decision = approve_scope(_plan(), scope="read", approver="dev")
    assert decision["approved"] is True
    assert decision["action_classes"] == ["read"]
    assert decision["pending"] == ["write", "exec"]


def test_approve_unknown_scope_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown scope"):
        approve_scope(_plan(), scope="nuclear", approver="dev")


def test_rollback_instructions_present():
    text = render_review(_plan())
    assert "rollback" in text.lower() or "revert" in text.lower()
