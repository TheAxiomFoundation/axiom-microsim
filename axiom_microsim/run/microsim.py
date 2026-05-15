"""Run CO SNAP over an :class:`EcpsBatch` via the engine binary.

We were going to use the dense (columnar) entry point. CO SNAP's where-
clauses reference derived values, which the dense compiler does not yet
support. So this v1 batches every CO household into a single
``CompiledExecutionRequest`` and shells out to the engine binary once.

For 413 CO households this lands in well under 5 s on a laptop. When the
dense compiler grows derived-where support, we swap the inner call for a
``CompiledDenseProgram.execute`` and the rest of the pipeline (project +
aggregate) stays unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
from ruamel.yaml import YAML

from ..data.ecps_loader import EcpsBatch, TaxUnitBatch
from ..project.co_snap import CoSnapProjection, project as project_co_snap
from ..project.federal_income_tax import (
    FedIncomeTaxProjection,
    project as project_federal_income_tax,
)


# --- Locations ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = Path(os.environ.get("AXIOM_ARTIFACTS_DIR", str(ROOT / "engine" / "artifacts")))
RULES_US_DIR = Path(os.environ.get("AXIOM_RULES_US_DIR", str(ROOT / "engine" / "rules-us")))
RULES_US_CO_DIR = Path(os.environ.get("AXIOM_RULES_US_CO_DIR", str(ROOT / "engine" / "rules-us-co")))
ENGINE_BIN = Path(
    os.environ.get(
        "AXIOM_RULES_ENGINE_BINARY",
        str(ROOT / "engine" / "axiom-rules-engine" / "target" / "release" / "axiom-rules-engine"),
    )
)

CO_SNAP_PROGRAM_REL = "policies/cdhs/snap/fy-2026-benefit-calculation.yaml"
CO_SNAP_BASELINE_PROGRAM = RULES_US_CO_DIR / CO_SNAP_PROGRAM_REL
CO_SNAP_BASELINE_ARTIFACT = ARTIFACTS_DIR / "co-snap.compiled.json"
# Schema dump (input slots × dtypes × defaults) generated from the artifact
# by axiom-co-snap. We need the full slot list because the engine demands
# every input filled — there's no implicit "use the compiled default."
CO_SNAP_BASE_SCHEMA = ARTIFACTS_DIR / "co-snap-base.json"

# §1(j) federal income tax — top-level program YAML. Compiles the §1(j)
# brackets logic together with §1(h) capital-gains imports and the
# rev-proc bracket parameters. Has no synthetic-program slug — we use
# the natural module IDs.
FED_INCOME_TAX_PROGRAM_REL = "statutes/26/1/j.yaml"

CO_SNAP_RELATION_NAME = "us:statutes/7/2012/j#relation.member_of_household"

# CO SNAP's compiled artifact carries schema name "co-snap.fy-2026" and
# expects InputRecord.name in the synthetic-input form. Mirrors
# `SYNTHETIC_INPUT_PREFIX` in axiom-co-snap/src/lib/programs/co-snap.ts.
CO_SNAP_INPUT_PREFIX = "axiom:co-snap-fy-2026#input."


def _input_id(slot: str) -> str:
    return CO_SNAP_INPUT_PREFIX + slot


# §1(j) outputs — TaxUnit-rooted, period=Year. The engine echoes the
# absolute id back as the dict key; we reverse-map to a friendly name.
FED_INCOME_TAX_OUTPUT_IDS: dict[str, str] = {
    "income_tax_main_rates": "us:statutes/26/1/j#income_tax_main_rates",
    "regular_tax_before_credits": "us:statutes/26/1/j#regular_tax_before_credits",
    "ordinary_taxable_income": "us:statutes/26/1/j#ordinary_taxable_income",
}
FED_INCOME_TAX_DEFAULT_OUTPUTS: tuple[str, ...] = tuple(FED_INCOME_TAX_OUTPUT_IDS)


# --- Reform overrides --------------------------------------------------------

@dataclass(frozen=True)
class ParameterOverride:
    """A single parameter patch.

    Mirrors the ``ParameterOverride`` shape in
    ``axiom-co-snap/src/lib/engine/patch-params.ts`` so a reform expressed
    in either app evaluates to the same patched program.
    """

    repo: Literal["rules-us", "rules-us-co"]
    file_relative: str
    parameter: str
    patch_kind: Literal["scale_values", "set_values", "scale_formula", "set_formula"]
    multiplier: float | None = None
    values: dict[int, float] | None = None
    formula: str | None = None


# --- Result ------------------------------------------------------------------

@dataclass
class MicrosimResult:
    program: str
    state: str
    period_year: int
    n_households: int
    n_persons: int
    household_weight: np.ndarray
    outputs: dict[str, np.ndarray]


# --- Public entry point ------------------------------------------------------

# Outputs we care about, by friendly name → absolute legal ID. The engine
# accepts either the absolute id in the request and returns it back; the
# friendly-name layer here keeps callers (CLI, server, aggregators) from
# having to know about RuleSpec coordinates.
DEFAULT_OUTPUT_IDS: dict[str, str] = {
    "snap_allotment": "us-co:regulations/10-ccr-2506-1/4.207.2#snap_allotment",
    "snap_regular_month_allotment": "us:statutes/7/2017/a#snap_regular_month_allotment",
    "snap_maximum_allotment": "us:policies/usda/snap/fy-2026-cola/maximum-allotments#snap_maximum_allotment",
    "snap_net_income_for_allotment": "us:statutes/7/2017/a#snap_net_income_for_allotment",
}
DEFAULT_OUTPUTS: tuple[str, ...] = tuple(DEFAULT_OUTPUT_IDS)


def run_co_snap(
    batch: EcpsBatch,
    *,
    period_year: int = 2026,
    overrides: list[ParameterOverride] | None = None,
    outputs: tuple[str, ...] = DEFAULT_OUTPUTS,
) -> MicrosimResult:
    projection = project_co_snap(batch, period_year=period_year)
    artifact_path, scratch = _artifact_for(overrides)
    try:
        out = _execute_compiled(projection, artifact_path, period_year, outputs)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
            artifact_path.unlink(missing_ok=True)

    return MicrosimResult(
        program="co-snap",
        state=batch.state,
        period_year=period_year,
        n_households=batch.n_households,
        n_persons=batch.n_persons,
        household_weight=batch.household_weight,
        outputs=out,
    )


def run_federal_income_tax(
    batch: TaxUnitBatch,
    *,
    period_year: int = 2026,
    overrides: list[ParameterOverride] | None = None,
    outputs: tuple[str, ...] = FED_INCOME_TAX_DEFAULT_OUTPUTS,
) -> "MicrosimResult":
    """Run §1(j) federal income tax over an ECPS tax-unit batch."""
    projection = project_federal_income_tax(batch, period_year=period_year)
    artifact_path, scratch = _fed_artifact_for(overrides)
    try:
        out = _execute_fed_income_tax(projection, artifact_path, period_year, outputs)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
            artifact_path.unlink(missing_ok=True)

    return MicrosimResult(
        program="federal-income-tax",
        state=batch.state,
        period_year=period_year,
        n_households=projection.n_tax_units,   # treat tax_units as the row entity
        n_persons=batch.n_persons,
        household_weight=batch.tax_unit_weight,
        outputs=out,
    )


# --- Schema (input slots + defaults) ----------------------------------------

@dataclass
class _SlotSpec:
    name: str
    dtype: str          # "bool" | "integer" | "decimal" | "date"
    default: object


def _load_schema() -> tuple[list[_SlotSpec], list[_SlotSpec]]:
    if not CO_SNAP_BASE_SCHEMA.exists():
        raise FileNotFoundError(
            f"CO SNAP schema dump missing at {CO_SNAP_BASE_SCHEMA}. Copy it from "
            f"axiom-co-snap/engine/artifacts/co-snap-base.json."
        )
    schema = json.loads(CO_SNAP_BASE_SCHEMA.read_text())
    hh = [_SlotSpec(s["name"], s["dtype"], s["default"]) for s in schema["household_inputs"]]
    pe = [_SlotSpec(s["name"], s["dtype"], s["default"]) for s in schema["person_inputs"]]
    return hh, pe


_HH_SLOTS: list[_SlotSpec] | None = None
_PERSON_SLOTS: list[_SlotSpec] | None = None


def _slots() -> tuple[list[_SlotSpec], list[_SlotSpec]]:
    global _HH_SLOTS, _PERSON_SLOTS
    if _HH_SLOTS is None or _PERSON_SLOTS is None:
        _HH_SLOTS, _PERSON_SLOTS = _load_schema()
    return _HH_SLOTS, _PERSON_SLOTS


# --- Compile / patch ---------------------------------------------------------

def _artifact_for(overrides: list[ParameterOverride] | None) -> tuple[Path, Path | None]:
    """Return ``(artifact_path, scratch_to_clean_or_None)``."""
    if not overrides:
        if not CO_SNAP_BASELINE_ARTIFACT.exists():
            _compile(CO_SNAP_BASELINE_PROGRAM, CO_SNAP_BASELINE_ARTIFACT)
        return CO_SNAP_BASELINE_ARTIFACT, None

    if not RULES_US_DIR.exists() or not RULES_US_CO_DIR.exists():
        raise FileNotFoundError(
            f"Rulespec dirs missing — expected {RULES_US_DIR} and {RULES_US_CO_DIR}. "
            "Run scripts/setup_engine.sh once."
        )
    scratch = Path(tempfile.mkdtemp(prefix="axiom-microsim-"))
    dst_us = scratch / "rules-us"
    dst_us_co = scratch / "rules-us-co"
    shutil.copytree(RULES_US_DIR, dst_us, symlinks=False)
    shutil.copytree(RULES_US_CO_DIR, dst_us_co, symlinks=False)
    for ov in overrides:
        target = (dst_us if ov.repo == "rules-us" else dst_us_co) / ov.file_relative
        _patch_yaml(target, ov)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_DIR / f".tmp-{scratch.name}.compiled.json"
    _compile(dst_us_co / CO_SNAP_PROGRAM_REL, out)
    return out, scratch


def _fed_artifact_for(overrides: list[ParameterOverride] | None) -> tuple[Path, Path | None]:
    """Compile §1(j) (with optional reform overrides) and return artifact path."""
    program_path = RULES_US_DIR / FED_INCOME_TAX_PROGRAM_REL
    if not overrides:
        baseline = ARTIFACTS_DIR / "federal-income-tax.compiled.json"
        if not baseline.exists():
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
            _compile(program_path, baseline)
        return baseline, None

    # Reform: copy the rules-us tree, patch, recompile.
    if not RULES_US_DIR.exists():
        raise FileNotFoundError(f"rules-us missing at {RULES_US_DIR}")
    scratch = Path(tempfile.mkdtemp(prefix="axiom-microsim-fed-"))
    dst = scratch / "rules-us"
    shutil.copytree(RULES_US_DIR, dst, symlinks=False)
    for ov in overrides:
        if ov.repo != "rules-us":
            raise ValueError(
                f"federal-income-tax reform overrides must target rules-us, got {ov.repo}"
            )
        _patch_yaml(dst / ov.file_relative, ov)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_DIR / f".tmp-{scratch.name}.compiled.json"
    _compile(dst / FED_INCOME_TAX_PROGRAM_REL, out)
    return out, scratch


def _execute_fed_income_tax(
    projection: FedIncomeTaxProjection,
    artifact_path: Path,
    period_year: int,
    output_names: tuple[str, ...],
) -> dict[str, np.ndarray]:
    """Build a CompiledExecutionRequest for §1(j) and run it."""
    interval = {"start": f"{period_year}-01-01", "end": f"{period_year}-12-31"}
    period = {"period_kind": "tax_year", "start": interval["start"], "end": interval["end"]}

    output_ids = [FED_INCOME_TAX_OUTPUT_IDS[n] for n in output_names]

    inputs: list[dict] = []
    queries: list[dict] = []
    for tu_idx in range(projection.n_tax_units):
        tu_id = f"tu{tu_idx}"
        for full_input_id, column in projection.inputs.items():
            inputs.append({
                "name": full_input_id,
                "entity": "TaxUnit",
                "entity_id": tu_id,
                "interval": interval,
                "value": _scalar_value(column[tu_idx]),
            })
        queries.append({"entity_id": tu_id, "period": period, "outputs": output_ids})

    request = {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": []},
        "queries": queries,
    }

    proc = subprocess.run(
        [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
        input=json.dumps(request), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"engine failed:\n{proc.stderr.strip()[:1500]}")
    response = json.loads(proc.stdout)

    id_to_name = {v: k for k, v in FED_INCOME_TAX_OUTPUT_IDS.items()}
    arrays = {n: np.zeros(projection.n_tax_units, dtype=np.float64) for n in output_names}
    for qr in response["results"]:
        eid = qr["entity_id"]
        if not eid.startswith("tu"):
            continue
        idx = int(eid[2:])
        for key, out in qr["outputs"].items():
            name = id_to_name.get(key, key)
            if name not in arrays:
                continue
            if out["kind"] == "scalar":
                v = out["value"]
                if v["kind"] in ("decimal", "integer"):
                    arrays[name][idx] = float(v["value"])
                elif v["kind"] == "bool":
                    arrays[name][idx] = 1.0 if v["value"] else 0.0
    return arrays


def _compile(program_yaml: Path, output_json: Path) -> None:
    if not ENGINE_BIN.exists():
        raise FileNotFoundError(
            f"Engine binary missing at {ENGINE_BIN}. Run scripts/setup_engine.sh once."
        )
    subprocess.run(
        [str(ENGINE_BIN), "compile", "--program", str(program_yaml), "--output", str(output_json)],
        check=True, capture_output=True,
    )


# --- Execute -----------------------------------------------------------------

def _execute_compiled(
    projection: CoSnapProjection,
    artifact_path: Path,
    period_year: int,
    output_names: tuple[str, ...],
) -> dict[str, np.ndarray]:
    output_ids = [DEFAULT_OUTPUT_IDS[n] for n in output_names]
    request = _build_compiled_request(projection, period_year, output_ids)
    proc = subprocess.run(
        [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
        input=json.dumps(request), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"engine failed:\n{proc.stderr.strip()}")
    response = json.loads(proc.stdout)
    id_to_name = {DEFAULT_OUTPUT_IDS[n]: n for n in output_names}
    return _collect_outputs(response, projection.n_households, output_names, id_to_name)


def _build_compiled_request(
    proj: CoSnapProjection,
    period_year: int,
    output_ids: list[str],
) -> dict:
    # SNAP is calculated monthly. Use January of the requested year as the
    # representative month — the run is interpreted as "what would each
    # household receive in this month under current rules."
    interval = {"start": f"{period_year}-01-01", "end": f"{period_year}-01-31"}
    period = {
        "period_kind": "month",
        "start": f"{period_year}-01-01",
        "end": f"{period_year}-01-31",
    }

    inputs: list[dict] = []
    relations: list[dict] = []
    queries: list[dict] = []

    hh_slots, person_slots = _slots()

    # Household-level inputs. The engine requires every slot in the schema
    # to be supplied; for those we have ECPS data for, use the projected
    # value, otherwise fall back to the slot's compiled default.
    for h_idx in range(proj.n_households):
        hh_id = f"h{h_idx}"
        for slot in hh_slots:
            value = (
                proj.household_inputs[slot.name][h_idx]
                if slot.name in proj.household_inputs
                else slot.default
            )
            inputs.append(_input_record(_input_id(slot.name), "Household", hh_id, interval, value))
        queries.append({
            "entity_id": hh_id,
            "period": period,
            "outputs": output_ids,
        })

    # Person-level inputs + member_of_household relations.
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
            inputs.append(_input_record(_input_id(slot.name), "Person", person_id, interval, value))
        relations.append({
            "name": CO_SNAP_RELATION_NAME,
            "tuple": [person_id, hh_id],
            "interval": interval,
        })

    return {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": relations},
        "queries": queries,
    }


def _input_record(name: str, entity: str, entity_id: str, interval: dict, value) -> dict:
    return {
        "name": name,
        "entity": entity,
        "entity_id": entity_id,
        "interval": interval,
        "value": _scalar_value(value),
    }


def _scalar_value(value) -> dict:
    """Encode a numpy / python scalar as the engine's tagged scalar JSON."""
    # Order matters: bool must come before int (numpy's bool is also int).
    if isinstance(value, (np.bool_, bool)):
        return {"kind": "bool", "value": bool(value)}
    if isinstance(value, (np.integer,)):
        return {"kind": "integer", "value": int(value)}
    if isinstance(value, int):
        return {"kind": "integer", "value": value}
    if isinstance(value, np.datetime64):
        return {"kind": "date", "value": str(value.astype("datetime64[D]"))}
    if isinstance(value, date):
        return {"kind": "date", "value": value.isoformat()}
    if isinstance(value, (np.floating, float)):
        return {"kind": "decimal", "value": f"{float(value):.6f}"}
    if isinstance(value, str):
        # Defaults like "2026-01-01" come through as strings; keep the kind
        # consistent with the slot dtype when we know it. For now treat any
        # 10-char ISO date as date, otherwise text.
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            return {"kind": "date", "value": value}
        return {"kind": "text", "value": value}
    raise TypeError(f"unsupported scalar type {type(value)} ({value!r})")


def _collect_outputs(
    response: dict,
    n_households: int,
    output_names: tuple[str, ...],
    id_to_name: dict[str, str],
) -> dict[str, np.ndarray]:
    arrays = {name: np.zeros(n_households, dtype=np.float64) for name in output_names}

    for query_result in response["results"]:
        entity_id = query_result["entity_id"]
        if not entity_id.startswith("h"):
            continue
        h_idx = int(entity_id[1:])
        for key, out in query_result["outputs"].items():
            # Engine echoes the absolute id back as the dict key.
            name = id_to_name.get(key, key)
            if name not in arrays:
                continue
            if out["kind"] == "scalar":
                v = out["value"]
                if v["kind"] in ("decimal", "integer"):
                    arrays[name][h_idx] = float(v["value"])
                elif v["kind"] == "bool":
                    arrays[name][h_idx] = 1.0 if v["value"] else 0.0
            elif out["kind"] == "judgment":
                arrays[name][h_idx] = {"holds": 1.0, "not_holds": 0.0, "undetermined": -1.0}[
                    out["outcome"]
                ]
    return arrays


# --- YAML patching -----------------------------------------------------------

def _patch_yaml(path: Path, override: ParameterOverride) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    with path.open() as f:
        doc = yaml.load(f)

    rule = next((r for r in doc.get("rules", []) if r.get("name") == override.parameter), None)
    if rule is None:
        raise KeyError(f"parameter {override.parameter!r} not in {path}")
    versions = rule.get("versions") or []
    if not versions:
        raise ValueError(f"parameter {override.parameter!r} has no versions")
    version = versions[0]

    if override.patch_kind == "scale_values":
        if "values" not in version:
            raise ValueError(f"{override.parameter} has no values to scale")
        for k in version["values"]:
            scaled = version["values"][k] * (override.multiplier or 1.0)
            # Preserve rate-style decimals, round money-style integers.
            # Heuristic: if the original was an int and the scale stays
            # whole-cent-ish, round to int; otherwise keep float precision.
            original = version["values"][k]
            if isinstance(original, int) and abs(scaled - round(scaled)) < 0.5:
                version["values"][k] = round(scaled)
            else:
                version["values"][k] = round(scaled, 6)
    elif override.patch_kind == "set_values":
        version.setdefault("values", {})
        for k, v in (override.values or {}).items():
            version["values"][k] = v
    elif override.patch_kind == "scale_formula":
        try:
            n = float(str(version["formula"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"scale_formula needs a numeric-literal formula on {override.parameter}"
            ) from exc
        version["formula"] = str(round(n * (override.multiplier or 1.0), 2))
    elif override.patch_kind == "set_formula":
        version["formula"] = override.formula

    with path.open("w") as f:
        yaml.dump(doc, f)
