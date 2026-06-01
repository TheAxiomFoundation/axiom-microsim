"""Tests for the richer PolicyEngine comparison output contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from axiom_microsim import server


ROOT = Path(__file__).resolve().parents[1]
PE_SCRIPT = ROOT / "scripts" / "compute_pe_one.py"


def _load_pe_script():
    spec = importlib.util.spec_from_file_location("compute_pe_one", PE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pe_comparison_result_includes_deciles_winners_losers_and_poverty() -> None:
    pe = _load_pe_script()
    weights = np.ones(20, dtype=float)
    axis = np.arange(20, dtype=float) * 10_000
    baseline_values = np.arange(20, dtype=float) * 1_000
    reform_values = baseline_values * 0.9

    baseline = {
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        "values": baseline_values,
        "weights": weights,
        "axis": axis,
        "annual_factor": 1.0,
        "higher_is_better": False,
        "poverty": {
            "in_poverty": np.array([True, True, False, False]),
            "weights": np.ones(4, dtype=float),
        },
    }
    reform = {
        **baseline,
        "values": reform_values,
        "poverty": {
            "in_poverty": np.array([False, True, False, True]),
            "weights": np.ones(4, dtype=float),
        },
    }

    result = pe._comparison_result("federal-income-tax", "US", 2026, baseline, reform)

    assert result["pe_total"] == float(reform_values.sum())
    assert len(result["pe_baseline"]["decile_distribution"]) == 10
    assert len(result["pe_reform"]["decile_impact"]) == 10
    assert result["pe_reform"]["households_winners"] == 19.0
    assert result["pe_reform"]["households_losers"] == 0.0
    assert result["pe_reform"]["households_unchanged"] == 1.0
    assert result["pe_poverty"] == {
        "population_weighted": 4.0,
        "in_poverty_weighted": 2.0,
        "poverty_rate": 0.5,
    }
    assert result["pe_poverty_impact"] == {
        "baseline_poverty_rate": 0.5,
        "reform_poverty_rate": 0.5,
        "delta_poverty_rate": 0.0,
        "people_lifted_out_of_poverty": 1.0,
        "people_falling_into_poverty": 1.0,
    }


def test_compare_endpoint_accepts_rich_pe_outputs(monkeypatch, tmp_path) -> None:
    python_path = tmp_path / "python"
    python_path.write_text("")
    monkeypatch.setattr(server, "_PE_PYTHON", python_path)

    payload = {
        "pe_total": 90.0,
        "pe_n_units": 2,
        "pe_weighted_filers": 1.0,
        "pe_weighted_total": 2.0,
        "pe_avg_per_filer": 90.0,
        "pe_baseline": {
            "annual_cost": 100.0,
            "monthly_cost": 100.0 / 12,
            "households_with_benefit": 1.0,
            "average_monthly_benefit": 100.0,
            "decile_distribution": [_decile_bin(i) for i in range(1, 11)],
        },
        "pe_reform": {
            "baseline_annual_cost": 100.0,
            "reform_annual_cost": 90.0,
            "delta_annual_cost": -10.0,
            "households_winners": 1.0,
            "households_losers": 0.0,
            "households_unchanged": 1.0,
            "households_total_weighted": 2.0,
            "average_winner_gain_monthly": 10.0,
            "average_loser_loss_monthly": 0.0,
            "decile_impact": [_decile_impact_bin(i) for i in range(1, 11)],
        },
        "pe_poverty": {
            "population_weighted": 2.0,
            "in_poverty_weighted": 1.0,
            "poverty_rate": 0.5,
        },
        "pe_poverty_impact": {
            "baseline_poverty_rate": 0.5,
            "reform_poverty_rate": 0.25,
            "delta_poverty_rate": -0.25,
            "people_lifted_out_of_poverty": 0.5,
            "people_falling_into_poverty": 0.0,
        },
    }

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(server._subprocess, "run", fake_run)

    response = TestClient(server.app).post(
        "/compare",
        json={
            "program": "federal-income-tax",
            "state": "US",
            "year": 2026,
            "overrides": [{"path": "gov.irs.income.bracket.rates.1", "value": 0.095}],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pe_total"] == 90.0
    assert len(data["pe_baseline"]["decile_distribution"]) == 10
    assert len(data["pe_reform"]["decile_impact"]) == 10
    assert data["pe_poverty"]["poverty_rate"] == 0.5
    assert data["pe_poverty_impact"]["delta_poverty_rate"] == -0.25


def _decile_bin(decile: int) -> dict:
    return {
        "decile": decile,
        "income_floor": float(decile - 1),
        "income_ceiling": float(decile),
        "households_weighted": 1.0,
        "mean_monthly_benefit": float(decile),
        "share_receiving": 0.5,
    }


def _decile_impact_bin(decile: int) -> dict:
    return {
        "decile": decile,
        "income_floor": float(decile - 1),
        "income_ceiling": float(decile),
        "households_weighted": 1.0,
        "mean_delta": -1.0,
        "share_winners": 0.0,
        "share_losers": 0.5,
    }
