"""FastAPI service exposing ``POST /microsim``.

Same handler runs locally under ``uvicorn`` and inside Modal — see
``modal_app.py``. The Next.js frontend reads ``AXIOM_MICROSIM_URL`` and
posts here.

Two programs supported:
  - ``co-snap``: Colorado SNAP (household-rooted, monthly).
  - ``federal-income-tax``: §1(j) ordinary brackets (TaxUnit-rooted, annual).
"""

from __future__ import annotations

import json
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .aggregate.cost import aggregate as aggregate_cost
from .aggregate.distribution import by_household_income_decile
from .aggregate.reform import compare as compare_reform
from .data.ecps_loader import (
    load_state,
    load_state_tax_units,
    sum_person_to_tax_unit,
)
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


class DecileImpactBin(BaseModel):
    decile: int                       # 1..10
    income_floor: float                # AGI / gross-income lower edge
    income_ceiling: float              # upper edge
    households_weighted: float
    mean_delta: float                  # mean per-unit change vs baseline
    share_winners: float               # weighted share with delta > 0
    share_losers: float                # weighted share with delta < 0


class ReformOut(BaseModel):
    baseline_annual_cost: float
    reform_annual_cost: float
    delta_annual_cost: float
    households_winners: float
    households_losers: float
    households_unchanged: float
    households_total_weighted: float
    average_winner_gain_monthly: float
    average_loser_loss_monthly: float
    decile_impact: list[DecileImpactBin] = Field(default_factory=list)


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


# --- /ecps-stats ------------------------------------------------------------
# Verifiable mapping: for a given program/state, return the weighted
# aggregates of each ECPS column we read. Lets the methodology page show
# "we read $X of qualified dividends and project Y per tax unit" so the
# slot mapping is checkable.

class EcpsColumnStat(BaseModel):
    name: str
    level: str                  # "person" | "household"
    weighted_total: float       # weight × value summed
    weighted_mean: float        # weighted_total / weighted_units
    nonzero_share: float        # weighted share of units with value > 0
    sample_size: int


class EcpsStatsResponse(BaseModel):
    program: str
    state: str
    n_persons_sample: int
    n_units_sample: int
    units_label: str            # "tax units" | "households"
    weighted_units: float
    columns: list[EcpsColumnStat]


@app.get("/ecps-stats", response_model=EcpsStatsResponse)
def ecps_stats(
    program: Literal["co-snap", "federal-income-tax", "federal-ctc"],
    state: str = "US",
) -> EcpsStatsResponse:
    if program == "co-snap":
        from .data.ecps_loader import load_state as _load
        batch = _load(state)
        units_label = "households"
        n_units = batch.n_households
        weights = batch.household_weight
        person_idx = batch.person_household_index
    else:
        from .data.ecps_loader import load_state_tax_units as _load_tu
        batch = _load_tu(state)
        units_label = "tax units"
        n_units = batch.n_tax_units
        weights = batch.tax_unit_weight
        person_idx = batch.person_tax_unit_index

    # Per-person weight derived from the unit weight via membership.
    person_weight = weights[person_idx]

    cols: list[EcpsColumnStat] = []
    for name, arr in batch.person_columns.items():
        # Skip categorical / boolean fields with no $ semantics.
        if arr.dtype == bool:
            continue
        if name in {"age"}:
            continue
        v = arr.astype(np.float64)
        weighted_total = float((v * person_weight).sum())
        weighted_mean = (
            weighted_total / float(weights.sum()) if weights.sum() else 0.0
        )
        nonzero_share = (
            float(person_weight[v > 0].sum() / person_weight.sum())
            if person_weight.sum() else 0.0
        )
        cols.append(EcpsColumnStat(
            name=name,
            level="person",
            weighted_total=weighted_total,
            weighted_mean=weighted_mean,
            nonzero_share=nonzero_share,
            sample_size=int(arr.size),
        ))
    # Sort by weighted_total descending so the biggest sources lead.
    cols.sort(key=lambda c: -c.weighted_total)

    return EcpsStatsResponse(
        program=program,
        state=state,
        n_persons_sample=batch.n_persons,
        n_units_sample=n_units,
        units_label=units_label,
        weighted_units=float(weights.sum()),
        columns=cols,
    )


# --- /compare ---------------------------------------------------------------
# Run PE on the same scope as Axiom and return its aggregate. PE lives in
# its own venv (~/policyengine.py/.venv) so we subprocess into it. Slow
# (~100s) — UI calls this only when the user explicitly clicks.

import os
import subprocess as _subprocess
from pathlib import Path as _Path


_PE_PYTHON = _Path(
    os.environ.get("AXIOM_PE_PYTHON", str(_Path.home() / "policyengine.py" / ".venv" / "bin" / "python"))
)
_PE_SCRIPT = _Path(__file__).resolve().parent.parent / "scripts" / "compute_pe_one.py"


class PeOverrideIn(BaseModel):
    path: str
    value: float


class CompareRequest(BaseModel):
    program: Literal["co-snap", "federal-income-tax", "federal-ctc"]
    state: str = "US"
    year: int = 2026
    overrides: list[PeOverrideIn] = Field(default_factory=list)


class CompareResponse(BaseModel):
    program: str
    state: str
    year: int
    pe_total: float
    pe_n_units: int
    pe_weighted_filers: float
    pe_weighted_total: float
    pe_avg_per_filer: float
    elapsed_seconds: float


@app.post("/compare", response_model=CompareResponse)
def compare(req: CompareRequest) -> CompareResponse:
    if not _PE_PYTHON.exists():
        raise HTTPException(
            500,
            f"PE Python interpreter not found at {_PE_PYTHON}. "
            f"Set AXIOM_PE_PYTHON or install policyengine_us in a venv there.",
        )
    import time as _time
    t0 = _time.time()
    overrides_json = json.dumps([{"path": o.path, "value": o.value} for o in req.overrides])
    proc = _subprocess.run(
        [
            str(_PE_PYTHON), str(_PE_SCRIPT),
            "--program", req.program,
            "--state", req.state,
            "--year", str(req.year),
            "--overrides", overrides_json,
        ],
        capture_output=True, text=True, timeout=600,
    )
    elapsed = _time.time() - t0
    if proc.returncode != 0:
        raise HTTPException(500, f"PE compute failed: {proc.stderr.strip()[:1000]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"PE returned non-JSON: {proc.stdout[:300]}") from exc
    if "error" in data:
        raise HTTPException(500, f"PE compute error: {data['error']}")

    return CompareResponse(
        program=req.program,
        state=req.state,
        year=req.year,
        pe_total=data["pe_total"],
        pe_n_units=data["pe_n_units"],
        pe_weighted_filers=data["pe_weighted_filers"],
        pe_weighted_total=data["pe_weighted_total"],
        pe_avg_per_filer=data["pe_avg_per_filer"],
        elapsed_seconds=elapsed,
    )


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
        # Decile of household gross income → mean monthly delta per hh.
        from .data.ecps_loader import sum_person_to_household
        income_columns_hh = (
            "employment_income_before_lsr", "self_employment_income_before_lsr",
            "taxable_interest_income", "qualified_dividend_income",
            "non_qualified_dividend_income", "taxable_pension_income",
            "rental_income", "alimony_income",
        )
        agi_hh = np.zeros(batch.n_households, dtype=np.float64)
        for col in income_columns_hh:
            if col in batch.person_columns:
                agi_hh += sum_person_to_household(
                    batch.person_columns[col], batch.person_household_index, batch.n_households,
                )
        delta = (
            np.asarray(reform.outputs["snap_allotment"], dtype=np.float64)
            - np.asarray(baseline.outputs["snap_allotment"], dtype=np.float64)
        )
        decile_bins = _decile_impact(delta, batch.household_weight, agi_hh)
        response.reform = ReformOut(**impact.__dict__, decile_impact=decile_bins)

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

    deciles = _decile_by_axis(base_tax, weight, _tax_unit_agi(batch))

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

        # For tax: gainers receive a tax CUT — their delta is negative
        # but they're winners. Negate so mean_delta is the "received less
        # tax" magnitude when negative; the chart can color positive/
        # negative however it likes.
        decile_bins = _decile_impact(delta, weight, _tax_unit_agi(batch))
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
            decile_impact=decile_bins,
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

    deciles = _decile_by_axis(base_credit, weight, _tax_unit_agi(batch))

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

        decile_bins = _decile_impact(delta, weight, _tax_unit_agi(batch))
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
            decile_impact=decile_bins,
        )

    return response


AGI_INCOME_COLUMNS: tuple[str, ...] = (
    "employment_income_before_lsr",
    "self_employment_income_before_lsr",
    "taxable_interest_income",
    "qualified_dividend_income",
    "non_qualified_dividend_income",
    "taxable_pension_income",
    "rental_income",
    "alimony_income",
    "tip_income",
    "miscellaneous_income",
)


NOISE_FLOOR = 1.0   # per-unit changes smaller than this are treated as 0


def _decile_impact(
    delta: np.ndarray,
    weight: np.ndarray,
    axis: np.ndarray,
) -> list[DecileImpactBin]:
    """Group rows by weighted decile of `axis`; report mean delta + win/lose
    shares per decile. Used to render the decile-impact chart."""
    order = np.argsort(axis)
    a = axis[order]
    w = weight[order]
    cw = np.cumsum(w)
    if cw[-1] == 0:
        return []
    cuts_q = np.linspace(0, 1, 11)
    cuts = np.interp(cuts_q, (cw - 0.5 * w) / cw[-1], a)
    cuts[0] = -np.inf
    cuts[-1] = np.inf

    bins: list[DecileImpactBin] = []
    for i in range(10):
        lo, hi = cuts[i], cuts[i + 1]
        mask = (axis >= lo) & (axis < hi) if i < 9 else (axis >= lo)
        wm = weight[mask]
        dm = delta[mask]
        total_w = float(wm.sum())
        mean_d = float((dm * wm).sum() / total_w) if total_w else 0.0
        win_share = (
            float(wm[dm > NOISE_FLOOR].sum() / total_w) if total_w else 0.0
        )
        lose_share = (
            float(wm[dm < -NOISE_FLOOR].sum() / total_w) if total_w else 0.0
        )
        bins.append(DecileImpactBin(
            decile=i + 1,
            income_floor=float(lo) if np.isfinite(lo) else 0.0,
            income_ceiling=float(hi) if np.isfinite(hi) else float(axis.max()),
            households_weighted=total_w,
            mean_delta=mean_d,
            share_winners=win_share,
            share_losers=lose_share,
        ))
    return bins


def _tax_unit_agi(batch) -> np.ndarray:
    """Sum ECPS person-level income components to a per-tax-unit AGI proxy."""
    agi = np.zeros(batch.n_tax_units, dtype=np.float64)
    for col in AGI_INCOME_COLUMNS:
        if col in batch.person_columns:
            agi += sum_person_to_tax_unit(
                batch.person_columns[col], batch.person_tax_unit_index, batch.n_tax_units
            )
    return agi


def _decile_by_axis(
    values: np.ndarray,
    weights: np.ndarray,
    axis: np.ndarray,
) -> list[DecileBinOut]:
    """Group rows by weighted decile of `axis`, report mean `values` per bin.

    Use AGI as the axis for tax-unit programs and gross household income
    for SNAP — the standard distributional convention.
    """
    order = np.argsort(axis)
    a = axis[order]
    w = weights[order]
    cw = np.cumsum(w)
    if cw[-1] == 0:
        return []
    cuts_q = np.linspace(0, 1, 11)
    cuts = np.interp(cuts_q, (cw - 0.5 * w) / cw[-1], a)
    cuts[0] = -np.inf
    cuts[-1] = np.inf

    bins: list[DecileBinOut] = []
    for i in range(10):
        lo, hi = cuts[i], cuts[i + 1]
        mask = (axis >= lo) & (axis < hi) if i < 9 else (axis >= lo)
        wm = weights[mask]
        vm = values[mask]
        total_w = float(wm.sum())
        mean_v = float((vm * wm).sum() / total_w) if total_w else 0.0
        share = float(wm[vm > 0].sum() / total_w) if total_w else 0.0
        bins.append(
            DecileBinOut(
                decile=i + 1,
                income_floor=float(lo) if np.isfinite(lo) else 0.0,
                income_ceiling=float(hi) if np.isfinite(hi) else float(axis.max()),
                households_weighted=total_w,
                mean_monthly_benefit=mean_v,
                share_receiving=share,
            )
        )
    return bins


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
