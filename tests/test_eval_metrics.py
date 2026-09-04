# tests/test_eval_metrics.py — scenario manifests + safety metrics (M5 T4).
from wisp.eval.metrics import EvalReport, evaluate_runs
from wisp.eval.scenarios import BUILTIN_SCENARIOS, EvalScenario


def test_builtin_scenarios_include_adversarial():
    kinds = {s.kind for s in BUILTIN_SCENARIOS}
    assert {"task", "prompt-injection", "approval-bypass"} <= kinds
    assert all(isinstance(s, EvalScenario) for s in BUILTIN_SCENARIOS)


def test_success_rate():
    runs = [{"success": True}, {"success": True}, {"success": False}]
    rep = evaluate_runs(runs)
    assert rep.success_rate == 2 / 3
    assert isinstance(rep, EvalReport)


def test_safety_blocks_bypass_attempts():
    runs = [
        {"success": True, "bypass_attempts": 0, "bypass_blocked": 0},
        {"success": False, "bypass_attempts": 2, "bypass_blocked": 2},
        {"success": True, "bypass_attempts": 1, "bypass_blocked": 0},
    ]
    rep = evaluate_runs(runs)
    assert rep.bypass_attempts == 3
    assert rep.bypass_blocked == 2
    assert rep.safety_rate == 2 / 3


def test_latency_percentiles():
    runs = [{"success": True, "latency_ms": float(v)} for v in (100, 200, 300, 400)]
    rep = evaluate_runs(runs)
    assert rep.latency_p50 == 200.0
    assert rep.latency_p95 == 400.0


def test_cost_and_recovery_and_interruption():
    runs = [
        {"success": True, "prompt_tokens": 100, "completion_tokens": 50,
         "recovered": True, "cancel_honored": True},
        {"success": True, "prompt_tokens": 200, "completion_tokens": 100,
         "recovered": False, "cancel_honored": True},
    ]
    rep = evaluate_runs(runs)
    assert rep.total_tokens == 450
    assert rep.recovery_rate == 0.5
    assert rep.interruption_rate == 1.0


def test_empty_runs_safe():
    rep = evaluate_runs([])
    assert rep.success_rate == 0.0 and rep.total_tokens == 0
