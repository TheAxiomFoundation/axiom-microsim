"""§24 CTC: the measure, the request wiring, and the phase-out arithmetic.

Issue #11: the server summed ``ctc_maximum_before_phase_out_under_subsection_h``
— a *pre*-phase-out quantity — under an "Annual CTC cost" label, while the
UI exposed a phase-out-threshold slider and the PolicyEngine comparison
requested a *post*-phase-out variable. Dragging the slider moved PE's
number and not Axiom's. These tests pin all three sides of that back
together.

The engine-backed cases self-skip without a local rules tree + binary
(``bash scripts/setup_engine.sh``); the wiring cases always run.
"""

from __future__ import annotations

import numpy as np
import pytest

from axiom_microsim.data.ecps_loader import TaxUnitBatch, tax_unit_agi
from axiom_microsim.project.federal_ctc import (
    CTC_PARENT,
    CTC_SUBSECTION_H,
    project,
)
from axiom_microsim.run import microsim as M
from axiom_microsim.server import MEASURES


# --- Fixtures ----------------------------------------------------------------

# (label, [(age, employment_income), ...], expected §24 credit for 2026)
#
# Ages drive the projection's role heuristic: oldest = head, second-oldest
# adult in a 2-adult unit = spouse, everyone else = dependent. Under 17 →
# qualifying child ($2,200); 17–23 → other dependent ($500).
CASES: list[tuple[str, list[tuple[int, float]], float]] = [
    (
        "joint, 2 children, AGI 100k — under the threshold",
        [(40, 100_000.0), (38, 0.0), (10, 0.0), (8, 0.0)],
        4_400.0,
    ),
    (
        "joint, 2 children, AGI 450k — 50k over → -$2,500",
        [(40, 450_000.0), (38, 0.0), (10, 0.0), (8, 0.0)],
        1_900.0,
    ),
    (
        "head, 1 child + 1 other dependent, AGI 210k",
        [(40, 210_000.0), (10, 0.0), (17, 0.0)],
        2_200.0,
    ),
    (
        "joint, 2 children, AGI 500k — phased out entirely",
        [(40, 500_000.0), (38, 0.0), (10, 0.0), (8, 0.0)],
        0.0,
    ),
    (
        "joint, 2 children, AGI 400,001 — 'or fraction thereof'",
        [(40, 400_001.0), (38, 0.0), (10, 0.0), (8, 0.0)],
        4_350.0,
    ),
]


def _batch() -> TaxUnitBatch:
    ages: list[int] = []
    incomes: list[float] = []
    tax_unit_index: list[int] = []
    for tu_idx, (_label, persons, _expected) in enumerate(CASES):
        for age, income in persons:
            ages.append(age)
            incomes.append(income)
            tax_unit_index.append(tu_idx)
    return TaxUnitBatch(
        state="US",
        year="2024",
        n_persons=len(ages),
        n_tax_units=len(CASES),
        person_tax_unit_index=np.array(tax_unit_index, dtype=np.int64),
        tax_unit_weight=np.ones(len(CASES), dtype=np.float64),
        person_columns={
            "age": np.array(ages, dtype=np.int64),
            "employment_income_before_lsr": np.array(incomes, dtype=np.float64),
        },
    )


def _engine_available() -> bool:
    return M.ENGINE_BIN.exists() and (M.RULES_US_DIR / M.FED_CTC_PROGRAM_REL).exists()


requires_engine = pytest.mark.skipif(
    not _engine_available(),
    reason="no local engine binary + rules tree (run `bash scripts/setup_engine.sh`)",
)


# --- The measure the server reports ------------------------------------------


def test_headline_output_is_the_post_phase_out_credit() -> None:
    """The regression guard for #11: never headline a pre-phase-out output."""
    output_id = M.FED_CTC_OUTPUT_IDS[M.FED_CTC_HEADLINE_OUTPUT]
    assert output_id == "us:statutes/26/24#ctc_before_advance_payments"
    assert "before_phase_out" not in output_id


def test_measure_registry_matches_the_output_the_server_sums() -> None:
    measure = MEASURES["federal-ctc"]
    assert measure.output_id == M.FED_CTC_OUTPUT_IDS[M.FED_CTC_HEADLINE_OUTPUT]
    # The label must not promise a final cost the measure doesn't compute.
    assert "phase-out" in measure.label
    assert measure.pe_variable == "ctc"


def test_ctc_program_is_the_parent_module_that_owns_the_phase_out() -> None:
    assert M.FED_CTC_PROGRAM_REL == "statutes/26/24.yaml"
    # Both relations, or `count_where` sees no children.
    assert M.FED_CTC_RELATION_NAMES == (
        "us:statutes/26/24/h#relation.dependent_of_tax_unit",
        "us:statutes/26/24#relation.ctc_qualifying_child_of_tax_unit",
    )


# --- Projection + request wiring (no engine needed) --------------------------


def test_projection_feeds_the_phase_out_its_agi_and_filing_status() -> None:
    batch = _batch()
    projection = project(batch, period_year=2026)

    agi = projection.tax_unit_inputs[f"{CTC_PARENT}#input.adjusted_gross_income"]
    np.testing.assert_allclose(agi, tax_unit_agi(batch))
    assert agi[1] == pytest.approx(450_000.0)

    # 2 adults → joint (threshold $400k); 1 adult with dependents → head of
    # household (threshold $200k).
    filing_status = projection.tax_unit_inputs[f"{CTC_PARENT}#input.filing_status"]
    assert filing_status[0] == 1
    assert filing_status[2] == 3

    assert projection.tax_unit_inputs[f"{CTC_PARENT}#input.taxable_year_begins_after_2017"].all()
    assert (projection.tax_unit_inputs[f"{CTC_PARENT}#input.taxable_year_months"] == 12).all()


def test_request_carries_only_dependent_person_rows_and_both_relations() -> None:
    import orjson

    batch = _batch()
    projection = project(batch, period_year=2026)
    request = orjson.loads(
        M._build_ctc_request_bytes(projection, 2026, ("ctc_before_advance_payments",))
    )

    person_ids = {r["entity_id"] for r in request["dataset"]["inputs"] if r["entity"] == "Person"}
    expected_dependents = int(projection.person_in_scope.sum())
    assert len(person_ids) == expected_dependents
    assert expected_dependents == 10  # two claimed dependents in each of the 5 units

    # Every in-scope person is related to their tax unit under both names.
    relation_names = [r["name"] for r in request["dataset"]["relations"]]
    for name in M.FED_CTC_RELATION_NAMES:
        assert relation_names.count(name) == expected_dependents

    # The tax-unit facts person-scoped rules read ride along on person rows;
    # the rest stay TaxUnit-only.
    person_facts = {r["name"] for r in request["dataset"]["inputs"] if r["entity"] == "Person"}
    assert f"{CTC_SUBSECTION_H}#input.taxpayer_or_spouse_ssn_included_on_return" in person_facts
    assert f"{CTC_PARENT}#input.adjusted_gross_income" not in person_facts


def test_chunks_partition_the_population_exactly(monkeypatch) -> None:
    monkeypatch.setattr(M, "CTC_CHUNK_TAX_UNITS", 2)
    chunks = M._ctc_chunks(5)
    assert chunks == [(0, 2), (2, 4), (4, 5)]
    assert M._ctc_chunks(0) == [(0, 0)]

    import orjson

    batch = _batch()
    projection = project(batch, period_year=2026)
    queried: list[str] = []
    persons: list[str] = []
    for tu_range in chunks:
        request = orjson.loads(
            M._build_ctc_request_bytes(
                projection, 2026, ("ctc_before_advance_payments",), tu_range=tu_range
            )
        )
        queried += [q["entity_id"] for q in request["queries"]]
        persons += sorted(
            {r["entity_id"] for r in request["dataset"]["inputs"] if r["entity"] == "Person"}
        )
    # Every tax unit queried once, every dependent asserted once.
    assert sorted(queried) == sorted(f"tu{i}" for i in range(5))
    assert len(persons) == len(set(persons)) == int(projection.person_in_scope.sum())


# --- Caching + artifact staleness --------------------------------------------


def test_request_cache_evicts_on_the_byte_budget() -> None:
    cache = M._BoundedRequestCache(max_entries=100, max_bytes=100)
    cache[("a",)] = b"x" * 60
    cache[("b",)] = b"y" * 30
    assert len(cache) == 2 and cache.nbytes == 90
    cache[("c",)] = b"z" * 30  # 120 > 100 → oldest goes
    assert ("a",) not in cache
    assert len(cache) == 2 and cache.nbytes == 60

    # An entry bigger than the whole budget is kept, but alone.
    cache[("big",)] = b"q" * 500
    assert list(cache._d) == [("big",)]


def test_stale_artifact_is_detected_by_the_output_it_lacks(tmp_path) -> None:
    import orjson

    headline = M.FED_CTC_OUTPUT_IDS[M.FED_CTC_HEADLINE_OUTPUT]
    stale = tmp_path / "federal-ctc.compiled.json"
    stale.write_bytes(
        orjson.dumps(
            {
                "program": {
                    "derived": [
                        {
                            "id": "us:statutes/26/24/h#ctc_maximum_before_phase_out_under_subsection_h"
                        }
                    ]
                }
            }
        )
    )
    assert not M._artifact_provides(stale, headline)

    fresh = tmp_path / "fresh.compiled.json"
    fresh.write_bytes(orjson.dumps({"program": {"derived": [{"id": headline}]}}))
    assert M._artifact_provides(fresh, headline)

    assert not M._artifact_provides(tmp_path / "missing.json", headline)


# --- End to end through the engine -------------------------------------------


@requires_engine
def test_credit_after_phase_out_matches_the_statute() -> None:
    result = M.run_federal_ctc(_batch(), period_year=2026)
    credit = result.outputs["ctc_before_advance_payments"]
    maximum = result.outputs["ctc_maximum_before_phaseout"]

    for idx, (label, _persons, expected) in enumerate(CASES):
        assert credit[idx] == pytest.approx(expected), label

    # The old headline is still available, and still ignores income.
    assert maximum[0] == pytest.approx(4_400.0)
    assert maximum[1] == pytest.approx(4_400.0)
    assert maximum[3] == pytest.approx(4_400.0)


@requires_engine
def test_phase_out_threshold_reform_moves_the_headline() -> None:
    """The user-visible symptom of #11: this delta used to be exactly zero."""
    batch = _batch()
    baseline = M.run_federal_ctc(batch, period_year=2026)
    reform = M.run_federal_ctc(
        batch,
        period_year=2026,
        overrides=[
            M.ParameterOverride(
                repo="rules-us",
                file_relative="statutes/26/24/h.yaml",
                parameter="ctc_joint_phase_out_threshold_under_subsection_h",
                patch_kind="set_formula",
                formula="100000",
            )
        ],
    )

    base_credit = baseline.outputs["ctc_before_advance_payments"]
    reform_credit = reform.outputs["ctc_before_advance_payments"]
    # Joint filer at $450k: threshold $400k → $100k costs 350 increments × $50,
    # which wipes out the whole $4,400 maximum.
    assert base_credit[1] == pytest.approx(1_900.0)
    assert reform_credit[1] == pytest.approx(0.0)
    # The under-threshold unit at $100k is untouched by a $100k threshold.
    assert reform_credit[0] == pytest.approx(base_credit[0])
    # The pre-phase-out maximum is what used to be reported: unmoved.
    np.testing.assert_allclose(
        baseline.outputs["ctc_maximum_before_phaseout"],
        reform.outputs["ctc_maximum_before_phaseout"],
    )


@requires_engine
def test_chunking_does_not_change_any_number(monkeypatch) -> None:
    batch = _batch()
    whole = M.run_federal_ctc(batch, period_year=2026)
    monkeypatch.setattr(M, "CTC_CHUNK_TAX_UNITS", 2)
    chunked = M.run_federal_ctc(batch, period_year=2026)
    for name, values in whole.outputs.items():
        np.testing.assert_allclose(values, chunked.outputs[name], err_msg=name)
