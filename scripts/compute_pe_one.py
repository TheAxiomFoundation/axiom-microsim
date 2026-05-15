"""Compute one PolicyEngine aggregate for the /compare endpoint.

Runs in the policyengine.py venv (where ``policyengine_us`` is installed).
The FastAPI server subprocesses into this script per request — no
caching, every call recomputes.

Wire shape::

    python compute_pe_one.py --program <id> --state <code> --year <yr>

Prints a single-line JSON result on stdout. Errors go to stderr +
non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time


ECPS_HF_PATH = "hf://policyengine/policyengine-us-data/enhanced_cps_2024.h5"


def _values(sim, var: str, period):
    s = sim.calculate(var, period=period)
    return s.values, s.weights


def run(program: str, state: str, year: int) -> dict:
    sys.stderr.write(f"[PE] loading {ECPS_HF_PATH} for {year}...\n")
    sys.stderr.flush()
    t0 = time.time()
    from policyengine_us import Microsimulation  # type: ignore[import-not-found]
    sim = Microsimulation(dataset=ECPS_HF_PATH)
    sys.stderr.write(f"[PE]   sim built in {time.time() - t0:.1f}s\n")
    sys.stderr.flush()

    if program == "federal-income-tax":
        return _run_federal_income_tax(sim, state, year)
    if program == "federal-ctc":
        return _run_federal_ctc(sim, state, year)
    if program == "co-snap":
        return _run_co_snap(sim, state, year)
    raise ValueError(f"unknown program {program!r}")


def _run_federal_income_tax(sim, state: str, year: int) -> dict:
    t0 = time.time()
    main_rates, weights = _values(sim, "income_tax_main_rates", year)
    if state and state != "US":
        # Need per-tax-unit state. PE: state_code_str is a Household var;
        # map to tax_unit via PE's entity machinery.
        state_per_tu = sim.calculate("state_code_str", period=year, map_to="tax_unit").values
        mask = state_per_tu == state
        main_rates = main_rates[mask]
        weights = weights[mask]
    sys.stderr.write(f"[PE]   computed in {time.time() - t0:.1f}s\n")

    total = float((main_rates * weights).sum())
    has_liability = main_rates > 0
    weighted_filers = float(weights[has_liability].sum())
    avg = (
        float((main_rates[has_liability] * weights[has_liability]).sum() / weighted_filers)
        if weighted_filers else 0.0
    )
    return {
        "scope": state,
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        "pe_total": total,
        "pe_n_units": int(len(main_rates)),
        "pe_weighted_filers": weighted_filers,
        "pe_weighted_total": float(weights.sum()),
        "pe_avg_per_filer": avg,
    }


def _run_federal_ctc(sim, state: str, year: int) -> dict:
    t0 = time.time()
    ctc, weights = _values(sim, "ctc_value", year)
    if state and state != "US":
        state_per_tu = sim.calculate("state_code_str", period=year, map_to="tax_unit").values
        mask = state_per_tu == state
        ctc = ctc[mask]
        weights = weights[mask]
    sys.stderr.write(f"[PE]   computed in {time.time() - t0:.1f}s\n")

    total = float((ctc * weights).sum())
    has_credit = ctc > 0
    weighted_recipients = float(weights[has_credit].sum())
    avg = (
        float((ctc[has_credit] * weights[has_credit]).sum() / weighted_recipients)
        if weighted_recipients else 0.0
    )
    return {
        "scope": state,
        "axiom_output": "ctc_maximum_before_phase_out_under_subsection_h",
        "pe_variable": "ctc_value (post phase-out)",
        "pe_total": total,
        "pe_n_units": int(len(ctc)),
        "pe_weighted_filers": weighted_recipients,
        "pe_weighted_total": float(weights.sum()),
        "pe_avg_per_filer": avg,
    }


def _run_co_snap(sim, state: str, year: int) -> dict:
    t0 = time.time()
    snap_per_hh = sim.calculate("snap", period=f"{year}-01", map_to="household")
    state_per_hh = sim.calculate("state_code_str", period=year)
    snap_arr = snap_per_hh.values
    weights = snap_per_hh.weights
    state_arr = state_per_hh.values
    mask = state_arr == state if state and state != "US" else slice(None)
    snap_arr = snap_arr[mask]
    weights = weights[mask]
    sys.stderr.write(f"[PE]   computed in {time.time() - t0:.1f}s\n")

    annual = float((snap_arr * weights).sum() * 12)
    weighted_recipients = float(weights[snap_arr > 0].sum())
    return {
        "scope": state,
        "axiom_output": "snap_allotment (× 12 for annual)",
        "pe_variable": "snap (monthly × 12)",
        "pe_total": annual,
        "pe_n_units": int(len(snap_arr)),
        "pe_weighted_filers": weighted_recipients,
        "pe_weighted_total": float(weights.sum()),
        "pe_avg_per_filer": (
            annual / weighted_recipients if weighted_recipients else 0.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    try:
        result = run(args.program, args.state, args.year)
    except Exception as exc:
        sys.stderr.write(f"[PE] failed: {exc}\n")
        import traceback; traceback.print_exc(file=sys.stderr)
        json.dump({"error": str(exc)}, sys.stdout)
        return 1
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
