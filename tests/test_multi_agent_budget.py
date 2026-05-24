"""Unit tests for BudgetTracker."""

import pytest

from wisp.multi_agent.subagent_orchestrator import BudgetTracker


class TestBudgetTracker:

    def test_init_no_budget(self):
        bt = BudgetTracker()
        assert bt.get_consumed() == 0
        assert bt.get_remaining() is None
        assert bt.check() is None

    def test_set_budget(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        assert bt.get_remaining() == 1000
        assert bt.check() is None

    def test_record_consumption(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(300)
        assert bt.get_consumed() == 300
        assert bt.get_remaining() == 700
        assert bt.check() is None

    def test_budget_exhausted(self):
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.record(100)
        assert bt.get_consumed() == 100
        assert bt.get_remaining() == 0
        error = bt.check()
        assert error is not None
        assert "exhausted" in error

    def test_budget_over_consumed(self):
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.record(150)
        assert bt.get_consumed() == 150
        assert bt.get_remaining() == 0
        error = bt.check()
        assert error is not None

    def test_remove_budget(self):
        bt = BudgetTracker()
        bt.set_budget(500)
        bt.record(100)
        bt.remove_budget()
        assert bt.get_remaining() is None
        assert bt.check() is None

    def test_multiple_records(self):
        bt = BudgetTracker()
        bt.set_budget(1000)
        bt.record(100)
        bt.record(200)
        bt.record(300)
        assert bt.get_consumed() == 600
        assert bt.get_remaining() == 400

    def test_zero_budget(self):
        bt = BudgetTracker()
        bt.set_budget(0)
        assert bt.get_remaining() == 0
        assert bt.check() is not None

    def test_negative_record_ignored(self):
        """Negative consumption is still recorded (no guard)."""
        bt = BudgetTracker()
        bt.set_budget(100)
        bt.record(-50)
        assert bt.get_consumed() == -50
        assert bt.get_remaining() == 150
