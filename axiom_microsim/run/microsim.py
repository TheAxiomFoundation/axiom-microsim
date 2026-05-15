"""Run CO SNAP over an :class:`EcpsBatch` via the dense engine.

Two execution paths:

1. **Baseline.** Load the prebuilt ``co-snap.compiled.json`` artifact,
   project the ECPS batch, call ``CompiledDenseProgram.execute``.
2. **Reform.** Patch the rulespec tree on disk (``patch_rulespec``),
   shell out to the engine binary to recompile (~70 ms), then load the
   patched artifact and execute.

Both return :class:`MicrosimResult` with weighted output arrays so the
aggregation layer doesn't need to know which path produced them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from ruamel.yaml import YAML

from axiom_rules_engine.dense import CompiledDenseProgram, DenseRelationBatch

from ..data.ecps_loader import EcpsBatch
from ..project.co_snap import CoSnapProjection, project as project_co_snap


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
CO_SNAP_BASELINE_ARTIFACT = ARTIFACTS_DIR / "co-snap.compiled.json"

CO_SNAP_RELATION_NAME = "us:statutes/7/2012/j#relation.member_of_household"


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
    """Engine output for one execution.

    ``outputs[name]`` is an ndarray of length ``n_households`` (or
    ``n_persons`` for person-level outputs).
    """

    program: str
    state: str
    period_year: int
    n_households: int
    n_persons: int
    household_weight: np.ndarray
    outputs: dict[str, np.ndarray]


# --- Public entry points -----------------------------------------------------

DEFAULT_OUTPUTS: tuple[str, ...] = (
    "snap_allotment",
    "snap_regular_month_allotment",
    "snap_maximum_allotment",
    "snap_net_income_for_allotment",
    "snap_income_eligible",
)


def run_co_snap(
    batch: EcpsBatch,
    *,
    period_year: int = 2026,
    overrides: list[ParameterOverride] | None = None,
    outputs: tuple[str, ...] = DEFAULT_OUTPUTS,
) -> MicrosimResult:
    """Run CO SNAP over a state-filtered ECPS batch.

    With no overrides, the prebuilt baseline artifact is used. With
    overrides, the rulespec is patched and recompiled per call.
    """
    projection = project_co_snap(batch, period_year=period_year)
    artifact_path = (
        CO_SNAP_BASELINE_ARTIFACT if not overrides else _compile_with_overrides(overrides)
    )
    try:
        result = _execute(projection, artifact_path, period_year=period_year, outputs=outputs)
    finally:
        if overrides and artifact_path != CO_SNAP_BASELINE_ARTIFACT:
            artifact_path.unlink(missing_ok=True)

    return MicrosimResult(
        program="co-snap",
        state=batch.state,
        period_year=period_year,
        n_households=batch.n_households,
        n_persons=batch.n_persons,
        household_weight=batch.household_weight,
        outputs=result,
    )


# --- Internals ---------------------------------------------------------------

def _execute(
    projection: CoSnapProjection,
    artifact_path: Path,
    *,
    period_year: int,
    outputs: tuple[str, ...],
) -> dict[str, np.ndarray]:
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"Compiled CO SNAP artifact not found at {artifact_path}. "
            f"Run scripts/compile_programs.sh first."
        )
    program = CompiledDenseProgram.from_file(str(artifact_path), entity="Household")

    relations = {
        CO_SNAP_RELATION_NAME: DenseRelationBatch(
            offsets=projection.relation_offsets,
            inputs=projection.person_inputs,
        )
    }
    raw = program.execute(
        period_kind="year",
        start=f"{period_year}-01",
        end=f"{period_year}-12",
        inputs=projection.household_inputs,
        relations=relations,
        outputs=list(outputs),
    )
    # The native binding returns a dict-of-list / dict-of-DenseOutputValue.
    # Normalise to numpy arrays.
    return {name: _to_numpy(values) for name, values in raw.get("outputs", raw).items()}


def _to_numpy(value: object) -> np.ndarray:
    if isinstance(value, np.ndarray):
        return value
    if isinstance(value, list):
        return np.asarray(value)
    if isinstance(value, dict):
        # DenseOutputValue may serialise as {"values": [...], "dtype": "..."}
        if "values" in value:
            return np.asarray(value["values"])
    raise TypeError(f"unexpected dense output type: {type(value)}")


def _compile_with_overrides(overrides: list[ParameterOverride]) -> Path:
    """Patch rulespec tree, shell out to the engine to recompile, return path."""
    if not RULES_US_DIR.exists() or not RULES_US_CO_DIR.exists():
        raise FileNotFoundError(
            f"Rulespec dirs missing — expected {RULES_US_DIR} and {RULES_US_CO_DIR}. "
            "Run scripts/compile_programs.sh once to clone them."
        )

    scratch = Path(tempfile.mkdtemp(prefix="axiom-microsim-"))
    try:
        dst_us = scratch / "rules-us"
        dst_us_co = scratch / "rules-us-co"
        # symlinks=False so writes don't pierce the source tree.
        shutil.copytree(RULES_US_DIR, dst_us, symlinks=False)
        shutil.copytree(RULES_US_CO_DIR, dst_us_co, symlinks=False)

        for ov in overrides:
            file_path = (dst_us if ov.repo == "rules-us" else dst_us_co) / ov.file_relative
            _patch_yaml(file_path, ov)

        program_path = dst_us_co / CO_SNAP_PROGRAM_REL
        out = ARTIFACTS_DIR / f".tmp-{scratch.name}.compiled.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(ENGINE_BIN), "compile", "--program", str(program_path), "--output", str(out)],
            check=True,
            capture_output=True,
        )
        return out
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _patch_yaml(path: Path, override: ParameterOverride) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    with path.open() as f:
        doc = yaml.load(f)

    rules = doc.get("rules", [])
    rule = next((r for r in rules if r.get("name") == override.parameter), None)
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
            version["values"][k] = round(version["values"][k] * (override.multiplier or 1.0))
    elif override.patch_kind == "set_values":
        version.setdefault("values", {})
        for k, v in (override.values or {}).items():
            version["values"][k] = v
    elif override.patch_kind == "scale_formula":
        if "formula" not in version:
            raise ValueError(f"{override.parameter} has no formula to scale")
        try:
            n = float(str(version["formula"]))
        except ValueError as exc:
            raise ValueError(
                f"scale_formula only supports numeric literals; "
                f"{override.parameter} = {version['formula']!r}"
            ) from exc
        version["formula"] = str(round(n * (override.multiplier or 1.0), 2))
    elif override.patch_kind == "set_formula":
        version["formula"] = override.formula

    with path.open("w") as f:
        yaml.dump(doc, f)
