"""Compare baseline and reform :class:`MicrosimResult`s — winners/losers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..run.microsim import MicrosimResult


@dataclass
class ReformImpact:
    program: str
    state: str
    period_year: int

    baseline_annual_cost: float
    reform_annual_cost: float
    delta_annual_cost: float          # reform - baseline

    households_winners: float         # weighted; benefit ↑ vs baseline
    households_losers: float          # weighted; benefit ↓
    households_unchanged: float
    households_total_weighted: float

    average_winner_gain_monthly: float
    average_loser_loss_monthly: float


# Per-household benefit changes smaller than this (in dollars/month) are
# treated as numerical noise from compile-time rounding. Tunable.
NOISE_FLOOR = 1.0


def compare(
    baseline: MicrosimResult,
    reform: MicrosimResult,
    *,
    benefit_output: str = "snap_allotment",
    months_per_year: int = 12,
) -> ReformImpact:
    if baseline.n_households != reform.n_households:
        raise ValueError("baseline and reform have different household counts — same batch required")

    base_b = np.asarray(baseline.outputs[benefit_output], dtype=np.float64)
    ref_b = np.asarray(reform.outputs[benefit_output], dtype=np.float64)
    weight = baseline.household_weight

    delta = ref_b - base_b
    winners = delta > NOISE_FLOOR
    losers = delta < -NOISE_FLOOR
    unchanged = ~(winners | losers)

    base_cost_monthly = float((base_b * weight).sum())
    ref_cost_monthly = float((ref_b * weight).sum())

    win_w = float(weight[winners].sum())
    lose_w = float(weight[losers].sum())

    avg_gain = float((delta[winners] * weight[winners]).sum() / win_w) if win_w > 0 else 0.0
    avg_loss = float((delta[losers] * weight[losers]).sum() / lose_w) if lose_w > 0 else 0.0

    return ReformImpact(
        program=baseline.program,
        state=baseline.state,
        period_year=baseline.period_year,
        baseline_annual_cost=base_cost_monthly * months_per_year,
        reform_annual_cost=ref_cost_monthly * months_per_year,
        delta_annual_cost=(ref_cost_monthly - base_cost_monthly) * months_per_year,
        households_winners=win_w,
        households_losers=lose_w,
        households_unchanged=float(weight[unchanged].sum()),
        households_total_weighted=float(weight.sum()),
        average_winner_gain_monthly=avg_gain,
        average_loser_loss_monthly=avg_loss,
    )
