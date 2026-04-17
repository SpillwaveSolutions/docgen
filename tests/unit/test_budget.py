"""Tests for CostAccumulator and BudgetExceededError.

Invariants under test:
- accrue() must raise BudgetExceededError BEFORE mutating state (so a caller that
  catches the error sees the prior totals intact).
- State survives save() / load_or_new() round-trip — this is what makes pipeline
  resume possible.
"""
from __future__ import annotations

import pytest

from designdoc.budget import BudgetExceededError, CostAccumulator, UsageRecord


def test_accrue_under_cap_does_not_raise():
    c = CostAccumulator(cap_usd=1.00)
    c.accrue(UsageRecord(input_tokens=1000, output_tokens=500, cost_usd=0.05, agent="x"))
    assert c.total_cost_usd == pytest.approx(0.05)
    assert c.total_input_tokens == 1000
    assert c.total_output_tokens == 500
    assert c.invocations == 1
    assert c.by_agent == {"x": pytest.approx(0.05)}


def test_accrue_over_cap_raises_and_preserves_state():
    c = CostAccumulator(cap_usd=0.10)
    c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.08, agent="x"))
    with pytest.raises(BudgetExceededError) as ex:
        c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.05, agent="x"))
    # projected total 0.13 should appear in the message
    assert "0.13" in str(ex.value)
    # state must not have been mutated by the failed accrue
    assert c.total_cost_usd == pytest.approx(0.08)
    assert c.invocations == 1


def test_accrue_aggregates_by_agent():
    c = CostAccumulator(cap_usd=1.00)
    c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.01, agent="doer"))
    c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.02, agent="checker"))
    c.accrue(UsageRecord(input_tokens=0, output_tokens=0, cost_usd=0.04, agent="doer"))
    assert c.by_agent["doer"] == pytest.approx(0.05)
    assert c.by_agent["checker"] == pytest.approx(0.02)


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / ".designdoc-budget.json"
    c = CostAccumulator(cap_usd=1.00, path=p)
    c.accrue(UsageRecord(input_tokens=1, output_tokens=1, cost_usd=0.02, agent="y"))
    c.save()

    c2 = CostAccumulator.load_or_new(cap_usd=1.00, path=p)
    assert c2.total_cost_usd == pytest.approx(0.02)
    assert c2.invocations == 1
    assert c2.by_agent == {"y": pytest.approx(0.02)}


def test_load_or_new_on_missing_file_returns_fresh(tmp_path):
    p = tmp_path / "does_not_exist.json"
    c = CostAccumulator.load_or_new(cap_usd=0.50, path=p)
    assert c.total_cost_usd == 0.0
    assert c.invocations == 0
    assert c.cap_usd == 0.50
