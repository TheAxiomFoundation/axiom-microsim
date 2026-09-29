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

import os
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import orjson
from ruamel.yaml import YAML

from ..data.ecps_loader import EcpsBatch, TaxUnitBatch
from ..project.co_snap import CoSnapProjection, project as project_co_snap
from ..project.federal_ctc import FedCtcProjection, project as project_federal_ctc
from ..project.federal_income_tax import (
    FedIncomeTaxProjection,
    project as project_federal_income_tax,
)

# --- Bounded LRU for request-bytes caches -----------------------------------
#
# Each cache entry is one encoded engine request — ~25 MB per CTC chunk
# (~450 MB for all of nationwide CTC), ~41 MB for CO SNAP CO. Counting
# entries alone doesn't bound anything useful when entries differ by 20×,
# so the cache carries a byte budget too: hitting every state for every
# program can't grow past REQUEST_CACHE_MAX_BYTES per cache of the Modal
# container's 8 GB, whatever mix of programs got there first. The entry cap
# is generous because a nationwide CTC run alone is ~18 chunk entries.

REQUEST_CACHE_MAX_ENTRIES = 64
REQUEST_CACHE_MAX_BYTES = 750_000_000


class _BoundedRequestCache:
    """Tiny LRU keyed by tuple → bytes, bounded by count *and* bytes.

    A single entry over the byte budget is kept (it is already resident;
    evicting it would only force a rebuild of the same bytes), but it
    evicts everything else.
    """

    def __init__(
        self,
        max_entries: int = REQUEST_CACHE_MAX_ENTRIES,
        max_bytes: int = REQUEST_CACHE_MAX_BYTES,
    ) -> None:
        self._max = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._d: OrderedDict[tuple, bytes] = OrderedDict()

    def get(self, key: tuple) -> bytes | None:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def __contains__(self, key: tuple) -> bool:
        return key in self._d

    def __getitem__(self, key: tuple) -> bytes:
        v = self._d[key]
        self._d.move_to_end(key)
        return v

    def __setitem__(self, key: tuple, value: bytes) -> None:
        existing = self._d.get(key)
        if existing is not None:
            self._bytes -= len(existing)
            self._d.move_to_end(key)
        self._d[key] = value
        self._bytes += len(value)
        while len(self._d) > 1 and (len(self._d) > self._max or self._bytes > self._max_bytes):
            _, evicted = self._d.popitem(last=False)
            self._bytes -= len(evicted)

    def __len__(self) -> int:
        return len(self._d)

    @property
    def nbytes(self) -> int:
        return self._bytes


# --- Locations ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = Path(os.environ.get("AXIOM_ARTIFACTS_DIR", str(ROOT / "engine" / "artifacts")))
RULES_US_DIR = Path(os.environ.get("AXIOM_RULES_US_DIR", str(ROOT / "engine" / "rules-us")))
RULES_US_CO_DIR = Path(
    os.environ.get("AXIOM_RULES_US_CO_DIR", str(ROOT / "engine" / "rules-us-co"))
)
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

# §24 Child Tax Credit — TaxUnit-rooted, year period.
#
# The program is the *parent* §24 module, which imports §24(h)'s post-2017
# amounts and thresholds and applies the §24(b)(1) phase-out to them. Running
# h.yaml alone would stop at the maximum before phase-out — the measure that
# ignores the phase-out threshold lever entirely (issue #11).
FED_CTC_PROGRAM_REL = "statutes/26/24.yaml"
FED_CTC_RELATION_NAMES: tuple[str, ...] = (
    "us:statutes/26/24/h#relation.dependent_of_tax_unit",
    "us:statutes/26/24#relation.ctc_qualifying_child_of_tax_unit",
)

FED_CTC_OUTPUT_IDS: dict[str, str] = {
    # The headline measure: §24 credit after the §24(b)(1) income phase-out,
    # before the §26(a) liability limitation and the §24(d) refundable split.
    "ctc_before_advance_payments": "us:statutes/26/24#ctc_before_advance_payments",
    "ctc_maximum_before_phaseout": "us:statutes/26/24#ctc_maximum_before_phaseout",
    "ctc_phaseout_amount": "us:statutes/26/24#ctc_phaseout_amount",
    "ctc_phaseout_threshold": "us:statutes/26/24#ctc_phaseout_threshold",
    "ctc_qualifying_children_under_subsection_h": "us:statutes/26/24/h#ctc_qualifying_children_under_subsection_h",
    "ctc_other_dependents_under_subsection_h": "us:statutes/26/24/h#ctc_other_dependents_under_subsection_h",
    "ctc_refundable_maximum_under_subsection_h": "us:statutes/26/24/h#ctc_refundable_maximum_under_subsection_h",
}
FED_CTC_DEFAULT_OUTPUTS: tuple[str, ...] = tuple(FED_CTC_OUTPUT_IDS)

# The output the server aggregates and the UI headlines. Named once, here,
# so the number, the label, and the artifact staleness check can't drift
# apart (issue #11: the server summed a pre-phase-out output under a
# "final cost" label for the whole of v1).
FED_CTC_HEADLINE_OUTPUT = "ctc_before_advance_payments"

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

CO_SNAP_EXCESS_SHELTER_OUTPUT = "us-co:regulations/10-ccr-2506-1/4.407.3#excess_shelter_deduction"
FED_SNAP_EXCESS_SHELTER_INPUT = "us:statutes/7/2014/e/6/A#input.snap_excess_shelter_deduction"


def run_co_snap(
    batch: EcpsBatch,
    *,
    period_year: int = 2026,
    overrides: list[ParameterOverride] | None = None,
    outputs: tuple[str, ...] = DEFAULT_OUTPUTS,
) -> MicrosimResult:
    projection = project_co_snap(batch, period_year=period_year)
    artifact_path, scratch = _artifact_for(overrides)
    cache_key = ("co-snap", batch.state, period_year, outputs)
    try:
        out = _execute_compiled(
            projection,
            artifact_path,
            period_year,
            outputs,
            cache_key=cache_key,
        )
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


def run_federal_ctc(
    batch: TaxUnitBatch,
    *,
    period_year: int = 2026,
    overrides: list[ParameterOverride] | None = None,
    outputs: tuple[str, ...] = FED_CTC_DEFAULT_OUTPUTS,
) -> "MicrosimResult":
    """Execute the §24 CTC RuleSpec program over an ECPS tax-unit batch.

    All §24 computation — including the §24(b)(1) phase-out — lives in
    rules-us/statutes/26/24.yaml and the §24(h) module it imports; this
    function only orchestrates: project → compile → run-compiled → decode.
    """
    projection = project_federal_ctc(batch, period_year=period_year)
    artifact_path, scratch = _ctc_artifact_for(overrides)
    cache_key = ("federal-ctc", batch.state, period_year, outputs)
    try:
        out = _execute_ctc(
            projection,
            artifact_path,
            period_year,
            outputs,
            cache_key=cache_key,
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
            artifact_path.unlink(missing_ok=True)

    return MicrosimResult(
        program="federal-ctc",
        state=batch.state,
        period_year=period_year,
        n_households=projection.n_tax_units,
        n_persons=batch.n_persons,
        household_weight=batch.tax_unit_weight,
        outputs=out,
    )


def run_federal_income_tax(
    batch: TaxUnitBatch,
    *,
    period_year: int = 2026,
    overrides: list[ParameterOverride] | None = None,
    outputs: tuple[str, ...] = FED_INCOME_TAX_DEFAULT_OUTPUTS,
) -> "MicrosimResult":
    """Run §1(j) federal income tax over an ECPS tax-unit batch.

    Uses the dense in-process engine path — `CompiledDenseProgram` reads
    numpy columns directly, no JSON, no subprocess. ~50× faster than
    the JSON `run-compiled` path for §1(j); see PERFORMANCE.md.
    """
    projection = project_federal_income_tax(batch, period_year=period_year)

    # Reform: patch YAML in a scratch tree, dense-compile from the
    # scratch program. Dense compile is ~5 ms. Baseline compiles from a
    # persistent staged tree so `us:` ids/imports resolve (see
    # _staged_rules_trees).
    if overrides:
        program_path, scratch = _patched_program_for_fed_income_tax(overrides)
    else:
        program_path, scratch = _baseline_rules_us_root() / FED_INCOME_TAX_PROGRAM_REL, None

    try:
        out = _execute_fed_income_tax_dense(projection, program_path, period_year, outputs)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    return MicrosimResult(
        program="federal-income-tax",
        state=batch.state,
        period_year=period_year,
        n_households=projection.n_tax_units,
        n_persons=batch.n_persons,
        household_weight=batch.tax_unit_weight,
        outputs=out,
    )


# --- Federal income tax (dense path) ----------------------------------------

FED_INCOME_TAX_BASELINE_PROGRAM = RULES_US_DIR / FED_INCOME_TAX_PROGRAM_REL


def _patched_program_for_fed_income_tax(
    overrides: list[ParameterOverride],
) -> tuple[Path, Path]:
    """Copy rules-us to a scratch tree, patch, return (program_yaml, scratch_root)."""
    scratch = _staged_rules_trees()
    dst = scratch / "rulespec-us"
    for ov in overrides:
        if ov.repo != "rules-us":
            raise ValueError(f"federal-income-tax overrides must target rules-us, got {ov.repo}")
        _patch_yaml(_resolve_override_target(dst, ov.file_relative), ov)
    return dst / FED_INCOME_TAX_PROGRAM_REL, scratch


def _execute_fed_income_tax_dense(
    projection: FedIncomeTaxProjection,
    program_yaml: Path,
    period_year: int,
    output_names: tuple[str, ...],
) -> dict[str, np.ndarray]:
    """In-process dense execution. No JSON, no subprocess."""
    from axiom_rules_engine.dense import CompiledDenseProgram

    program = CompiledDenseProgram.from_file(str(program_yaml), entity="TaxUnit")
    # The projection keys inputs by their full RuleSpec id; the dense
    # binding wants bare slot names.
    dense_inputs = {
        full_id.split("#input.", 1)[1]: column for full_id, column in projection.inputs.items()
    }
    raw = program.execute(
        period_kind="year",
        start=f"{period_year}-01-01",
        end=f"{period_year}-12-31",
        inputs=dense_inputs,
        outputs=list(output_names),
    )
    out_block = raw.get("outputs", raw)
    arrays: dict[str, np.ndarray] = {}
    for name in output_names:
        v = out_block.get(name)
        if v is None:
            arrays[name] = np.zeros(projection.n_tax_units, dtype=np.float64)
        else:
            arrays[name] = np.asarray(v, dtype=np.float64)
    return arrays


# --- Schema (input slots + defaults) ----------------------------------------


@dataclass
class _SlotSpec:
    name: str
    dtype: str  # "bool" | "integer" | "decimal" | "date"
    default: object


def _load_schema() -> tuple[list[_SlotSpec], list[_SlotSpec]]:
    if not CO_SNAP_BASE_SCHEMA.exists():
        raise FileNotFoundError(
            f"CO SNAP schema dump missing at {CO_SNAP_BASE_SCHEMA}. Copy it from "
            f"axiom-co-snap/engine/artifacts/co-snap-base.json."
        )
    schema = orjson.loads(CO_SNAP_BASE_SCHEMA.read_bytes())
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


def _staged_rules_trees(*, include_co: bool = False) -> Path:
    """Copy the rules checkouts into a scratch tree under canonical names.

    The engine derives canonical ``us:`` rule ids from a ``rulespec-``
    path component (after resolving symlinks), so compiling from a
    checkout named ``rules-us`` yields an artifact with no absolute
    ids — and every request keyed by legal id then fails. Staging under
    ``rulespec-us`` / ``rulespec-us-co`` keeps ids intact regardless of
    how the local checkout is named. Caller removes the scratch dir.
    """
    if not RULES_US_DIR.exists():
        raise FileNotFoundError(f"rules-us missing at {RULES_US_DIR}")
    if include_co and not RULES_US_CO_DIR.exists():
        raise FileNotFoundError(f"rules-us-co missing at {RULES_US_CO_DIR}")
    scratch = Path(tempfile.mkdtemp(prefix="axiom-microsim-rules-"))
    shutil.copytree(RULES_US_DIR, scratch / "rulespec-us", symlinks=False)
    if include_co:
        shutil.copytree(RULES_US_CO_DIR, scratch / "rulespec-us-co", symlinks=False)
    return scratch


_BASELINE_RULES_SCRATCH: Path | None = None


def _baseline_rules_us_root() -> Path:
    """A rules-us tree whose path contains a ``rulespec-`` component.

    Used for baseline compiles that read YAML directly (no patching).
    If the checkout already resolves to a canonical name (Modal), use it
    as-is; otherwise stage one persistent copy per process and reuse it.
    """
    global _BASELINE_RULES_SCRATCH
    if any(part.startswith("rulespec-") for part in RULES_US_DIR.resolve().parts):
        return RULES_US_DIR
    if _BASELINE_RULES_SCRATCH is None or not _BASELINE_RULES_SCRATCH.exists():
        _BASELINE_RULES_SCRATCH = _staged_rules_trees()
    return _BASELINE_RULES_SCRATCH / "rulespec-us"


def _artifact_for(overrides: list[ParameterOverride] | None) -> tuple[Path, Path | None]:
    """Return ``(artifact_path, scratch_to_clean_or_None)``."""
    if not overrides:
        if not CO_SNAP_BASELINE_ARTIFACT.exists():
            scratch = _staged_rules_trees(include_co=True)
            try:
                ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
                _compile(
                    scratch / "rulespec-us-co" / CO_SNAP_PROGRAM_REL, CO_SNAP_BASELINE_ARTIFACT
                )
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        return CO_SNAP_BASELINE_ARTIFACT, None

    scratch = _staged_rules_trees(include_co=True)
    dst_us = scratch / "rulespec-us"
    dst_us_co = scratch / "rulespec-us-co"
    for ov in overrides:
        target_root = dst_us if ov.repo == "rules-us" else dst_us_co
        _patch_yaml(_resolve_override_target(target_root, ov.file_relative), ov)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_DIR / f".tmp-{scratch.name}.compiled.json"
    _compile(dst_us_co / CO_SNAP_PROGRAM_REL, out)
    return out, scratch


def _artifact_provides(artifact_path: Path, output_id: str) -> bool:
    """True if a compiled artifact declares ``output_id`` as a derived rule.

    Cached baseline artifacts outlive the program they were compiled from
    (both the local `engine/artifacts` dir and the Modal image bake them).
    Checking for the output we're about to query turns a stale artifact
    into a recompile instead of a wrong headline number.
    """
    try:
        artifact = orjson.loads(artifact_path.read_bytes())
    except OSError, orjson.JSONDecodeError:
        return False
    derived = artifact.get("program", {}).get("derived", [])
    return any(rule.get("id") == output_id for rule in derived)


def _ctc_artifact_for(overrides: list[ParameterOverride] | None) -> tuple[Path, Path | None]:
    """Compile §24 (with optional reform overrides) and return artifact path."""
    headline_output = FED_CTC_OUTPUT_IDS[FED_CTC_HEADLINE_OUTPUT]
    if not overrides:
        baseline = ARTIFACTS_DIR / "federal-ctc.compiled.json"
        if not baseline.exists() or not _artifact_provides(baseline, headline_output):
            scratch = _staged_rules_trees()
            try:
                ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
                _compile(scratch / "rulespec-us" / FED_CTC_PROGRAM_REL, baseline)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        return baseline, None

    scratch = _staged_rules_trees()
    dst = scratch / "rulespec-us"
    for ov in overrides:
        if ov.repo != "rules-us":
            raise ValueError(f"federal-ctc overrides must target rules-us, got {ov.repo}")
        _patch_yaml(_resolve_override_target(dst, ov.file_relative), ov)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACTS_DIR / f".tmp-{scratch.name}.compiled.json"
    _compile(dst / FED_CTC_PROGRAM_REL, out)
    return out, scratch


_CTC_REQUEST_CACHE = _BoundedRequestCache()

# Tax units per engine invocation. §24 is computed entirely within a tax
# unit, so slicing the population is arithmetically identical to one big
# run (verified: identical $151.329B nationwide total at 5k / 10k / 20k /
# no chunking) — but the engine answers every query with a full dependency
# trace, ~5 KB per tax unit at this program size, and decoding a nationwide
# response in one piece peaked at 6.4 GB in an 8 GB container that also
# hosts PolicyEngine for /compare. At 5k the peak is 2.1 GB for the same
# wall clock, so this costs nothing but subprocess spawns.
CTC_CHUNK_TAX_UNITS = 5_000


def _ctc_chunks(n_tax_units: int) -> list[tuple[int, int]]:
    """Half-open [start, end) tax-unit ranges, in order."""
    return [
        (start, min(start + CTC_CHUNK_TAX_UNITS, n_tax_units))
        for start in range(0, max(n_tax_units, 1), CTC_CHUNK_TAX_UNITS)
    ]


def _build_ctc_request_bytes(
    projection: FedCtcProjection,
    period_year: int,
    output_names: tuple[str, ...],
    cache_key: tuple | None = None,
    tu_range: tuple[int, int] | None = None,
) -> bytes:
    """Build + encode one §24 request slice.

    Cached per (state, period, outputs, slice) — reform calls reuse it,
    since only the artifact path changes between baseline and reform, not
    the inputs.
    """
    if cache_key is not None:
        cached = _CTC_REQUEST_CACHE.get(cache_key)
        if cached is not None:
            return cached

    tu_start, tu_end = tu_range if tu_range is not None else (0, projection.n_tax_units)
    interval = {"start": f"{period_year}-01-01", "end": f"{period_year}-12-31"}
    period = {"period_kind": "tax_year", "start": interval["start"], "end": interval["end"]}
    output_ids = [FED_CTC_OUTPUT_IDS[n] for n in output_names]

    inputs: list[dict] = []
    relations: list[dict] = []
    queries: list[dict] = []

    for tu_idx in range(tu_start, tu_end):
        tu_id = f"tu{tu_idx}"
        for full_id, column in projection.tax_unit_inputs.items():
            inputs.append(
                {
                    "name": full_id,
                    "entity": "TaxUnit",
                    "entity_id": tu_id,
                    "interval": interval,
                    "value": _scalar_value(column[tu_idx]),
                }
            )
        queries.append({"entity_id": tu_id, "period": period, "outputs": output_ids})

    # Persons are sorted by tax unit, so this slice's persons are exactly
    # the offsets range of its tax units.
    person_start = int(projection.relation_offsets[tu_start])
    person_end = int(projection.relation_offsets[tu_end])
    pos_in_sorted = np.arange(person_start, person_end)
    tu_for_person = np.searchsorted(projection.relation_offsets, pos_in_sorted, side="right") - 1

    # Tax-unit facts read by person-scoped rules have to be asserted on the
    # person rows too. Only those — replicating all of them would multiply
    # the request by the number of tax-unit inputs (see PERFORMANCE.md, C).
    tax_unit_inputs_on_persons = [
        (full_id, column)
        for full_id, column in projection.tax_unit_inputs.items()
        if full_id in projection.tax_unit_inputs_on_persons
    ]

    for sorted_p_idx in range(person_start, person_end):
        # Persons who can't be claimed as a dependent carry all-false §24
        # flags: they never satisfy either `count_where` predicate and no
        # rule reads anything else about them. Sending their rows costs
        # ~15 records each and changes no output (regression-tested in
        # tests/test_federal_ctc_program.py).
        if not projection.person_in_scope[sorted_p_idx]:
            continue
        person_id = f"p{sorted_p_idx}"
        tu_idx = int(tu_for_person[sorted_p_idx - person_start])
        tu_id = f"tu{tu_idx}"
        for full_id, column in projection.person_inputs.items():
            inputs.append(
                {
                    "name": full_id,
                    "entity": "Person",
                    "entity_id": person_id,
                    "interval": interval,
                    "value": _scalar_value(column[sorted_p_idx]),
                }
            )
        for full_id, column in tax_unit_inputs_on_persons:
            inputs.append(
                {
                    "name": full_id,
                    "entity": "Person",
                    "entity_id": person_id,
                    "interval": interval,
                    "value": _scalar_value(column[tu_idx]),
                }
            )
        for relation_name in FED_CTC_RELATION_NAMES:
            relations.append(
                {
                    "name": relation_name,
                    "tuple": [person_id, tu_id],
                    "interval": interval,
                }
            )

    request = {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": relations},
        "queries": queries,
    }
    encoded = orjson.dumps(request)
    if cache_key is not None:
        _CTC_REQUEST_CACHE[cache_key] = encoded
    return encoded


def _execute_ctc(
    projection: FedCtcProjection,
    artifact_path: Path,
    period_year: int,
    output_names: tuple[str, ...],
    cache_key: tuple | None = None,
) -> dict[str, np.ndarray]:
    """Run the §24 program over the batch, one tax-unit chunk at a time."""
    id_to_name = {v: k for k, v in FED_CTC_OUTPUT_IDS.items()}
    arrays = {n: np.zeros(projection.n_tax_units, dtype=np.float64) for n in output_names}

    for tu_range in _ctc_chunks(projection.n_tax_units):
        chunk_key = None if cache_key is None else (*cache_key, tu_range)
        request_bytes = _build_ctc_request_bytes(
            projection, period_year, output_names, cache_key=chunk_key, tu_range=tu_range
        )
        proc = subprocess.run(
            [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
            input=request_bytes,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"engine failed:\n{proc.stderr.strip()[:1500]}")
        response = orjson.loads(proc.stdout)
        # Drop the raw bytes before walking the decoded response — for a
        # nationwide run each is hundreds of MB.
        proc = None

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
        response = None

    return arrays


def _fed_artifact_for(overrides: list[ParameterOverride] | None) -> tuple[Path, Path | None]:
    """Compile §1(j) (with optional reform overrides) and return artifact path."""
    if not overrides:
        baseline = ARTIFACTS_DIR / "federal-income-tax.compiled.json"
        if not baseline.exists():
            scratch = _staged_rules_trees()
            try:
                ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
                _compile(scratch / "rulespec-us" / FED_INCOME_TAX_PROGRAM_REL, baseline)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        return baseline, None

    # Reform: copy the rules-us tree, patch, recompile.
    scratch = _staged_rules_trees()
    dst = scratch / "rulespec-us"
    for ov in overrides:
        if ov.repo != "rules-us":
            raise ValueError(
                f"federal-income-tax reform overrides must target rules-us, got {ov.repo}"
            )
        _patch_yaml(_resolve_override_target(dst, ov.file_relative), ov)

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
            inputs.append(
                {
                    "name": full_input_id,
                    "entity": "TaxUnit",
                    "entity_id": tu_id,
                    "interval": interval,
                    "value": _scalar_value(column[tu_idx]),
                }
            )
        queries.append({"entity_id": tu_id, "period": period, "outputs": output_ids})

    request = {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": []},
        "queries": queries,
    }

    proc = subprocess.run(
        [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
        input=orjson.dumps(request),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"engine failed:\n{proc.stderr.strip()[:1500]}")
    response = orjson.loads(proc.stdout)

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
        check=True,
        capture_output=True,
    )


# --- Execute -----------------------------------------------------------------

_CO_SNAP_REQUEST_CACHE = _BoundedRequestCache()


def _execute_compiled(
    projection: CoSnapProjection,
    artifact_path: Path,
    period_year: int,
    output_names: tuple[str, ...],
    cache_key: tuple | None = None,
) -> dict[str, np.ndarray]:
    output_ids = [DEFAULT_OUTPUT_IDS[n] for n in output_names]
    request_bytes: bytes
    if cache_key is not None and cache_key in _CO_SNAP_REQUEST_CACHE:
        request_bytes = _CO_SNAP_REQUEST_CACHE[cache_key]
    else:
        request = _build_compiled_request(projection, period_year, output_ids)
        request_bytes = orjson.dumps(request)
        if cache_key is not None:
            _CO_SNAP_REQUEST_CACHE[cache_key] = request_bytes

    proc = subprocess.run(
        [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
        input=request_bytes,
        capture_output=True,
    )
    if proc.returncode != 0 and b"missing input `snap_excess_shelter_deduction`" in proc.stderr:
        request_bytes = _build_co_snap_shelter_bridge_request_bytes(
            projection, artifact_path, period_year, output_ids
        )
        if cache_key is not None:
            _CO_SNAP_REQUEST_CACHE[cache_key] = request_bytes
        proc = subprocess.run(
            [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
            input=request_bytes,
            capture_output=True,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"engine failed:\n{proc.stderr.strip()}")
    response = orjson.loads(proc.stdout)
    id_to_name = {DEFAULT_OUTPUT_IDS[n]: n for n in output_names}
    return _collect_outputs(response, projection.n_households, output_names, id_to_name)


def _build_co_snap_shelter_bridge_request_bytes(
    projection: CoSnapProjection,
    artifact_path: Path,
    period_year: int,
    output_ids: list[str],
) -> bytes:
    """Bind CO's shelter deduction into the imported federal SNAP input.

    The pinned rules-us module `7/2014/e/6/A` exposes
    `snap_excess_shelter_deduction` as a module-local input. Colorado computes
    the value as `excess_shelter_deduction`, so if the compiled artifact asks
    for the federal input we compute the Colorado output first and retry with
    the federal input populated.
    """
    bridge_request = _build_compiled_request(
        projection, period_year, [CO_SNAP_EXCESS_SHELTER_OUTPUT]
    )
    bridge_proc = subprocess.run(
        [str(ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
        input=orjson.dumps(bridge_request),
        capture_output=True,
    )
    if bridge_proc.returncode != 0:
        raise RuntimeError(
            f"engine failed while computing CO SNAP shelter bridge:\n{bridge_proc.stderr.strip()}"
        )
    bridge_response = orjson.loads(bridge_proc.stdout)
    shelter = _collect_outputs(
        bridge_response,
        projection.n_households,
        ("excess_shelter_deduction",),
        {CO_SNAP_EXCESS_SHELTER_OUTPUT: "excess_shelter_deduction"},
    )["excess_shelter_deduction"]
    request = _build_compiled_request(
        projection,
        period_year,
        output_ids,
        extra_household_inputs={FED_SNAP_EXCESS_SHELTER_INPUT: shelter},
    )
    return orjson.dumps(request)


def _build_compiled_request(
    proj: CoSnapProjection,
    period_year: int,
    output_ids: list[str],
    extra_household_inputs: dict[str, np.ndarray] | None = None,
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
        for input_id, values in (extra_household_inputs or {}).items():
            inputs.append(_input_record(input_id, "Household", hh_id, interval, values[h_idx]))
        queries.append(
            {
                "entity_id": hh_id,
                "period": period,
                "outputs": output_ids,
            }
        )

    # Person-level inputs + member_of_household relations.
    person_to_hh = (
        np.searchsorted(proj.relation_offsets, np.arange(proj.n_persons), side="right") - 1
    )
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
        relations.append(
            {
                "name": CO_SNAP_RELATION_NAME,
                "tuple": [person_id, hh_id],
                "interval": interval,
            }
        )

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


def _resolve_override_target(root: Path, file_relative: str) -> Path:
    """Resolve ``file_relative`` against ``root``, refusing to leave it.

    ``file_relative`` arrives from the HTTP surface, so it is untrusted. Plain
    ``root / file_relative`` is not safe: an absolute path silently discards
    ``root`` entirely, and ``..`` segments walk out of it. Resolve the joined
    path and require the result to stay under the resolved root, which also
    catches escapes through a symlinked rule file.
    """
    if not file_relative or Path(file_relative).is_absolute():
        raise ValueError(f"file_relative must be a relative path, got {file_relative!r}")
    if ".." in Path(file_relative).parts:
        raise ValueError(f"file_relative must not contain '..', got {file_relative!r}")

    resolved_root = root.resolve()
    target = (resolved_root / file_relative).resolve()
    if not target.is_relative_to(resolved_root):
        raise ValueError(f"file_relative escapes the rules root: {file_relative!r}")
    return target


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
