# tests/test_contracts_policy.py
import pytest
from wisp.contracts.policy import PolicyDecisionEnvelope, CANCELLED_BY_USER
from wisp.core.contracts import ApprovalDecision, ToolRisk
from wisp.infra.policy_engine import PolicyDecision


def test_from_gate_decision():
    env = PolicyDecisionEnvelope.from_gate_decision(
        ApprovalDecision(allowed=False, reason="nope", risk=ToolRisk.EXEC))
    assert env.allowed is False and env.risk == "exec"


def test_from_engine_decision():
    env = PolicyDecisionEnvelope.from_engine_decision(
        PolicyDecision.deny("r1", "bad"))
    assert env.rule_name == "r1" and env.allowed is False


def test_cancel_is_denial_not_exception():
    env = PolicyDecisionEnvelope.cancelled("c1")
    assert env.allowed is False and env.reason == CANCELLED_BY_USER
    assert PolicyDecisionEnvelope.from_dict(env.to_dict()) == env


def test_from_dict_unknown_field_rejected():
    with pytest.raises(ValueError, match="unknown policy fields"):
        PolicyDecisionEnvelope.from_dict({"allowed": True, "bogus": 1})
