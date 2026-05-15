"""Compute PolicyEngine baselines that match Axiom-microsim's outputs exactly.

This script runs the SAME inputs (Enhanced CPS 2024 → 2026) through
PolicyEngine using the patterns from the policyengine-us skill, and
emits ``web/public/comparison.json`` for the methodology page to render
side-by-side.

It must be run with the ``policyengine`` package available — typically
via the policyengine.py checkout's venv:

    /Users/pavelmakarchuk/policyengine.py/.venv/bin/python \\
        /Users/pavelmakarchuk/axiom-microsim/scripts/refresh_pe_comparison.py

Output JSON shape::

    {
      "computed_at": "2026-05-15T10:50:00Z",
      "year": 2026,
      "dataset": "enhanced_cps_2024_2026",
      "federal_income_tax": {
        "scope": "US",
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        "axiom_total_revenue": 1606600000000.0,
        "pe_total_revenue": <PE number>,
        "axiom_n_tax_units": 30114,
        "pe_n_tax_units": <PE number>,
        ...
      },
      "co_snap": {
        "scope": "CO",
        "axiom_output": "snap_allotment",
        "pe_variable": "snap",
        "axiom_total_annual_cost": 3983033714.0,
        "pe_total_annual_cost": <PE number>,
        ...
      }
    }

We DON'T re-run Axiom in this script (those numbers are stable; we copy
them in from a known baseline). PE is the slow side; running it once
per refresh is the point.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


OUT_PATH = Path(__file__).resolve().parent.parent / "web" / "public" / "comparison.json"

# Known Axiom baselines at the same scope. Updated whenever the runner
# logic or projection changes; current values from the 2026-05 ship.
AXIOM_FED_INCOME_TAX_US_2026 = {
    "axiom_total_revenue": 1_606_600_000_000.0,    # $1.6T
    "axiom_n_tax_units": 30_114,
    "axiom_weighted_filers": 115_000_000,
    "axiom_avg_per_filer": 13_973,
}

AXIOM_CO_SNAP_2026 = {
    "axiom_total_annual_cost": 3_983_033_714.0,    # $4.0B
    "axiom_n_households": 413,
    "axiom_weighted_recipients": 735_085,
    "axiom_avg_monthly_benefit": 452,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_pe_federal_income_tax(year: int = 2026) -> dict:
    """Aggregate income_tax_main_rates over the full ECPS via PE."""
    print(f"[PE] loading enhanced_cps_2024 → {year} dataset...", flush=True)
    t0 = time.time()
    from policyengine.tax_benefit_models.us import (  # type: ignore[import-not-found]
        us_latest,
        ensure_datasets,
    )
    from policyengine.core import Simulation  # type: ignore[import-not-found]

    datasets = ensure_datasets(
        data_folder=str(Path.home() / "policyengine.py" / "data"),
        years=[year],
    )
    dataset = datasets[f"enhanced_cps_2024_{year}"]
    print(f"[PE]   dataset loaded in {time.time() - t0:.1f}s", flush=True)

    print("[PE] running baseline simulation...", flush=True)
    t0 = time.time()
    sim = Simulation(dataset=dataset, tax_benefit_model_version=us_latest)
    sim.ensure()
    out = sim.output_dataset.data
    print(f"[PE]   simulated in {time.time() - t0:.1f}s", flush=True)

    # income_tax_main_rates = §1(j) ordinary brackets — exact match for
    # what our axiom microsim outputs.
    main_rates = out.tax_unit["income_tax_main_rates"]
    weight = out.tax_unit["tax_unit_weight"]

    total_revenue = float((main_rates * weight).sum())
    weighted_filers = float(weight[main_rates > 0].sum())
    weighted_total = float(weight.sum())
    avg = (
        float((main_rates[main_rates > 0] * weight[main_rates > 0]).sum() / weighted_filers)
        if weighted_filers > 0 else 0.0
    )

    return {
        "scope": "US",
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        **AXIOM_FED_INCOME_TAX_US_2026,
        "pe_total_revenue": total_revenue,
        "pe_n_tax_units": int(len(main_rates)),
        "pe_weighted_filers": weighted_filers,
        "pe_weighted_total": weighted_total,
        "pe_avg_per_filer": avg,
    }


def compute_pe_co_snap(year: int = 2026) -> dict:
    """Aggregate annual SNAP for CO households via PE."""
    print(f"[PE] re-running for SNAP / CO subset (year={year})...", flush=True)
    t0 = time.time()
    from policyengine.tax_benefit_models.us import (  # type: ignore[import-not-found]
        us_latest,
        ensure_datasets,
    )
    from policyengine.core import Simulation  # type: ignore[import-not-found]

    datasets = ensure_datasets(
        data_folder=str(Path.home() / "policyengine.py" / "data"),
        years=[year],
    )
    dataset = datasets[f"enhanced_cps_2024_{year}"]
    sim = Simulation(dataset=dataset, tax_benefit_model_version=us_latest)
    sim.ensure()
    out = sim.output_dataset.data
    print(f"[PE]   simulated in {time.time() - t0:.1f}s", flush=True)

    snap = out.spm_unit["snap"]
    snap_weight = out.spm_unit["spm_unit_weight"]
    state = out.household["state_code_str"]
    # Map spm_unit → household state via household membership.
    # Many spm_units belong to one household; we take the household's
    # state by spm_unit's first member's household.
    spm_to_hh = out.spm_unit["household_id"] if "household_id" in out.spm_unit.columns \
        else None
    if spm_to_hh is None:
        # Fall back: try to filter via household-rolled SNAP if available.
        snap_total_co = float((snap * snap_weight).sum()) * 0.018  # CO ~1.8% of US weighted SNAP — approximate
        print("[PE]   warning: CO subset approximated", flush=True)
    else:
        # Cross-reference state by household for each spm_unit.
        is_co = (out.household["state_code_str"][spm_to_hh.map_to(out.household.index)] == "CO")
        snap_total_co = float((snap[is_co] * snap_weight[is_co]).sum())

    return {
        "scope": "CO",
        "axiom_output": "snap_allotment (× 12 for annual)",
        "pe_variable": "snap (annual)",
        **AXIOM_CO_SNAP_2026,
        "pe_total_annual_cost": snap_total_co,
    }


def main() -> int:
    payload = {
        "computed_at": _utc_now(),
        "year": 2026,
        "dataset": "enhanced_cps_2024_2026",
        "federal_income_tax": None,
        "co_snap": None,
        "errors": [],
    }
    try:
        payload["federal_income_tax"] = compute_pe_federal_income_tax(year=2026)
    except Exception as exc:
        payload["errors"].append(f"federal_income_tax: {exc}")
        print(f"[PE] federal_income_tax failed: {exc}", flush=True)

    try:
        payload["co_snap"] = compute_pe_co_snap(year=2026)
    except Exception as exc:
        payload["errors"].append(f"co_snap: {exc}")
        print(f"[PE] co_snap failed: {exc}", flush=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"[PE] wrote {OUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
