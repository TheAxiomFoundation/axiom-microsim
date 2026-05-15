"""Phase-by-phase timing of an axiom-microsim run.

Splits the work into the smallest stages that move independently:

  1.  ECPS h5 load
  2.  Projection (ECPS columns → engine input columns)
  3.  YAML patch (only for reform)
  4.  Engine compile (only for reform; baseline reuses prebuilt artifact)
  5.  Build CompiledExecutionRequest dict (Python loop)
  6.  JSON encode the request to a string
  7.  Subprocess: spawn engine, write JSON to stdin, read stdout
  8.  JSON decode the response
  9.  Decode-to-numpy + aggregate

Usage::

    python scripts/profile_run.py [--program <id>] [--state <code>] \
        [--reform] [--repeats N]

Run from the project root with the venv that has axiom_microsim installed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Phase:
    name: str
    seconds: float
    bytes_in: int = 0
    bytes_out: int = 0
    items: int = 0


@contextmanager
def time_phase(name: str, sink: list[Phase]):
    t0 = time.perf_counter()
    bag: dict = {}
    yield bag
    sink.append(
        Phase(
            name=name,
            seconds=time.perf_counter() - t0,
            bytes_in=bag.get("bytes_in", 0),
            bytes_out=bag.get("bytes_out", 0),
            items=bag.get("items", 0),
        )
    )


def _print_phase_table(label: str, phases: list[Phase]) -> None:
    total = sum(p.seconds for p in phases)
    print(f"\n=== {label} (total {total*1000:.0f} ms) ===")
    print(f"{'phase':<28} {'time':>10} {'%':>6}  {'items':>10}  {'bytes':>14}")
    print("-" * 78)
    for p in phases:
        bytes_str = (
            f"{p.bytes_out:>14,}" if p.bytes_out
            else (f"{p.bytes_in:>14,}" if p.bytes_in else "")
        )
        items_str = f"{p.items:>10,}" if p.items else ""
        pct = (p.seconds / total * 100) if total else 0
        print(f"{p.name:<28} {p.seconds*1000:>8.1f} ms {pct:>5.1f}%  {items_str:>10}  {bytes_str}")
    print("-" * 78)
    print(f"{'TOTAL':<28} {total*1000:>8.1f} ms")


# --- Per-program profile harnesses ------------------------------------------

def profile_co_snap(state: str, reform: bool) -> list[Phase]:
    from axiom_microsim.data.ecps_loader import load_state
    from axiom_microsim.project.co_snap import project as project_co_snap
    from axiom_microsim.run.microsim import (
        CO_SNAP_BASELINE_PROGRAM, CO_SNAP_BASELINE_ARTIFACT, CO_SNAP_INPUT_PREFIX,
        CO_SNAP_RELATION_NAME, DEFAULT_OUTPUTS, DEFAULT_OUTPUT_IDS, ENGINE_BIN,
        _slots, _scalar_value, ParameterOverride, _artifact_for, _execute_compiled,
    )
    phases: list[Phase] = []

    with time_phase("1 · ECPS h5 load (CO)", phases) as bag:
        batch = load_state(state)
        bag["items"] = batch.n_households

    with time_phase("2 · projection", phases) as bag:
        proj = project_co_snap(batch, period_year=2026)
        bag["items"] = proj.n_households

    overrides = []
    if reform:
        overrides = [
            ParameterOverride(
                repo="rules-us",
                file_relative="policies/usda/snap/fy-2026-cola/maximum-allotments.yaml",
                parameter="snap_maximum_allotment_table",
                patch_kind="scale_values", multiplier=1.10,
            )
        ]

    if reform:
        with time_phase("3+4 · YAML patch + engine compile", phases) as bag:
            artifact_path, scratch = _artifact_for(overrides)
            bag["items"] = len(overrides)
    else:
        artifact_path = CO_SNAP_BASELINE_ARTIFACT
        scratch = None

    # Hand-build the request so we can time each sub-step.
    output_names = DEFAULT_OUTPUTS
    output_ids = [DEFAULT_OUTPUT_IDS[n] for n in output_names]
    interval = {"start": "2026-01-01", "end": "2026-01-31"}
    period = {"period_kind": "month", "start": "2026-01-01", "end": "2026-01-31"}

    with time_phase("5 · build CompiledExecutionRequest", phases) as bag:
        hh_slots, person_slots = _slots()
        inputs: list[dict] = []
        relations: list[dict] = []
        queries: list[dict] = []
        for h_idx in range(proj.n_households):
            hh_id = f"h{h_idx}"
            for slot in hh_slots:
                value = (
                    proj.household_inputs[slot.name][h_idx]
                    if slot.name in proj.household_inputs
                    else slot.default
                )
                inputs.append({
                    "name": CO_SNAP_INPUT_PREFIX + slot.name, "entity": "Household",
                    "entity_id": hh_id, "interval": interval,
                    "value": _scalar_value(value),
                })
            queries.append({"entity_id": hh_id, "period": period, "outputs": output_ids})
        person_to_hh = np.searchsorted(proj.relation_offsets, np.arange(proj.n_persons), side="right") - 1
        for p_idx in range(proj.n_persons):
            person_id = f"p{p_idx}"
            hh_id = f"h{int(person_to_hh[p_idx])}"
            for slot in person_slots:
                value = (
                    proj.person_inputs[slot.name][p_idx]
                    if slot.name in proj.person_inputs
                    else slot.default
                )
                inputs.append({
                    "name": CO_SNAP_INPUT_PREFIX + slot.name, "entity": "Person",
                    "entity_id": person_id, "interval": interval,
                    "value": _scalar_value(value),
                })
            for slot in hh_slots:
                if slot.name not in proj.household_inputs:
                    continue
                inputs.append({
                    "name": CO_SNAP_INPUT_PREFIX + slot.name, "entity": "Person",
                    "entity_id": person_id, "interval": interval,
                    "value": _scalar_value(proj.household_inputs[slot.name][int(person_to_hh[p_idx])]),
                })
            relations.append({
                "name": CO_SNAP_RELATION_NAME,
                "tuple": [person_id, hh_id], "interval": interval,
            })
        request = {"mode": "fast", "dataset": {"inputs": inputs, "relations": relations}, "queries": queries}
        bag["items"] = len(inputs) + len(relations) + len(queries)

    with time_phase("6 · JSON encode request", phases) as bag:
        request_json = json.dumps(request)
        bag["bytes_out"] = len(request_json)

    with time_phase("7 · engine subprocess (run-compiled)", phases) as bag:
        proc = subprocess.run(
            [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
            input=request_json, text=True, capture_output=True,
        )
        bag["bytes_in"] = len(proc.stdout)
        bag["bytes_out"] = len(request_json)

    with time_phase("8 · JSON decode response", phases) as bag:
        response = json.loads(proc.stdout)
        bag["bytes_in"] = len(proc.stdout)
        bag["items"] = len(response.get("results", []))

    with time_phase("9 · decode + aggregate", phases) as bag:
        arrays = {n: np.zeros(proj.n_households, dtype=np.float64) for n in output_names}
        id_to_name = {v: k for k, v in DEFAULT_OUTPUT_IDS.items()}
        for qr in response["results"]:
            eid = qr["entity_id"]
            if not eid.startswith("h"):
                continue
            h_idx = int(eid[1:])
            for key, out in qr["outputs"].items():
                name = id_to_name.get(key, key)
                if name not in arrays: continue
                if out["kind"] == "scalar":
                    v = out["value"]
                    if v["kind"] in ("decimal", "integer"):
                        arrays[name][h_idx] = float(v["value"])
        total = float((arrays["snap_allotment"] * proj.household_weight).sum())
        bag["items"] = proj.n_households

    if scratch:
        import shutil; shutil.rmtree(scratch, ignore_errors=True)
        artifact_path.unlink(missing_ok=True)

    return phases


def profile_federal_income_tax(state: str, reform: bool) -> list[Phase]:
    from axiom_microsim.data.ecps_loader import load_state_tax_units
    from axiom_microsim.project.federal_income_tax import project as project_fit
    from axiom_microsim.run.microsim import (
        CO_SNAP_BASELINE_ARTIFACT, ENGINE_BIN, FED_INCOME_TAX_OUTPUT_IDS,
        FED_INCOME_TAX_DEFAULT_OUTPUTS, ParameterOverride, _scalar_value,
        _fed_artifact_for, ARTIFACTS_DIR,
    )
    phases: list[Phase] = []

    with time_phase("1 · ECPS h5 load (tax units)", phases) as bag:
        batch = load_state_tax_units(state)
        bag["items"] = batch.n_tax_units

    with time_phase("2 · projection", phases) as bag:
        proj = project_fit(batch, period_year=2026)
        bag["items"] = proj.n_tax_units

    overrides = []
    if reform:
        overrides = [
            ParameterOverride(
                repo="rules-us",
                file_relative="policies/irs/rev-proc-2025-32/income-tax-brackets.yaml",
                parameter="income_tax_bracket_rates",
                patch_kind="scale_values", multiplier=1.10,
            )
        ]

    artifact_path, scratch = _fed_artifact_for(overrides) if overrides else (
        ARTIFACTS_DIR / "federal-income-tax.compiled.json", None
    )
    if reform:
        # Already done as part of _fed_artifact_for
        pass

    output_names = FED_INCOME_TAX_DEFAULT_OUTPUTS
    output_ids = [FED_INCOME_TAX_OUTPUT_IDS[n] for n in output_names]
    interval = {"start": "2026-01-01", "end": "2026-12-31"}
    period = {"period_kind": "tax_year", "start": interval["start"], "end": interval["end"]}

    with time_phase("5 · build CompiledExecutionRequest", phases) as bag:
        inputs: list[dict] = []
        queries: list[dict] = []
        for tu_idx in range(proj.n_tax_units):
            tu_id = f"tu{tu_idx}"
            for full_input_id, column in proj.inputs.items():
                inputs.append({
                    "name": full_input_id, "entity": "TaxUnit", "entity_id": tu_id,
                    "interval": interval, "value": _scalar_value(column[tu_idx]),
                })
            queries.append({"entity_id": tu_id, "period": period, "outputs": output_ids})
        request = {"mode": "fast", "dataset": {"inputs": inputs, "relations": []}, "queries": queries}
        bag["items"] = len(inputs) + len(queries)

    with time_phase("6 · JSON encode request", phases) as bag:
        request_json = json.dumps(request)
        bag["bytes_out"] = len(request_json)

    with time_phase("7 · engine subprocess (run-compiled)", phases) as bag:
        proc = subprocess.run(
            [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
            input=request_json, text=True, capture_output=True,
        )
        bag["bytes_in"] = len(proc.stdout)

    with time_phase("8 · JSON decode response", phases) as bag:
        response = json.loads(proc.stdout)
        bag["bytes_in"] = len(proc.stdout)
        bag["items"] = len(response.get("results", []))

    with time_phase("9 · decode + aggregate", phases) as bag:
        arrays = {n: np.zeros(proj.n_tax_units, dtype=np.float64) for n in output_names}
        id_to_name = {v: k for k, v in FED_INCOME_TAX_OUTPUT_IDS.items()}
        for qr in response["results"]:
            eid = qr["entity_id"]
            if not eid.startswith("tu"): continue
            idx = int(eid[2:])
            for key, out in qr["outputs"].items():
                name = id_to_name.get(key, key)
                if name not in arrays: continue
                if out["kind"] == "scalar":
                    v = out["value"]
                    if v["kind"] in ("decimal", "integer"):
                        arrays[name][idx] = float(v["value"])
        total = float((arrays["income_tax_main_rates"] * batch.tax_unit_weight).sum())
        bag["items"] = proj.n_tax_units

    if scratch:
        import shutil; shutil.rmtree(scratch, ignore_errors=True)
        artifact_path.unlink(missing_ok=True)

    return phases


def profile_federal_ctc(state: str, reform: bool) -> list[Phase]:
    from axiom_microsim.data.ecps_loader import load_state_tax_units
    from axiom_microsim.project.federal_ctc import project as project_ctc
    from axiom_microsim.run.microsim import (
        ENGINE_BIN, FED_CTC_OUTPUT_IDS, FED_CTC_DEFAULT_OUTPUTS,
        FED_CTC_RELATION_NAME, ParameterOverride, _scalar_value,
        _ctc_artifact_for, ARTIFACTS_DIR,
    )
    phases: list[Phase] = []

    with time_phase("1 · ECPS h5 load (tax units)", phases) as bag:
        batch = load_state_tax_units(state)
        bag["items"] = batch.n_tax_units

    with time_phase("2 · projection", phases) as bag:
        proj = project_ctc(batch, period_year=2026)
        bag["items"] = proj.n_tax_units

    overrides = []
    if reform:
        overrides = [
            ParameterOverride(
                repo="rules-us",
                file_relative="statutes/26/24/h.yaml",
                parameter="ctc_child_amount_under_subsection_h",
                patch_kind="set_formula", formula="3000",
            )
        ]

    artifact_path, scratch = _ctc_artifact_for(overrides) if overrides else (
        ARTIFACTS_DIR / "federal-ctc.compiled.json", None
    )

    output_names = FED_CTC_DEFAULT_OUTPUTS
    output_ids = [FED_CTC_OUTPUT_IDS[n] for n in output_names]
    interval = {"start": "2026-01-01", "end": "2026-12-31"}
    period = {"period_kind": "tax_year", "start": interval["start"], "end": interval["end"]}

    with time_phase("5 · build CompiledExecutionRequest", phases) as bag:
        inputs: list[dict] = []
        relations: list[dict] = []
        queries: list[dict] = []
        for tu_idx in range(proj.n_tax_units):
            tu_id = f"tu{tu_idx}"
            for full_id, column in proj.tax_unit_inputs.items():
                inputs.append({"name": full_id, "entity": "TaxUnit", "entity_id": tu_id,
                               "interval": interval, "value": _scalar_value(column[tu_idx])})
            queries.append({"entity_id": tu_id, "period": period, "outputs": output_ids})
        sort = proj.person_sort
        pos_in_sorted = np.arange(proj.n_persons)
        tu_for_person = np.searchsorted(proj.relation_offsets, pos_in_sorted, side="right") - 1
        for sorted_p_idx in range(proj.n_persons):
            person_id = f"p{sorted_p_idx}"
            tu_idx = int(tu_for_person[sorted_p_idx])
            tu_id = f"tu{tu_idx}"
            for full_id, column in proj.person_inputs.items():
                inputs.append({"name": full_id, "entity": "Person", "entity_id": person_id,
                               "interval": interval, "value": _scalar_value(column[sorted_p_idx])})
            for full_id, column in proj.tax_unit_inputs.items():
                inputs.append({"name": full_id, "entity": "Person", "entity_id": person_id,
                               "interval": interval, "value": _scalar_value(column[tu_idx])})
            relations.append({"name": FED_CTC_RELATION_NAME, "tuple": [person_id, tu_id], "interval": interval})
        request = {"mode": "fast", "dataset": {"inputs": inputs, "relations": relations}, "queries": queries}
        bag["items"] = len(inputs) + len(relations) + len(queries)

    with time_phase("6 · JSON encode request", phases) as bag:
        request_json = json.dumps(request)
        bag["bytes_out"] = len(request_json)

    with time_phase("7 · engine subprocess (run-compiled)", phases) as bag:
        proc = subprocess.run(
            [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
            input=request_json, text=True, capture_output=True,
        )
        bag["bytes_in"] = len(proc.stdout)

    with time_phase("8 · JSON decode response", phases) as bag:
        response = json.loads(proc.stdout)
        bag["bytes_in"] = len(proc.stdout)
        bag["items"] = len(response.get("results", []))

    with time_phase("9 · decode + aggregate", phases) as bag:
        arrays = {n: np.zeros(proj.n_tax_units, dtype=np.float64) for n in output_names}
        id_to_name = {v: k for k, v in FED_CTC_OUTPUT_IDS.items()}
        for qr in response["results"]:
            eid = qr["entity_id"]
            if not eid.startswith("tu"): continue
            idx = int(eid[2:])
            for key, out in qr["outputs"].items():
                name = id_to_name.get(key, key)
                if name not in arrays: continue
                if out["kind"] == "scalar":
                    v = out["value"]
                    if v["kind"] in ("decimal", "integer"):
                        arrays[name][idx] = float(v["value"])
        total = float((arrays["ctc_maximum_before_phase_out_under_subsection_h"] * batch.tax_unit_weight).sum())
        bag["items"] = proj.n_tax_units

    if scratch:
        import shutil; shutil.rmtree(scratch, ignore_errors=True)
        artifact_path.unlink(missing_ok=True)

    return phases


PROFILES = {
    "co-snap": profile_co_snap,
    "federal-income-tax": profile_federal_income_tax,
    "federal-ctc": profile_federal_ctc,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", choices=list(PROFILES) + ["all"], default="all")
    parser.add_argument("--state", default=None,
                        help="State code; defaults to CO for snap, US otherwise")
    parser.add_argument("--reform", action="store_true",
                        help="Profile a reform run (recompile + reform-only steps)")
    parser.add_argument("--repeats", type=int, default=2,
                        help="Best-of-N repeats per (program, kind) — first run is warmup")
    args = parser.parse_args()

    programs = list(PROFILES) if args.program == "all" else [args.program]
    for p in programs:
        state = args.state or ("CO" if p == "co-snap" else "US")
        kinds = ("baseline", "reform") if args.reform else ("baseline",)
        # CO SNAP reform compile fails locally due to engine import-resolver
        # naming mismatch (works in Modal where dirs use `rulespec-` prefix).
        if p == "co-snap" and "reform" in kinds:
            kinds = ("baseline",)
            print("\n[note] skipping CO SNAP reform locally (engine import-resolver naming)")
        for kind in kinds:
            best: list[Phase] | None = None
            best_total = float("inf")
            for r in range(args.repeats):
                phases = PROFILES[p](state, kind == "reform")
                total = sum(ph.seconds for ph in phases)
                if total < best_total:
                    best_total = total
                    best = phases
            assert best is not None
            label = f"{p} · {state} · {kind}  (best of {args.repeats})"
            _print_phase_table(label, best)
    return 0


if __name__ == "__main__":
    sys.exit(main())
