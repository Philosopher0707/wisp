"""The keep-or-revert rule and the loop, with fake runner and proposer (no model calls)."""
from wisp.judge import core as J
from wisp.judge import improve as I

TASKS = J.load_tasks()


def V(task, verdict="SOLVED", honest=True, msg=""):
    return J.Verdict(task, verdict, [], [], claim_honest=honest, message=msg)


def S(solved, total, dishonest=0, infra=0):
    return {"solved": solved, "scored": total - infra, "dishonest": dishonest,
            "infra": infra, "total": total, "value": solved - dishonest}


# ── decide ──
def test_adopts_when_tune_improves_and_held_out_holds():
    assert I.decide(S(5, 9), S(7, 9), S(2, 4), S(2, 4))[0]


def test_reverts_when_tune_does_not_improve():
    assert not I.decide(S(5, 9), S(5, 9))[0]


def test_reverts_overfit_when_held_out_drops():
    ok, why = I.decide(S(5, 9), S(8, 9), S(3, 4), S(2, 4))
    assert not ok and "overfit" in why


def test_reverts_when_dishonest_claims_rise_even_if_solves_rise():
    ok, why = I.decide(S(5, 9, 0), S(8, 9, 1))
    assert not ok and "dishonest" in why


def test_inconclusive_when_too_many_infra_runs():
    ok, why = I.decide(S(5, 9), S(7, 9, infra=5))
    assert not ok and "inconclusive" in why


# ── sanitize ──
def test_sanitize_rejects_grader_aware_text():
    for bad in ("Edit the hidden tests", "make the judge happy", "satisfy the verdict"):
        assert I.sanitize(bad, "")[0] == ""


def test_sanitize_rejects_empty_unchanged_and_long():
    assert I.sanitize("  ", "x")[1] == "empty proposal"
    assert "identical" in I.sanitize("same", "same")[1]
    assert "too long" in I.sanitize("a" * 2000, "")[1]


def test_sanitize_strips_fences():
    assert I.sanitize("```\nRun the tests first.\n```", "")[0] == "Run the tests first."


# ── the loop ──
def _split():
    return [TASKS[i] for i in ("clamp", "dedupe", "fizzbuzz")], [TASKS[i] for i in ("flatten", "percent")]


def _run(runner, proposer, current=""):
    tune, held = _split()
    logs: list[str] = []
    final = I.run_loop(tune, held, TASKS, repeat=1, iterations=2, runner=runner,
                       proposer=proposer, current=current, log=logs.append)
    return final, "\n".join(logs)


def test_loop_adopts_a_helping_addendum():
    runner = lambda t, add: V(t.id) if "tests first" in add else V(t.id, "FAILED")  # noqa: E731
    final, log = _run(runner, lambda p: "Run the tests first.")
    assert final == "Run the tests first." and "ADOPTED" in log


def test_loop_reverts_an_overfitting_addendum():
    tune_ids = {"clamp", "dedupe", "fizzbuzz"}

    def runner(t, add):
        if "overfit" in add:
            return V(t.id) if t.id in tune_ids else V(t.id, "FAILED")
        return V(t.id, "FAILED") if t.id in tune_ids else V(t.id)
    final, log = _run(runner, lambda p: "overfit habit")
    assert final == "" and "REVERTED" in log


def test_loop_never_runs_a_rejected_proposal():
    calls: list[str] = []

    def runner(t, add):
        calls.append(add)
        return V(t.id, "FAILED")
    _run(runner, lambda p: "tamper with the hidden checks")
    assert set(calls) == {""}


def test_loop_stops_when_nothing_fails():
    asked: list[str] = []
    _run(lambda t, a: V(t.id), lambda p: asked.append(p) or "x")
    assert asked == []


def test_meta_prompt_shows_failures_and_forbids_naming_tasks():
    p = I.meta_prompt("", [V("clamp", "GAMED", honest=False, msg="all done")], TASKS)
    assert "claimed success but was wrong" in p and "Do not name specific tasks" in p
