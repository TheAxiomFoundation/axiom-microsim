"""App-v2 output contract tests for the microsim API.

These tests keep the API response shape aligned with ``web/src/lib/types.ts``.
They use a deterministic federal-income-tax fixture so the test covers the
reform aggregation path without depending on the native dense engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from fastapi.testclient import TestClient

from axiom_microsim import server
from axiom_microsim.run.microsim import MicrosimResult


FEDERAL_RATE_CUT_OVERRIDE = {
    "repo": "rules-us",
    "file_relative": "policies/irs/rev-proc-2025-32/income-tax-brackets.yaml",
    "parameter": "income_tax_bracket_rates",
    "patch_kind": "scale_values",
    "multiplier": 0.95,
}


@dataclass
class FakeTaxUnitBatch:
    state: str = "US"
    n_tax_units: int = 20
    n_persons: int = 20

    def __post_init__(self) -> None:
        self.tax_unit_weight = np.ones(self.n_tax_units, dtype=np.float64)
        self.person_tax_unit_index = np.arange(self.n_tax_units, dtype=np.int64)
        self.person_columns = {
            "employment_income_before_lsr": np.linspace(
                5_000, 195_000, self.n_tax_units, dtype=np.float64
            )
        }


def test_app_v2_federal_reform_returns_every_output_field(monkeypatch) -> None:
    """Same-population federal reform with the same override shape as app-v2."""
    batch = FakeTaxUnitBatch()
    baseline_tax = np.linspace(0, 19_000, batch.n_tax_units, dtype=np.float64)
    calls = []

    def fake_load_state_tax_units(state: str) -> FakeTaxUnitBatch:
        assert state == "US"
        return batch

    def fake_run_federal_income_tax(
        loaded_batch: FakeTaxUnitBatch,
        *,
        period_year: int,
        overrides=None,
    ) -> MicrosimResult:
        assert loaded_batch is batch
        assert period_year == 2026
        calls.append(overrides)
        tax = baseline_tax if not overrides else baseline_tax * 0.95
        return MicrosimResult(
            program="federal-income-tax",
            state=batch.state,
            period_year=period_year,
            n_households=batch.n_tax_units,
            n_persons=batch.n_persons,
            household_weight=batch.tax_unit_weight,
            outputs={server.TAX_OUTPUT: tax},
        )

    monkeypatch.setattr(server, "load_state_tax_units", fake_load_state_tax_units)
    monkeypatch.setattr(server, "run_federal_income_tax", fake_run_federal_income_tax)

    response = TestClient(server.app).post(
        "/microsim",
        json={
            "program": "federal-income-tax",
            "state": "US",
            "year": 2026,
            "overrides": [FEDERAL_RATE_CUT_OVERRIDE],
        },
    )

    assert response.status_code == 200
    data = response.json()
    _assert_app_v2_response_shape(data, expect_reform=True)

    assert len(calls) == 2
    assert calls[0] is None
    assert len(calls[1]) == 1
    override = calls[1][0]
    assert override.repo == FEDERAL_RATE_CUT_OVERRIDE["repo"]
    assert override.file_relative == FEDERAL_RATE_CUT_OVERRIDE["file_relative"]
    assert override.parameter == FEDERAL_RATE_CUT_OVERRIDE["parameter"]
    assert override.patch_kind == FEDERAL_RATE_CUT_OVERRIDE["patch_kind"]
    assert override.multiplier == FEDERAL_RATE_CUT_OVERRIDE["multiplier"]

    expected_baseline = float(baseline_tax.sum())
    expected_reform = float((baseline_tax * 0.95).sum())
    reform = data["reform"]
    assert data["baseline"]["annual_cost"] == expected_baseline
    assert reform["baseline_annual_cost"] == expected_baseline
    assert reform["reform_annual_cost"] == expected_reform
    assert reform["delta_annual_cost"] == expected_reform - expected_baseline
    assert reform["households_winners"] == 19.0
    assert reform["households_losers"] == 0.0
    assert reform["households_unchanged"] == 1.0


def _assert_app_v2_response_shape(data: dict, *, expect_reform: bool) -> None:
    assert set(data) == {
        "program",
        "state",
        "period_year",
        "n_households_sampled",
        "n_persons_sampled",
        "households_total_weighted",
        "baseline",
        "reform",
    }
    assert data["program"] == "federal-income-tax"
    assert data["state"] == "US"
    assert data["period_year"] == 2026
    assert data["n_households_sampled"] == 20
    assert data["n_persons_sampled"] == 20
    assert data["households_total_weighted"] == 20.0

    baseline = data["baseline"]
    assert set(baseline) == {
        "annual_cost",
        "monthly_cost",
        "households_with_benefit",
        "average_monthly_benefit",
        "decile_distribution",
    }
    for key in (
        "annual_cost",
        "monthly_cost",
        "households_with_benefit",
        "average_monthly_benefit",
    ):
        assert _finite_number(baseline[key])
    _assert_decile_bins(baseline["decile_distribution"], "mean_monthly_benefit")

    if not expect_reform:
        assert data["reform"] is None
        return

    reform = data["reform"]
    assert set(reform) == {
        "baseline_annual_cost",
        "reform_annual_cost",
        "delta_annual_cost",
        "households_winners",
        "households_losers",
        "households_unchanged",
        "households_total_weighted",
        "average_winner_gain_monthly",
        "average_loser_loss_monthly",
        "decile_impact",
    }
    for key in (
        "baseline_annual_cost",
        "reform_annual_cost",
        "delta_annual_cost",
        "households_winners",
        "households_losers",
        "households_unchanged",
        "households_total_weighted",
        "average_winner_gain_monthly",
        "average_loser_loss_monthly",
    ):
        assert _finite_number(reform[key])
    _assert_decile_impact_bins(reform["decile_impact"])


def _assert_decile_bins(bins: list[dict], value_key: str) -> None:
    assert len(bins) == 10
    for i, row in enumerate(bins, start=1):
        assert set(row) == {
            "decile",
            "income_floor",
            "income_ceiling",
            "households_weighted",
            value_key,
            "share_receiving",
        }
        assert row["decile"] == i
        _assert_bin_numbers(row, value_key)
        assert 0.0 <= row["share_receiving"] <= 1.0


def _assert_decile_impact_bins(bins: list[dict]) -> None:
    assert len(bins) == 10
    for i, row in enumerate(bins, start=1):
        assert set(row) == {
            "decile",
            "income_floor",
            "income_ceiling",
            "households_weighted",
            "mean_delta",
            "share_winners",
            "share_losers",
        }
        assert row["decile"] == i
        _assert_bin_numbers(row, "mean_delta")
        assert 0.0 <= row["share_winners"] <= 1.0
        assert 0.0 <= row["share_losers"] <= 1.0


def _assert_bin_numbers(row: dict, value_key: str) -> None:
    for key in ("income_floor", "income_ceiling", "households_weighted", value_key):
        assert _finite_number(row[key])


def _finite_number(value: object) -> bool:
    return isinstance(value, int | float) and math.isfinite(float(value))
