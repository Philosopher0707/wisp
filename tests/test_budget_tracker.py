"""Unit tests for BudgetTracker — token accounting for subagent execution."""

import pytest

from wisp.multi_agent._budget_tracker import BudgetTracker


class TestBudgetTracker:
    def test_initial_state(self):
        bt = BudgetTracker()
        assert bt.get_consumed() == 0
        assert bt.get_remaining() is None
        assert bt.get_ratio() is None
        assert bt.check() is None

    def test_set_budget(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        assert bt.get_remaining() == 1000
        assert bt.get_ratio() == 1.0

    def test_record_tokens(self):
        bt = BudgetTracker()
        bt.record(100)
        assert bt.get_consumed() == 100

    def test_multiple_records(self):
        bt = BudgetTracker()
        bt.record(50)
        bt.record(30)
        bt.record(20)
        assert bt.get_consumed() == 100

    def test_get_remaining_with_budget(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(300)
        assert bt.get_remaining() == 700

    def test_get_remaining_min_zero(self):
        """Remaining should never go below zero."""
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.record(150)
        assert bt.get_remaining() == 0

    def test_get_ratio_tracks_consumption(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(500)
        assert bt.get_ratio() == 0.5

    def test_get_ratio_at_zero(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(1000)
        assert bt.get_ratio() == 0.0

    def test_get_ratio_min_zero(self):
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.record(200)
        assert bt.get_ratio() == 0.0

    def test_get_ratio_none_with_no_budget(self):
        bt = BudgetTracker()
        assert bt.get_ratio() is None

    def test_get_ratio_none_zero_budget(self):
        """Zero budget means no budget limit effectively."""
        bt = BudgetTracker()
        bt.set_budget(0)
        assert bt.get_ratio() is None

    def test_check_passes_with_budget_remaining(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(500)
        assert bt.check() is None

    def test_check_fails_when_exhausted(self):
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.record(100)
        error = bt.check()
        assert error is not None
        assert "exhausted" in error.lower()
        assert "100" in error

    def test_check_fails_when_over_exhausted(self):
        bt = BudgetTracker()
        bt.set_budget(50)
        bt.record(100)
        error = bt.check()
        assert error is not None
        assert "exhausted" in error.lower()

    def test_check_passes_without_budget(self):
        bt = BudgetTracker()
        bt.record(999999)
        assert bt.check() is None

    def test_remove_budget(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(500)
        bt.remove_budget()
        assert bt.get_remaining() is None
        assert bt.get_ratio() is None
        assert bt.check() is None

    def test_set_budget_none(self):
        bt = BudgetTracker()
        bt.set_budget(500)
        bt.set_budget(None)
        assert bt.get_remaining() is None

    def test_set_budget_zero(self):
        bt = BudgetTracker()
        bt.set_budget(None)
        assert bt.get_remaining() is None

    def test_large_token_counts(self):
        bt = BudgetTracker()
        bt.set_budget(10_000_000)
        bt.record(5_000_000)
        assert bt.get_remaining() == 5_000_000
        assert bt.get_ratio() == 0.5

    def test_no_record_after_remove_budget(self):
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.remove_budget()
        bt.record(500)
        assert bt.get_consumed() == 500
        assert bt.get_remaining() is None


class TestBudgetTrackerEdgeCases:
    def test_ratio_precision(self):
        """Ratio should be precise for small fractions."""
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(1)
        assert bt.get_ratio() == pytest.approx(0.999)

    def test_ratio_precision_near_zero(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(999)
        assert bt.get_ratio() == pytest.approx(0.001)

    def test_consecutive_budget_changes(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(300)
        bt.set_budget(500)
        assert bt.get_remaining() == 200  # 500 - 300
