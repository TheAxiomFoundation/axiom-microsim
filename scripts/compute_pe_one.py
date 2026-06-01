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
import os
import sys
import time
from pathlib import Path

import numpy as np


ECPS_HF_PATH = "hf://policyengine/policyengine-us-data/enhanced_cps_2024.h5"
LOCAL_ECPS_PATH = Path("~/Downloads/enhanced_cps_2024.h5").expanduser()
ECPS_PATH = (
    os.environ.get("AXIOM_PE_ECPS_PATH")
    or os.environ.get("AXIOM_ECPS_PATH")
    or (str(LOCAL_ECPS_PATH) if LOCAL_ECPS_PATH.exists() else ECPS_HF_PATH)
)
NOISE_FLOOR = 1.0


def _values(sim, var: str, period):
    s = sim.calculate(var, period=period)
    return s.values, s.weights


def _series(sim, var: str, period, *, map_to: str | None = None):
    if map_to is None:
        s = sim.calculate(var, period=period)
    else:
        s = sim.calculate(var, period=period, map_to=map_to)
    return np.asarray(s.values), np.asarray(s.weights, dtype=float)


def _build_sim(year: int, overrides: list[dict] | None):
    """Build a Microsimulation, applying optional parametric overrides.

    Each override is ``{"path": "<dotted PE param path>", "value": <num>}``.
    Wrapped into a PolicyEngine Reform via ``Reform.from_dict``. Paths that
    target a bracket of a Scale parameter must include the bracket index,
    e.g. ``gov.irs.credits.ctc.amount.base.brackets[0].amount``.
    """
    sys.stderr.write(f"[PE] loading {ECPS_PATH} for {year}")
    if overrides:
        sys.stderr.write(f" with {len(overrides)} override(s)")
    sys.stderr.write("...\n")
    sys.stderr.flush()
    t0 = time.time()
    from policyengine_us import Microsimulation  # type: ignore[import-not-found]

    reform = None
    if overrides:
        from policyengine_core.reforms import Reform  # type: ignore[import-not-found]
        reform_dict = {}
        period_key = f"{year}-01-01.{year}-12-31"
        for ov in overrides:
            reform_dict[ov["path"]] = {period_key: ov["value"]}
        reform = Reform.from_dict(reform_dict, country_id="us")
        sys.stderr.write(f"[PE]   built reform with {len(overrides)} override(s)\n")

    sim = Microsimulation(dataset=ECPS_PATH, reform=reform) if reform \
        else Microsimulation(dataset=ECPS_PATH)
    sys.stderr.write(f"[PE]   sim built in {time.time() - t0:.1f}s\n")
    sys.stderr.flush()
    return sim


def run(program: str, state: str, year: int, overrides: list[dict] | None) -> dict:
    baseline_sim = _build_sim(year, None)
    baseline = _run_program(baseline_sim, program, state, year)
    reform = None
    if overrides:
        reform_sim = _build_sim(year, overrides)
        reform = _run_program(reform_sim, program, state, year)
    return _comparison_result(program, state, year, baseline, reform)


def _run_program(sim, program: str, state: str, year: int) -> dict:
    if program == "federal-income-tax":
        return _run_federal_income_tax(sim, state, year)
    if program == "federal-ctc":
        return _run_federal_ctc(sim, state, year)
    if program == "co-snap":
        return _run_co_snap(sim, state, year)
    raise ValueError(f"unknown program {program!r}")


def _run_federal_income_tax(sim, state: str, year: int) -> dict:
    t0 = time.time()
    main_rates, weights = _series(sim, "income_tax_main_rates", year)
    agi, _ = _series(sim, "adjusted_gross_income", year)
    mask = _state_mask(sim, state, year, "tax_unit", len(main_rates))
    main_rates = main_rates[mask]
    weights = weights[mask]
    agi = agi[mask]
    sys.stderr.write(f"[PE]   computed in {time.time() - t0:.1f}s\n")

    return {
        "scope": state,
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        "values": main_rates,
        "weights": weights,
        "axis": agi,
        "annual_factor": 1.0,
        "higher_is_better": False,
        "poverty": _poverty(sim, state, year),
    }


def _run_federal_ctc(sim, state: str, year: int) -> dict:
    t0 = time.time()
    ctc, weights = _series(sim, "ctc_value", year)
    agi, _ = _series(sim, "adjusted_gross_income", year)
    mask = _state_mask(sim, state, year, "tax_unit", len(ctc))
    ctc = ctc[mask]
    weights = weights[mask]
    agi = agi[mask]
    sys.stderr.write(f"[PE]   computed in {time.time() - t0:.1f}s\n")

    return {
        "scope": state,
        "axiom_output": "ctc_maximum_before_phase_out_under_subsection_h",
        "pe_variable": "ctc_value (post phase-out)",
        "values": ctc,
        "weights": weights,
        "axis": agi,
        "annual_factor": 1.0,
        "higher_is_better": True,
        "poverty": _poverty(sim, state, year),
    }


def _run_co_snap(sim, state: str, year: int) -> dict:
    t0 = time.time()
    snap_arr, weights = _series(sim, "snap", f"{year}-01", map_to="household")
    income, _ = _series(sim, "household_market_income", year)
    mask = _state_mask(sim, state, year, "household", len(snap_arr))
    snap_arr = snap_arr[mask]
    weights = weights[mask]
    income = income[mask]
    sys.stderr.write(f"[PE]   computed in {time.time() - t0:.1f}s\n")

    return {
        "scope": state,
        "axiom_output": "snap_allotment (× 12 for annual)",
        "pe_variable": "snap (monthly × 12)",
        "values": snap_arr,
        "weights": weights,
        "axis": income,
        "annual_factor": 12.0,
        "higher_is_better": True,
        "poverty": _poverty(sim, state, year),
    }


def _comparison_result(
    program: str,
    state: str,
    year: int,
    baseline: dict,
    reform: dict | None,
) -> dict:
    baseline_summary = _baseline_summary(baseline)
    result = {
        "scope": state,
        "axiom_output": baseline["axiom_output"],
        "pe_variable": baseline["pe_variable"],
        "pe_total": baseline_summary["annual_cost"],
        "pe_n_units": int(len(baseline["values"])),
        "pe_weighted_filers": baseline_summary["households_with_benefit"],
        "pe_weighted_total": float(baseline["weights"].sum()),
        "pe_avg_per_filer": baseline_summary["average_monthly_benefit"],
        "pe_baseline": baseline_summary,
        "pe_reform": None,
        "pe_poverty": _public_poverty(baseline.get("poverty")),
        "pe_poverty_impact": None,
    }
    if reform is None:
        return result

    reform_summary = _reform_summary(baseline, reform)
    result.update({
        "pe_total": reform_summary["reform_annual_cost"],
        "pe_weighted_filers": float(reform["weights"][reform["values"] > 0].sum()),
        "pe_avg_per_filer": _average_positive(reform["values"], reform["weights"]),
        "pe_reform": reform_summary,
        "pe_poverty_impact": _poverty_impact(
            baseline.get("poverty"), reform.get("poverty")
        ),
    })
    return result


def _baseline_summary(run: dict) -> dict:
    values = np.asarray(run["values"], dtype=float)
    weights = np.asarray(run["weights"], dtype=float)
    annual_factor = float(run["annual_factor"])
    monthly_total = float((values * weights).sum())
    annual_total = monthly_total * annual_factor
    return {
        "annual_cost": annual_total,
        "monthly_cost": annual_total / 12,
        "households_with_benefit": float(weights[values > 0].sum()),
        "average_monthly_benefit": _average_positive(values, weights),
        "decile_distribution": _decile_distribution(values, weights, run["axis"]),
    }


def _reform_summary(baseline: dict, reform: dict) -> dict:
    base = np.asarray(baseline["values"], dtype=float)
    ref = np.asarray(reform["values"], dtype=float)
    weights = np.asarray(baseline["weights"], dtype=float)
    delta = ref - base
    if baseline["higher_is_better"]:
        winners = delta > NOISE_FLOOR
        losers = delta < -NOISE_FLOOR
        avg_gain_values = delta[winners]
        avg_loss_values = -delta[losers]
    else:
        winners = delta < -NOISE_FLOOR
        losers = delta > NOISE_FLOOR
        avg_gain_values = -delta[winners]
        avg_loss_values = delta[losers]
    win_w = float(weights[winners].sum())
    lose_w = float(weights[losers].sum())
    base_annual = float((base * weights).sum() * float(baseline["annual_factor"]))
    ref_annual = float((ref * weights).sum() * float(baseline["annual_factor"]))
    return {
        "baseline_annual_cost": base_annual,
        "reform_annual_cost": ref_annual,
        "delta_annual_cost": ref_annual - base_annual,
        "households_winners": win_w,
        "households_losers": lose_w,
        "households_unchanged": float(weights.sum()) - win_w - lose_w,
        "households_total_weighted": float(weights.sum()),
        "average_winner_gain_monthly": (
            float((avg_gain_values * weights[winners]).sum() / win_w) if win_w else 0.0
        ),
        "average_loser_loss_monthly": (
            float((avg_loss_values * weights[losers]).sum() / lose_w) if lose_w else 0.0
        ),
        "decile_impact": _decile_impact(delta, weights, baseline["axis"]),
    }


def _state_mask(sim, state: str, year: int, entity: str, expected_len: int):
    if not state or state == "US":
        return slice(None)
    map_to = None if entity == "household" else entity
    state_values, _ = _series(sim, "state_code_str", year, map_to=map_to)
    if len(state_values) != expected_len:
        raise ValueError(
            f"state mask for {entity} has {len(state_values)} rows; expected {expected_len}"
        )
    return state_values == state


def _decile_distribution(values: np.ndarray, weights: np.ndarray, axis: np.ndarray) -> list[dict]:
    bins = []
    for i, mask, lo, hi in _weighted_decile_masks(axis, weights):
        wm = weights[mask]
        vm = values[mask]
        total_w = float(wm.sum())
        bins.append({
            "decile": i,
            "income_floor": lo,
            "income_ceiling": hi,
            "households_weighted": total_w,
            "mean_monthly_benefit": float((vm * wm).sum() / total_w) if total_w else 0.0,
            "share_receiving": float(wm[vm > 0].sum() / total_w) if total_w else 0.0,
        })
    return bins


def _decile_impact(delta: np.ndarray, weights: np.ndarray, axis: np.ndarray) -> list[dict]:
    bins = []
    for i, mask, lo, hi in _weighted_decile_masks(axis, weights):
        wm = weights[mask]
        dm = delta[mask]
        total_w = float(wm.sum())
        bins.append({
            "decile": i,
            "income_floor": lo,
            "income_ceiling": hi,
            "households_weighted": total_w,
            "mean_delta": float((dm * wm).sum() / total_w) if total_w else 0.0,
            "share_winners": float(wm[dm > NOISE_FLOOR].sum() / total_w) if total_w else 0.0,
            "share_losers": float(wm[dm < -NOISE_FLOOR].sum() / total_w) if total_w else 0.0,
        })
    return bins


def _weighted_decile_masks(axis: np.ndarray, weights: np.ndarray):
    axis = np.asarray(axis, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if len(axis) == 0 or float(weights.sum()) == 0.0:
        return []
    order = np.argsort(axis)
    a = axis[order]
    w = weights[order]
    cw = np.cumsum(w)
    cuts_q = np.linspace(0, 1, 11)
    cuts = np.interp(cuts_q, (cw - 0.5 * w) / cw[-1], a)
    cuts[0] = -np.inf
    cuts[-1] = np.inf
    masks = []
    for i in range(10):
        lo = cuts[i]
        hi = cuts[i + 1]
        mask = (axis >= lo) & (axis < hi) if i < 9 else (axis >= lo)
        masks.append((
            i + 1,
            mask,
            float(lo) if np.isfinite(lo) else 0.0,
            float(hi) if np.isfinite(hi) else float(axis.max()),
        ))
    return masks


def _average_positive(values: np.ndarray, weights: np.ndarray) -> float:
    positive = values > 0
    positive_w = float(weights[positive].sum())
    return float((values[positive] * weights[positive]).sum() / positive_w) if positive_w else 0.0


def _poverty(sim, state: str, year: int) -> dict | None:
    try:
        in_poverty, weights = _series(sim, "in_poverty", year, map_to="person")
        mask = _state_mask(sim, state, year, "person", len(in_poverty))
    except Exception as exc:
        sys.stderr.write(f"[PE]   poverty output unavailable: {exc}\n")
        return None
    in_poverty = np.asarray(in_poverty[mask], dtype=bool)
    weights = np.asarray(weights[mask], dtype=float)
    return {
        "in_poverty": in_poverty,
        "weights": weights,
        **_public_poverty({"in_poverty": in_poverty, "weights": weights}),
    }


def _public_poverty(poverty: dict | None) -> dict | None:
    if poverty is None:
        return None
    in_poverty = np.asarray(poverty["in_poverty"], dtype=bool)
    weights = np.asarray(poverty["weights"], dtype=float)
    total = float(weights.sum())
    count = float(weights[in_poverty].sum())
    return {
        "population_weighted": total,
        "in_poverty_weighted": count,
        "poverty_rate": count / total if total else 0.0,
    }


def _poverty_impact(baseline: dict | None, reform: dict | None) -> dict | None:
    if baseline is None or reform is None:
        return None
    base = np.asarray(baseline["in_poverty"], dtype=bool)
    ref = np.asarray(reform["in_poverty"], dtype=bool)
    weights = np.asarray(baseline["weights"], dtype=float)
    if len(base) != len(ref):
        return None
    base_public = _public_poverty(baseline)
    ref_public = _public_poverty(reform)
    assert base_public is not None and ref_public is not None
    lifted = base & ~ref
    entered = ~base & ref
    return {
        "baseline_poverty_rate": base_public["poverty_rate"],
        "reform_poverty_rate": ref_public["poverty_rate"],
        "delta_poverty_rate": ref_public["poverty_rate"] - base_public["poverty_rate"],
        "people_lifted_out_of_poverty": float(weights[lifted].sum()),
        "people_falling_into_poverty": float(weights[entered].sum()),
    }


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--overrides",
        default="[]",
        help='JSON list of {"path": "...", "value": ...} parameter overrides.',
    )
    args = parser.parse_args()

    try:
        overrides = json.loads(args.overrides)
        result = run(args.program, args.state, args.year, overrides or None)
    except Exception as exc:
        sys.stderr.write(f"[PE] failed: {exc}\n")
        import traceback; traceback.print_exc(file=sys.stderr)
        json.dump({"error": str(exc)}, sys.stdout)
        return 1
    json.dump(result, sys.stdout, default=_json_default)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
