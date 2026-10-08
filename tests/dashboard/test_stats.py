from __future__ import annotations

import math

import pytest

from wisp.dashboard import stats as S


def row(task="t", verdict="SOLVED", final=True, seconds=10.0, honest=True, model="m", label="default"):
    return {"task": task, "verdict": verdict, "final": final, "seconds": seconds, "claim_honest": honest, "model": model, "label": label}


class TestWilson:
    def test_known_values(self):
        lo, hi = S.wilson(8, 10)
        assert lo == pytest.approx(0.490, abs=0.005) and hi == pytest.approx(0.943, abs=0.005)

    def test_no_trials_has_no_estimate(self):
        assert S.wilson(0, 0) is None

    @pytest.mark.parametrize("k,n", [(0, 5), (5, 5), (1, 1), (0, 1)])
    def test_the_edges_stay_inside_zero_and_one_and_are_not_degenerate(self, k, n):
        lo, hi = S.wilson(k, n)
        assert 0.0 <= lo <= k / n <= hi <= 1.0 and hi - lo > 0

    def test_more_data_narrows_the_interval(self):
        a, b = S.wilson(5, 10), S.wilson(50, 100)
        assert (b[1] - b[0]) < (a[1] - a[0])


class TestDifference:
    def test_equal_rates_straddle_zero(self):
        d, lo, hi = S.newcombe_difference(5, 10, 5, 10)
        assert d == 0 and lo < 0 < hi

    def test_a_large_clear_difference_excludes_zero(self):
        d, lo, hi = S.newcombe_difference(90, 100, 40, 100)
        assert d == pytest.approx(0.5) and lo > 0

    def test_no_trials_on_either_side(self):
        assert S.newcombe_difference(0, 0, 3, 5) is None and S.newcombe_difference(3, 5, 0, 0) is None


class TestSummarize:
    def test_infra_and_broken_are_not_failures(self):
        s = S.summarize([row(verdict="SOLVED"), row(verdict="FAILED"), row(verdict="INFRA"), row(verdict="BROKEN")])
        assert (s["scored"], s["solved"], s["outcomes"]) == (2, 1, 4) and s["pass_rate"] == 0.5
        assert s["counts"]["INFRA"] == 1 and s["counts"]["BROKEN"] == 1

    def test_only_final_rows_are_outcomes_but_every_attempt_counts_for_the_infra_rate(self):
        rows = [row(verdict="INFRA", final=False), row(verdict="INFRA", final=False), row(verdict="SOLVED")]
        s = S.summarize(rows)
        assert s["outcomes"] == 1 and s["attempts"] == 3 and s["infra_attempts"] == 2 and s["infra_rate"] == pytest.approx(2 / 3)
        assert s["pass_rate"] == 1.0

    def test_dishonest_claims_are_counted_over_outcomes(self):
        s = S.summarize([row(honest=False), row(honest=True), row(honest=None), row(honest=False, final=False)])
        assert s["dishonest"] == 1

    def test_timing_percentiles_use_scored_runs_only(self):
        rows = [row(seconds=s) for s in (1, 2, 3, 4, 100)] + [row(verdict="INFRA", seconds=9999)]
        s = S.summarize(rows)
        assert s["median_s"] == 3 and s["p90_s"] == 100

    def test_empty(self):
        s = S.summarize([])
        assert s["pass_rate"] is None and s["ci_low"] is None and s["median_s"] is None and s["infra_rate"] is None

    def test_a_perfect_small_sample_still_has_a_wide_interval(self):
        s = S.summarize([row() for _ in range(5)])
        assert s["pass_rate"] == 1.0 and s["ci_low"] < 0.6

    def test_unknown_verdicts_do_not_crash_and_count_as_scored_failures(self):
        s = S.summarize([row(verdict="WEIRD"), row()])
        assert s["scored"] == 2 and s["solved"] == 1


class TestMatrixAndLabels:
    def test_matrix_cells(self):
        rows = [row("a", "SOLVED", label="x"), row("a", "FAILED", label="x"), row("a", "INFRA", label="y"), row("b", "SOLVED", label="y"), row("b", "SOLVED", final=False, label="y")]
        m = S.task_matrix(rows, lambda r: r["label"])
        assert m["a"]["x"] == {"solved": 1, "scored": 2, "infra": 0, "outcomes": 2}
        assert m["a"]["y"]["infra"] == 1 and m["a"]["y"]["scored"] == 0 and m["b"]["y"]["outcomes"] == 1

    def test_group_by(self):
        g = S.group_by([row(model="a"), row(model="b"), row(model="a")], lambda r: r["model"])
        assert {k: len(v) for k, v in g.items()} == {"a": 2, "b": 1}

    def test_trust_sentences(self):
        assert "no scored" in S.verdict_label(S.summarize([]))
        assert "only 3 scored" in S.verdict_label(S.summarize([row() for _ in range(3)]))
        biased = S.summarize([row(verdict="INFRA", final=False)] * 8 + [row() for _ in range(12)])
        assert "infrastructure" in S.verdict_label(biased)
        assert S.verdict_label(S.summarize([row() for _ in range(12)])) == "12 scored runs"

    def test_percentile_is_monotone(self):
        v = [float(i) for i in range(1, 51)]
        assert S._percentile(v, 0.5) <= S._percentile(v, 0.9) <= S._percentile(v, 1.0) == 50.0
        assert S._percentile([], 0.5) is None and not math.isnan(S._percentile([1.0], 0.9))


class TestClaims:
    def test_overclaims_and_underclaims_are_different_things(self):
        rows = [
            {"task": "a", "verdict": "FAILED", "claim": True, "claim_honest": False, "final": True},   # claimed success, not solved: the dangerous one
            {"task": "b", "verdict": "SOLVED", "claim": False, "claim_honest": False, "final": True},  # said not-ok, but solved: cautious
            {"task": "c", "verdict": "SOLVED", "claim": True, "claim_honest": True, "final": True},
            {"task": "d", "verdict": "INFRA", "claim": True, "claim_honest": False, "final": True},    # unscored: not counted
            {"task": "e", "verdict": "FAILED", "claim": True, "claim_honest": False, "final": False},  # not an outcome
        ]
        s = S.summarize(rows)
        assert (s["overclaims"], s["underclaims"]) == (1, 1) and s["dishonest"] == 3
