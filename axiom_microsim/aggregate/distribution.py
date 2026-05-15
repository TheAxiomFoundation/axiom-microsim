"""Weighted decile-of-household-income distribution of a benefit output.

No PE / microdf dependency. Plain numpy weighted percentiles + groupby.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.ecps_loader import EcpsBatch, sum_person_to_household
from ..run.microsim import MicrosimResult


@dataclass
class DecileBin:
    decile: int                      # 1..10
    income_floor: float              # lower edge (annual)
    income_ceiling: float            # upper edge (annual)
    households_weighted: float
    mean_monthly_benefit: float      # mean across households in this decile
    share_receiving: float           # weighted share with benefit > 0


@dataclass
class DistributionAggregate:
    program: str
    state: str
    period_year: int
    bins: list[DecileBin]


def by_household_income_decile(
    result: MicrosimResult,
    batch: EcpsBatch,
    *,
    benefit_output: str = "snap_allotment",
    income_columns: tuple[str, ...] = (
        "employment_income_before_lsr",
        "self_employment_income_before_lsr",
        "taxable_pension_income",
        "taxable_interest_income",
        "qualified_dividend_income",
        "non_qualified_dividend_income",
        "rental_income",
        "alimony_income",
    ),
) -> DistributionAggregate:
    """Group households into 10 weighted income deciles, report mean benefit."""
    benefit = np.asarray(result.outputs[benefit_output], dtype=np.float64)
    weight = result.household_weight

    # Build household income from the same person-level columns the loader
    # carries, summed within household. Only columns present are summed —
    # the loader may have been called with a smaller set.
    hh_income = np.zeros(batch.n_households, dtype=np.float64)
    for col in income_columns:
        if col in batch.person_columns:
            hh_income += sum_person_to_household(
                batch.person_columns[col], batch.person_household_index, batch.n_households
            )

    cuts = _weighted_quantiles(hh_income, weight, np.linspace(0, 1, 11))
    cuts[0] = -np.inf
    cuts[-1] = np.inf

    bins: list[DecileBin] = []
    for i in range(10):
        lo, hi = cuts[i], cuts[i + 1]
        mask = (hh_income >= lo) & (hh_income < hi) if i < 9 else (hh_income >= lo)
        w = weight[mask]
        b = benefit[mask]
        total_w = float(w.sum())
        mean_b = float((b * w).sum() / total_w) if total_w > 0 else 0.0
        share = float(w[b > 0].sum() / total_w) if total_w > 0 else 0.0
        bins.append(
            DecileBin(
                decile=i + 1,
                income_floor=float(lo) if np.isfinite(lo) else 0.0,
                income_ceiling=float(hi) if np.isfinite(hi) else float(hh_income.max()),
                households_weighted=total_w,
                mean_monthly_benefit=mean_b,
                share_receiving=share,
            )
        )

    return DistributionAggregate(
        program=result.program,
        state=result.state,
        period_year=result.period_year,
        bins=bins,
    )


def _weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs: np.ndarray) -> np.ndarray:
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cw = np.cumsum(w)
    if cw[-1] == 0:
        return np.full_like(qs, np.nan, dtype=np.float64)
    cw_normalized = (cw - 0.5 * w) / cw[-1]
    return np.interp(qs, cw_normalized, v)
