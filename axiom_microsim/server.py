"""FastAPI service exposing ``POST /microsim``.

Same handler runs locally under ``uvicorn`` and inside Modal — see
``modal_app.py``. The Next.js frontend reads ``AXIOM_MICROSIM_URL`` and
posts here.

Wire shape::

    POST /microsim
    {
      "program": "co-snap",
      "state": "CO",
      "year": 2026,
      "overrides": [                   // optional
        {
          "repo": "rules-us-co",
          "file_relative": "policies/cdhs/snap/.../maximum-allotments.yaml",
          "parameter": "snap_maximum_allotment_two_person",
          "patch_kind": "scale_values",
          "multiplier": 1.05
        }
      ]
    }

    200 OK
    {
      "program": "co-snap",
      "state": "CO",
      "period_year": 2026,
      "baseline": {...},
      "reform": {...}                  // present iff overrides was non-empty
    }
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .aggregate.cost import aggregate as aggregate_cost
from .aggregate.distribution import by_household_income_decile
from .aggregate.reform import compare as compare_reform
from .data.ecps_loader import load_state
from .run.microsim import ParameterOverride, run_co_snap


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
    program: Literal["co-snap"] = "co-snap"
    state: str = "CO"
    year: int = 2026
    overrides: list[OverrideIn] = Field(default_factory=list)


class DecileBinOut(BaseModel):
    decile: int
    income_floor: float
    income_ceiling: float
    households_weighted: float
    mean_monthly_benefit: float
    share_receiving: float


class BaselineOut(BaseModel):
    annual_cost: float
    monthly_cost: float
    households_with_benefit: float
    average_monthly_benefit: float
    decile_distribution: list[DecileBinOut]


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


class MicrosimResponse(BaseModel):
    program: str
    state: str
    period_year: int
    n_households_sampled: int
    n_persons_sampled: int
    households_total_weighted: float
    baseline: BaselineOut
    reform: ReformOut | None = None


# --- App --------------------------------------------------------------------

app = FastAPI(title="axiom-microsim", version="0.1.0")

# Local dev convenience: allow the Next.js dev server to call us. In Modal
# the Vercel app and the Modal endpoint are cross-origin too, so we keep
# this permissive — the service has no auth and exposes only public ECPS
# aggregates, so nothing here is sensitive.
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
    if req.program != "co-snap":
        raise HTTPException(400, f"v1 only supports program=co-snap (got {req.program!r})")

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

    if req.overrides:
        reform = run_co_snap(
            batch, period_year=req.year, overrides=[o.to_runtime() for o in req.overrides]
        )
        impact = compare_reform(baseline, reform)
        response.reform = ReformOut(**impact.__dict__)

    return response
