"""Compute PolicyEngine baselines that match Axiom-microsim's outputs exactly.

Uses ``policyengine_us.Microsimulation`` directly — the same API
``axiom-programs`` uses — because it gives us per-variable access via
``sim.calculate(name, period)``. The higher-level ``policyengine``
package's ``Simulation.output_dataset.data`` only exposes a curated
shortlist that does not include ``income_tax_main_rates``.

Run with the policyengine.py venv that has ``policyengine_us`` installed::

    /Users/pavelmakarchuk/policyengine.py/.venv/bin/python \\
        /Users/pavelmakarchuk/axiom-microsim/scripts/refresh_pe_comparison.py

Output: ``web/public/comparison.json`` for the methodology page.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "public" / "comparison.json"

# Latest Axiom baselines at the same scope. Updated when the runner /
# projection logic changes.
AXIOM_FED_INCOME_TAX_US_2026 = {
    "axiom_total_revenue": 1_606_600_000_000.0,
    "axiom_n_tax_units": 30_114,
    "axiom_weighted_filers": 115_000_000,
    "axiom_avg_per_filer": 13_973,
}

AXIOM_CO_SNAP_2026 = {
    "axiom_total_annual_cost": 3_983_033_714.0,
    "axiom_n_households": 413,
    "axiom_weighted_recipients": 735_085,
    "axiom_avg_monthly_benefit": 452,
}

ECPS_HF_PATH = "hf://policyengine/policyengine-us-data/enhanced_cps_2024.h5"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_sim(year: int):
    """Construct a single PE Microsimulation we can reuse for both queries."""
    print(f"[PE] loading enhanced_cps_2024 → uprating to {year}...", flush=True)
    t0 = time.time()
    from policyengine_us import Microsimulation  # type: ignore[import-not-found]
    sim = Microsimulation(dataset=ECPS_HF_PATH)
    print(f"[PE]   sim built in {time.time() - t0:.1f}s", flush=True)
    return sim


def _values(sim, var: str, period):
    """sim.calculate returns a MicroSeries; .values is the raw ndarray."""
    s = sim.calculate(var, period=period)
    return s.values, s.weights


def compute_pe_federal_income_tax(sim, year: int) -> dict:
    """Sum income_tax_main_rates × tax_unit_weight nationwide."""
    print(f"[PE] computing income_tax_main_rates for {year}...", flush=True)
    t0 = time.time()
    main_rates_arr, tu_weights = _values(sim, "income_tax_main_rates", year)
    print(f"[PE]   computed in {time.time() - t0:.1f}s", flush=True)

    total_revenue = float((main_rates_arr * tu_weights).sum())
    has_liability = main_rates_arr > 0
    weighted_filers = float(tu_weights[has_liability].sum())
    weighted_total = float(tu_weights.sum())
    avg = (
        float((main_rates_arr[has_liability] * tu_weights[has_liability]).sum() / weighted_filers)
        if weighted_filers > 0 else 0.0
    )

    return {
        "scope": "US",
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        **AXIOM_FED_INCOME_TAX_US_2026,
        "pe_total_revenue": total_revenue,
        "pe_n_tax_units": int(len(main_rates_arr)),
        "pe_weighted_filers": weighted_filers,
        "pe_weighted_total": weighted_total,
        "pe_avg_per_filer": avg,
    }


def compute_pe_co_snap(sim, year: int) -> dict:
    """Sum SNAP × spm_unit_weight × 12 for SPM units in CO."""
    print(f"[PE] computing CO SNAP for {year}...", flush=True)
    t0 = time.time()
    # SNAP is a monthly variable. PE annualises in calculate() via its
    # period broadcasting, but to match the convention used by axiom
    # (monthly_allotment × 12) we explicitly multiply.
    snap_monthly_arr, spm_weights = _values(sim, "snap", f"{year}-01")
    state_per_spm_arr, _ = _values(sim, "state_code_str", year)  # entity-mapped to SPM
    print(f"[PE]   computed in {time.time() - t0:.1f}s", flush=True)

    in_co = state_per_spm_arr == "CO"
    annual_cost = float((snap_monthly_arr[in_co] * spm_weights[in_co]).sum() * 12)
    weighted_units = float(spm_weights[in_co].sum())
    weighted_recipients = float(spm_weights[in_co][snap_monthly_arr[in_co] > 0].sum())

    return {
        "scope": "CO",
        "axiom_output": "snap_allotment (× 12 for annual)",
        "pe_variable": "snap (monthly × 12)",
        **AXIOM_CO_SNAP_2026,
        "pe_total_annual_cost": annual_cost,
        "pe_weighted_spm_units": weighted_units,
        "pe_weighted_recipients": weighted_recipients,
    }


def main() -> int:
    payload: dict = {
        "computed_at": _utc_now(),
        "year": 2026,
        "dataset": "enhanced_cps_2024 (uprated to 2026)",
        "federal_income_tax": None,
        "co_snap": None,
        "errors": [],
    }
    try:
        sim = _build_sim(year=2026)
    except Exception as exc:
        payload["errors"].append(f"sim build: {exc}")
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=2))
        print(f"[PE] sim build failed: {exc}", flush=True)
        return 1

    try:
        payload["federal_income_tax"] = compute_pe_federal_income_tax(sim, year=2026)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        payload["errors"].append(f"federal_income_tax: {exc}")

    try:
        payload["co_snap"] = compute_pe_co_snap(sim, year=2026)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        payload["errors"].append(f"co_snap: {exc}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"[PE] wrote {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
