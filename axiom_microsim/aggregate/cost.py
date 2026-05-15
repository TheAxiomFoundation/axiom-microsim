"""Aggregate cost / caseload from a :class:`MicrosimResult`."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..run.microsim import MicrosimResult


@dataclass
class CostAggregate:
    program: str
    state: str
    period_year: int

    # Annualised — raw output is monthly allotment.
    total_annual_cost: float
    total_monthly_cost: float

    households_with_benefit: float       # weighted
    average_monthly_benefit: float       # per receiving household
    households_total_weighted: float


# CO SNAP's headline benefit is monthly. Annualise for top-line cost so the
# number lines up with USDA's reported state SNAP outlays.
MONTHS_PER_YEAR = 12


def aggregate(result: MicrosimResult, *, benefit_output: str = "snap_allotment") -> CostAggregate:
    benefit = _output(result, benefit_output)
    weight = result.household_weight

    monthly_total = float((benefit * weight).sum())
    receiving_mask = benefit > 0
    receiving_weight = float(weight[receiving_mask].sum())

    avg = float((benefit[receiving_mask] * weight[receiving_mask]).sum() / receiving_weight) \
        if receiving_weight > 0 else 0.0

    return CostAggregate(
        program=result.program,
        state=result.state,
        period_year=result.period_year,
        total_annual_cost=monthly_total * MONTHS_PER_YEAR,
        total_monthly_cost=monthly_total,
        households_with_benefit=receiving_weight,
        average_monthly_benefit=avg,
        households_total_weighted=float(weight.sum()),
    )


def _output(result: MicrosimResult, name: str) -> np.ndarray:
    if name not in result.outputs:
        raise KeyError(f"output {name!r} not in result; have {list(result.outputs)}")
    return np.asarray(result.outputs[name], dtype=np.float64)
