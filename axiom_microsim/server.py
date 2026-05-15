"""FastAPI service exposing ``POST /microsim``.

Same handler runs locally under ``uvicorn`` and inside Modal — see
``modal_app.py``. The Next.js frontend reads ``AXIOM_MICROSIM_URL`` and
posts here.

Two programs supported:
  - ``co-snap``: Colorado SNAP (household-rooted, monthly).
  - ``federal-income-tax``: §1(j) ordinary brackets (TaxUnit-rooted, annual).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .aggregate.cost import aggregate as aggregate_cost
from .aggregate.distribution import by_household_income_decile
from .aggregate.reform import compare as compare_reform
from .data.ecps_loader import load_state, load_state_tax_units
from .run.microsim import (
    ParameterOverride,
    run_co_snap,
    run_federal_ctc,
    run_federal_income_tax,
)


# --- Request / response models ----------------------------------------------

class OverrideIn(BaseModel):
    repo: Literal["rules-us", "rules-us-co"]
    file_relative: str
    parameter: str
    patch_kind: Literal["scale_values", "set_values", "scale_formula", "set_formula"]
    multiplier: float | None = None
    values: dict[int, float] | None = None
    formula: str | None = None

    def to_runtime(self) -> ParameterOverride:
        return ParameterOverride(
            repo=self.repo,
            file_relative=self.file_relative,
            parameter=self.parameter,
            patch_kind=self.patch_kind,
            multiplier=self.multiplier,
            values=self.values,
            formula=self.formula,
        )


class MicrosimRequest(BaseModel):
    program: Literal["co-snap", "federal-income-tax", "federal-ctc"] = "co-snap"
    state: str = "CO"
    year: int = 2026
    overrides: list[OverrideIn] = Field(default_factory=list)


# --- Shared output shapes ---------------------------------------------------

class DecileBinOut(BaseModel):
    decile: int
    income_floor: float
    income_ceiling: float
    households_weighted: float
    mean_monthly_benefit: float        # for tax: mean annual tax (we reuse the field name)
    share_receiving: float             # for tax: share with positive liability


class BaselineOut(BaseModel):
    annual_cost: float                 # for tax: annual revenue
    monthly_cost: float                # for tax: annual revenue / 12
    households_with_benefit: float     # for tax: tax units with positive liability
    average_monthly_benefit: float     # for tax: average annual liability per filer
    decile_distribution: list[DecileBinOut]


class ReformOut(BaseModel):
    baseline_annual_cost: float
    reform_annual_cost: float
    delta_annual_cost: float
    households_winners: float          # SNAP: gain in benefit. Tax: pay LESS tax.
    households_losers: float           # SNAP: loss in benefit. Tax: pay MORE tax.
    households_unchanged: float
    households_total_weighted: float
    average_winner_gain_monthly: float
    average_loser_loss_monthly: float


class MicrosimResponse(BaseModel):
    program: str
    state: str
    period_year: int
    n_households_sampled: int          # for tax: tax units
    n_persons_sampled: int
    households_total_weighted: float
    baseline: BaselineOut
    reform: ReformOut | None = None


# --- App --------------------------------------------------------------------

app = FastAPI(title="axiom-microsim", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/microsim", response_model=MicrosimResponse)
def microsim(req: MicrosimRequest) -> MicrosimResponse:
    overrides = [o.to_runtime() for o in req.overrides]
    if req.program == "co-snap":
        return _run_co_snap(req, overrides)
    if req.program == "federal-income-tax":
        return _run_federal_income_tax(req, overrides)
    if req.program == "federal-ctc":
        return _run_federal_ctc(req, overrides)
    raise HTTPException(400, f"unknown program {req.program!r}")


# --- co-snap path -----------------------------------------------------------

def _run_co_snap(req: MicrosimRequest, overrides: list[ParameterOverride]) -> MicrosimResponse:
    try:
        batch = load_state(req.state)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    baseline = run_co_snap(batch, period_year=req.year)
    cost = aggregate_cost(baseline)
    dist = by_household_income_decile(baseline, batch)

    response = MicrosimResponse(
        program=baseline.program,
        state=baseline.state,
        period_year=baseline.period_year,
        n_households_sampled=baseline.n_households,
        n_persons_sampled=baseline.n_persons,
        households_total_weighted=cost.households_total_weighted,
        baseline=BaselineOut(
            annual_cost=cost.total_annual_cost,
            monthly_cost=cost.total_monthly_cost,
            households_with_benefit=cost.households_with_benefit,
            average_monthly_benefit=cost.average_monthly_benefit,
            decile_distribution=[DecileBinOut(**b.__dict__) for b in dist.bins],
        ),
    )

    if overrides:
        reform = run_co_snap(batch, period_year=req.year, overrides=overrides)
        impact = compare_reform(baseline, reform)
        response.reform = ReformOut(**impact.__dict__)

    return response


# --- federal-income-tax path ------------------------------------------------

# Headline output for §1(j). Annual; we don't divide by 12.
TAX_OUTPUT = "income_tax_main_rates"


def _run_federal_income_tax(
    req: MicrosimRequest, overrides: list[ParameterOverride]
) -> MicrosimResponse:
    try:
        batch = load_state_tax_units(req.state)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    baseline = run_federal_income_tax(batch, period_year=req.year)
    base_tax = np.asarray(baseline.outputs[TAX_OUTPUT], dtype=np.float64)
    weight = baseline.household_weight

    annual_revenue = float((base_tax * weight).sum())
    has_liability = base_tax > 0
    weighted_filers = float(weight[has_liability].sum())
    avg_per_filer = (
        float((base_tax[has_liability] * weight[has_liability]).sum() / weighted_filers)
        if weighted_filers > 0 else 0.0
    )

    deciles = _tax_deciles(base_tax, weight)

    response = MicrosimResponse(
        program=baseline.program,
        state=baseline.state,
        period_year=baseline.period_year,
        n_households_sampled=baseline.n_households,   # = n tax units
        n_persons_sampled=baseline.n_persons,
        households_total_weighted=float(weight.sum()),
        baseline=BaselineOut(
            annual_cost=annual_revenue,
            monthly_cost=annual_revenue / 12,
            households_with_benefit=weighted_filers,
            average_monthly_benefit=avg_per_filer,    # mean ANNUAL liability per filer
            decile_distribution=deciles,
        ),
    )

    if overrides:
        reform = run_federal_income_tax(batch, period_year=req.year, overrides=overrides)
        ref_tax = np.asarray(reform.outputs[TAX_OUTPUT], dtype=np.float64)
        ref_revenue = float((ref_tax * weight).sum())
        delta = ref_tax - base_tax

        # Winners under a TAX reform = pay LESS tax (delta < 0).
        # Losers = pay MORE tax (delta > 0). Same field semantics as SNAP if
        # you read "winners = better off."
        gainers = delta < -1.0
        loswers = delta > 1.0
        win_w = float(weight[gainers].sum())
        lose_w = float(weight[loswers].sum())
        avg_gain = float((-delta[gainers] * weight[gainers]).sum() / win_w) if win_w else 0.0
        avg_loss = float((delta[loswers] * weight[loswers]).sum() / lose_w) if lose_w else 0.0

        response.reform = ReformOut(
            baseline_annual_cost=annual_revenue,
            reform_annual_cost=ref_revenue,
            delta_annual_cost=ref_revenue - annual_revenue,
            households_winners=win_w,
            households_losers=lose_w,
            households_unchanged=float(weight.sum()) - win_w - lose_w,
            households_total_weighted=float(weight.sum()),
            average_winner_gain_monthly=avg_gain,
            average_loser_loss_monthly=avg_loss,
        )

    return response


CTC_OUTPUT = "ctc_maximum_before_phase_out_under_subsection_h"


def _run_federal_ctc(req: MicrosimRequest, overrides: list[ParameterOverride]) -> MicrosimResponse:
    try:
        batch = load_state_tax_units(req.state)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    baseline = run_federal_ctc(batch, period_year=req.year)
    base_credit = np.asarray(baseline.outputs[CTC_OUTPUT], dtype=np.float64)
    weight = baseline.household_weight

    annual_cost = float((base_credit * weight).sum())
    has_credit = base_credit > 0
    weighted_recipients = float(weight[has_credit].sum())
    avg = (
        float((base_credit[has_credit] * weight[has_credit]).sum() / weighted_recipients)
        if weighted_recipients > 0 else 0.0
    )

    deciles = _tax_deciles(base_credit, weight)

    response = MicrosimResponse(
        program=baseline.program,
        state=baseline.state,
        period_year=baseline.period_year,
        n_households_sampled=baseline.n_households,
        n_persons_sampled=baseline.n_persons,
        households_total_weighted=float(weight.sum()),
        baseline=BaselineOut(
            annual_cost=annual_cost,
            monthly_cost=annual_cost / 12,
            households_with_benefit=weighted_recipients,
            average_monthly_benefit=avg,
            decile_distribution=deciles,
        ),
    )

    if overrides:
        reform = run_federal_ctc(batch, period_year=req.year, overrides=overrides)
        ref_credit = np.asarray(reform.outputs[CTC_OUTPUT], dtype=np.float64)
        ref_cost = float((ref_credit * weight).sum())
        delta = ref_credit - base_credit

        # CTC reform semantics: gainers receive MORE credit (delta > 0),
        # losers receive LESS (delta < 0). Same as SNAP framing.
        gainers = delta > 1.0
        losers = delta < -1.0
        win_w = float(weight[gainers].sum())
        lose_w = float(weight[losers].sum())
        avg_gain = float((delta[gainers] * weight[gainers]).sum() / win_w) if win_w else 0.0
        avg_loss = float((-delta[losers] * weight[losers]).sum() / lose_w) if lose_w else 0.0

        response.reform = ReformOut(
            baseline_annual_cost=annual_cost,
            reform_annual_cost=ref_cost,
            delta_annual_cost=ref_cost - annual_cost,
            households_winners=win_w,
            households_losers=lose_w,
            households_unchanged=float(weight.sum()) - win_w - lose_w,
            households_total_weighted=float(weight.sum()),
            average_winner_gain_monthly=avg_gain,
            average_loser_loss_monthly=avg_loss,
        )

    return response


def _tax_deciles(tax: np.ndarray, weight: np.ndarray) -> list[DecileBinOut]:
    """Group tax units by the size of their tax liability into 10 weighted
    deciles. (For SNAP we group by income; for tax it's most informative to
    group by liability so D10 = highest payers.)"""
    order = np.argsort(tax)
    t = tax[order]
    w = weight[order]
    cw = np.cumsum(w)
    if cw[-1] == 0:
        return []
    cuts_q = np.linspace(0, 1, 11)
    cuts = np.interp(cuts_q, (cw - 0.5 * w) / cw[-1], t)
    cuts[0] = -np.inf
    cuts[-1] = np.inf

    bins: list[DecileBinOut] = []
    for i in range(10):
        lo, hi = cuts[i], cuts[i + 1]
        mask = (tax >= lo) & (tax < hi) if i < 9 else (tax >= lo)
        wm = weight[mask]
        tm = tax[mask]
        total_w = float(wm.sum())
        mean_t = float((tm * wm).sum() / total_w) if total_w else 0.0
        share = float(wm[tm > 0].sum() / total_w) if total_w else 0.0
        bins.append(
            DecileBinOut(
                decile=i + 1,
                income_floor=float(lo) if np.isfinite(lo) else 0.0,
                income_ceiling=float(hi) if np.isfinite(hi) else float(tax.max()),
                households_weighted=total_w,
                mean_monthly_benefit=mean_t,        # repurposed: mean annual liability
                share_receiving=share,
            )
        )
    return bins
