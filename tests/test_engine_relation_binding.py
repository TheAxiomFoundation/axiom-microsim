"""The producer's §24(h) requests against the real engine binary (issue #23).

Runs the production request builder and ``_execute_ctc`` against
``$AXIOM_RULES_ENGINE_BINARY``, the binary the app itself shells out to, on
the committed fixture artifacts. Self-skips when the binary is absent, as in
CI. Assertions that need strict binding (axiom-rules-engine#190) skip on an
engine whose responses do not report ``metadata.relation_binding``.

The owner-first reference is rulespec-us ``statutes/26/24/h.test.yaml`` case
``joint_return_aggregate_credit_and_refundable_cap`` at ``d9a03f17``, the
source both CTC fixtures were compiled from.
"""

from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

import numpy as np
import orjson
import pytest
from hypothesis import assume, event, given, settings
from hypothesis import strategies as st

from _artifacts import (
    CTC_TYPED,
    CTC_TYPED_LEGACY_SLOTS,
    CTC_UNTYPED,
    load_artifact,
    write_artifact,
)
from axiom_microsim.data.ecps_loader import TaxUnitBatch
from axiom_microsim.project.federal_ctc import FedCtcProjection, project as project_ctc
from axiom_microsim.run import microsim as M
from axiom_microsim.run.microsim import FED_CTC_DEFAULT_OUTPUTS, _build_ctc_request_bytes
from axiom_microsim.run.relation_layout import (
    RelationLayout,
    _executable_slot_kinds,
    relation_layout,
)

pytestmark = pytest.mark.skipif(
    not M.ENGINE_BIN.exists(),
    reason=f"engine binary not found at {M.ENGINE_BIN}; set AXIOM_RULES_ENGINE_BINARY",
)

H = "us:statutes/26/24/h#input."
QUALIFYING_CHILDREN = "ctc_qualifying_children_under_subsection_h"
OTHER_DEPENDENTS = "ctc_other_dependents_under_subsection_h"
MAXIMUM = "ctc_maximum_before_phase_out_under_subsection_h"

# Expected outputs of the companion case (h.test.yaml at d9a03f17).
COMPANION_REFERENCE = {
    QUALIFYING_CHILDREN: 1,
    OTHER_DEPENDENTS: 2,
    "ctc_phase_out_threshold_under_subsection_h": 400_000,
    MAXIMUM: 3_200,
    "ctc_refundable_maximum_under_subsection_h": 1_400,
}

# Fixture name → (declares slot kinds, loader); see fixtures/artifacts/README.md.
ARTIFACTS = {
    "untyped-v0.1.1": (False, lambda: load_artifact(CTC_UNTYPED)),
    "typed-post-179": (True, lambda: load_artifact(CTC_TYPED)),
    "typed-legacy-slots": (True, lambda: load_artifact(CTC_TYPED_LEGACY_SLOTS)),
}


@pytest.fixture(params=sorted(ARTIFACTS))
def artifact(request, tmp_path: Path) -> tuple[str, bool, Path]:
    declared, load = ARTIFACTS[request.param]
    return request.param, declared, write_artifact(tmp_path, f"{request.param}.json", load())


def _companion_projection() -> FedCtcProjection:
    """The companion case's tax unit and its three related persons, input for input."""
    person_inputs = {
        H + "dependent_under_section_152": [True, True, True],
        H + "qualifying_child_described_in_subsection_c": [True, False, True],
        H + "noncitizen_exception_to_other_dependent_credit_under_subsection_h": [False] * 3,
        H + "qualifying_child_ssn_included_on_return": [True, True, False],
        H + "qualifying_child_ssn_is_valid_for_subsection_h": [True, True, False],
    }
    tax_unit_inputs = {
        H + "filing_status_is_joint_return": [True],
        H + "taxpayer_or_spouse_ssn_included_on_return": [True],
        H + "taxpayer_or_spouse_ssn_is_valid_for_subsection_h": [True],
    }
    return FedCtcProjection(
        n_tax_units=1,
        n_persons=3,
        period_year=2018,
        tax_unit_weight=np.ones(1),
        tax_unit_inputs={k: np.asarray(v, dtype=bool) for k, v in tax_unit_inputs.items()},
        person_inputs={k: np.asarray(v, dtype=bool) for k, v in person_inputs.items()},
        person_sort=np.arange(3),
        relation_offsets=np.array([0, 3]),
        qualifying_children_per_tu=np.array([1]),
        other_dependents_per_tu=np.array([2]),
        filing_status=np.array([1]),
    )


def _run(artifact_path: Path, request: bytes) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(M.ENGINE_BIN), "run-compiled", "--artifact", str(artifact_path)],
        input=request,
        capture_output=True,
    )


def _outputs(stdout: bytes) -> dict[str, float]:
    (result,) = orjson.loads(stdout)["results"]
    return {
        key.split("#", 1)[1]: float(out["value"]["value"]) for key, out in result["outputs"].items()
    }


def _layout(path: Path) -> RelationLayout:
    return M._ctc_layout(path)


def _flipped(layout: RelationLayout) -> RelationLayout:
    return RelationLayout(
        layout.relation, layout.owner_kind, layout.member_kind, 1 - layout.owner_slot, "usage"
    )


def test_producer_run_reproduces_the_companion_reference(artifact) -> None:
    _, _, path = artifact
    out = M._execute_ctc(_companion_projection(), path, 2018, FED_CTC_DEFAULT_OUTPUTS)

    assert {name: float(values[0]) for name, values in out.items()} == COMPANION_REFERENCE


def test_strict_engine_binds_the_producer_request_without_lenient(artifact) -> None:
    _, _, path = artifact
    request = _build_ctc_request_bytes(
        _companion_projection(), 2018, FED_CTC_DEFAULT_OUTPUTS, layout=_layout(path)
    )
    assert b"relation_binding" not in request and b"lenient" not in request

    proc = _run(path, request)

    assert proc.returncode == 0, proc.stderr.decode()[:1000]
    assert b"relation_slot_entity_mismatch" not in proc.stderr
    binding = orjson.loads(proc.stdout).get("metadata", {}).get("relation_binding")
    if binding is None:
        pytest.skip("engine predates strict relation binding (axiom-rules-engine#190)")
    assert binding == "strict"
    assert _outputs(proc.stdout) == COMPANION_REFERENCE


def test_the_other_tuple_order_is_refused_or_counts_nothing(artifact) -> None:
    """What the producer avoids: the order the artifact does not aggregate with.

    A strict engine refuses it when the artifact declares slot kinds. Without
    declarations, or on a lenient engine, it runs, exits 0 and counts no
    dependents, which is why the order has to come from the artifact.
    """
    _, declared, path = artifact
    projection = _companion_projection()
    right = _run(
        path,
        _build_ctc_request_bytes(projection, 2018, FED_CTC_DEFAULT_OUTPUTS, layout=_layout(path)),
    )
    strict = orjson.loads(right.stdout).get("metadata", {}).get("relation_binding") == "strict"
    wrong_request = _build_ctc_request_bytes(
        projection, 2018, FED_CTC_DEFAULT_OUTPUTS, layout=_flipped(_layout(path))
    )

    proc = _run(path, wrong_request)

    if strict and declared:
        assert proc.returncode != 0
        assert b"strict dataset relation entity validation failed" in proc.stderr
    else:
        assert proc.returncode == 0, proc.stderr.decode()[:1000]
        out = _outputs(proc.stdout)
        assert (out[QUALIFYING_CHILDREN], out[OTHER_DEPENDENTS], out[MAXIMUM]) == (0, 0, 0)


@st.composite
def tax_unit_batches(draw) -> TaxUnitBatch:
    sizes = draw(st.lists(st.integers(1, 6), min_size=1, max_size=5))
    n_persons = sum(sizes)
    ages = draw(st.lists(st.integers(0, 80), min_size=n_persons, max_size=n_persons))
    order = list(draw(st.permutations(range(n_persons))))
    tax_unit_index = np.repeat(np.arange(len(sizes)), sizes).astype(np.int64)[order]
    return TaxUnitBatch(
        state="ZZ",
        year="2024",
        n_persons=n_persons,
        n_tax_units=len(sizes),
        person_tax_unit_index=tax_unit_index,
        tax_unit_weight=np.ones(len(sizes)),
        person_columns={"age": np.asarray(ages, dtype=np.int64)},
    )


@pytest.mark.parametrize("name", sorted(ARTIFACTS))
@settings(max_examples=15, deadline=None)
@given(batch=tax_unit_batches())
def test_engine_counts_equal_the_projections_own_counts(name, batch, tmp_path_factory) -> None:
    """Differential: the engine's relation counts match the projection's numpy counts.

    The projection classifies each person without the engine; the engine
    counts them through the relation. They agree for every tax unit only when
    each tuple is in the order the artifact aggregates with.
    """
    _, load = ARTIFACTS[name]
    path = write_artifact(tmp_path_factory.mktemp(name), "artifact.json", load())
    projection = project_ctc(batch, period_year=2026)
    # With no dependent, every count is 0 under either order.
    assume(projection.person_inputs[H + "dependent_under_section_152"].any())

    out = M._execute_ctc(projection, path, 2026, FED_CTC_DEFAULT_OUTPUTS)

    np.testing.assert_array_equal(out[QUALIFYING_CHILDREN], projection.qualifying_children_per_tu)
    np.testing.assert_array_equal(out[OTHER_DEPENDENTS], projection.other_dependents_per_tu)
    assert (out[QUALIFYING_CHILDREN] + out[OTHER_DEPENDENTS]).sum() == (
        projection.person_inputs[H + "dependent_under_section_152"].sum()
    )


# --- Generated programs, with the engine as the oracle -----------------------
#
# Self-contained requests carry the program inline, so the engine runs shapes
# no fixture has: member-side uses, counts nested in another relation's
# predicate, typed and untyped relations in both declared orders, and slots
# that agree or not. The reference counts are plain Python over the
# population; the engine decides what each order computes.

DEP, TUH = "dep", "tuh"
TU, P, HH = "TaxUnit", "Person", "Household"
IV = {"start": "2026-01-01", "end": "2026-12-31"}
PERIOD = {"period_kind": "tax_year", **IV}
CHILD = {"kind": "derived", "name": "is_child"}


def _lit(value: int) -> dict:
    return {"kind": "literal", "value": {"kind": "integer", "value": value}}


def _prog_rule(name: str, entity: str, expr: dict, *, judgment: bool = False) -> dict:
    return {
        "name": name,
        "entity": entity,
        "dtype": "judgment" if judgment else "integer",
        "unit": None,
        "semantics": "judgment" if judgment else "scalar",
        "expr": expr,
    }


def _count(relation: str, current_slot: int, where: dict | None = None) -> dict:
    node = {
        "kind": "count_related",
        "relation": relation,
        "current_slot": current_slot,
        "related_slot": 1 - current_slot,
    }
    if where is not None:
        node["where"] = where
    return node


BASE_RULES = [
    _prog_rule(
        "is_child",
        P,
        {
            "kind": "comparison",
            "left": {"kind": "input", "name": "age"},
            "op": "lt",
            "right": _lit(17),
        },
        judgment=True,
    ),
    # Give tax units and households an input record, so their ids carry kinds.
    _prog_rule("tu_flag_rule", TU, {"kind": "input", "name": "tu_flag"}),
    _prog_rule("hh_flag_rule", HH, {"kind": "input", "name": "hh_flag"}),
]


@st.composite
def generated_programs(draw):
    """A program over ``dep`` (tax unit–person) and ``tuh`` (tax unit–household).

    Untyped ``dep`` is compiled the only way engines compile untyped relations:
    each use's evaluating entity in slot 1. Typed ``dep`` gets slots that follow
    one intended orientation, or independent random slots.
    """
    dep_declared = draw(st.sampled_from([None, [TU, P], [P, TU]]))
    tuh_declared = draw(st.sampled_from([None, [TU, HH], [HH, TU]]))
    intended = draw(st.sampled_from([0, 1]))
    consistent = draw(st.booleans())

    def slot_for(entity: str) -> int:
        if dep_declared is None:
            return 1
        if not consistent:
            return draw(st.sampled_from([0, 1]))
        return intended if entity == TU else 1 - intended

    household_slot = draw(st.sampled_from([0, 1]))
    uses = draw(
        st.sets(
            st.sampled_from(["n_children", "n_units", "units_with_children", "units"]), min_size=1
        )
    )
    rules = list(BASE_RULES)
    if "n_children" in uses:
        rules.append(_prog_rule("n_children", TU, _count(DEP, slot_for(TU), CHILD)))
    if "n_units" in uses:
        rules.append(_prog_rule("n_units", P, _count(DEP, slot_for(P))))
    for name in sorted(uses & {"units_with_children", "units"}):
        inner = _count(DEP, slot_for(TU), CHILD if name == "units_with_children" else None)
        where = {"kind": "comparison", "left": inner, "op": "gt", "right": _lit(0)}
        rules.append(_prog_rule(name, HH, _count(TUH, household_slot, where)))
    relations = []
    for relation, declared in ((DEP, dep_declared), (TUH, tuh_declared)):
        schema = {"name": relation, "arity": 2}
        if declared is not None:
            schema["slot_entities"] = declared
        relations.append(schema)
    # A bare nested count's evaluating entity is visible only through a
    # typed outer relation.
    unpinned = "units" in uses and tuh_declared is None
    return {"relations": relations, "derived": rules}, household_slot, uses, unpinned


# Households of tax units of person ages. The first person is a child, so a
# count read from the wrong slot always shows.
populations = st.lists(
    st.lists(st.lists(st.integers(0, 80), min_size=1, max_size=3), min_size=1, max_size=3),
    min_size=1,
    max_size=3,
).map(lambda households: [[[5, *households[0][0][1:]], *households[0][1:]], *households[1:]])


def _flatten(households):
    """(household id, tax unit id, person id, age) for every person."""
    rows, tu, p = [], 0, 0
    for h, units in enumerate(households):
        for ages in units:
            for age in ages:
                rows.append((f"h{h}", f"tu{tu}", f"p{p}", age))
                p += 1
            tu += 1
    return rows


def _inline_dataset(households, owner_slot: int, household_slot: int | None) -> dict:
    """``household_slot`` None: no nested use, so no ``tuh`` tuples to lay out."""
    rows = _flatten(households)
    inputs, relations, seen = [], [], set()

    def record(name, entity, entity_id, value):
        inputs.append(
            {
                "name": name,
                "entity": entity,
                "entity_id": entity_id,
                "interval": IV,
                "value": {"kind": "integer", "value": value},
            }
        )

    for hh, tu, p, age in rows:
        if hh not in seen:
            seen.add(hh)
            record("hh_flag", HH, hh, 1)
        if tu not in seen:
            seen.add(tu)
            record("tu_flag", TU, tu, 1)
            if household_slot is not None:
                pair = [tu, tu]
                pair[household_slot] = hh
                relations.append({"name": TUH, "tuple": pair, "interval": IV})
        record("age", P, p, age)
        layout = RelationLayout(DEP, TU, P, owner_slot, "usage")
        relations.append({"name": DEP, "tuple": layout.tuple_for(tu, p), "interval": IV})
    return {"inputs": inputs, "relations": relations}


def _reference(households, uses) -> dict[tuple[str, str], float]:
    rows = _flatten(households)
    out: dict[tuple[str, str], float] = {}
    for hh, tu, p, age in rows:
        if "n_children" in uses:
            out[(tu, "n_children")] = out.get((tu, "n_children"), 0) + (age < 17)
        if "n_units" in uses:
            out[(p, "n_units")] = 1
    units = {}
    for hh, tu, _, age in rows:
        units.setdefault(hh, {}).setdefault(tu, False)
        units[hh][tu] |= age < 17
    for hh, flags in units.items():
        if "units_with_children" in uses:
            out[(hh, "units_with_children")] = sum(flags.values())
        if "units" in uses:
            out[(hh, "units")] = len(flags)
    return {key: float(value) for key, value in out.items()}


def _run_inline(
    program, dataset, reference, *, mode: str = "fast"
) -> tuple[subprocess.CompletedProcess, dict | None]:
    queries = {}
    for entity_id, output in reference:
        queries.setdefault(entity_id, []).append(output)
    request = {
        "mode": mode,
        "program": program,
        "dataset": dataset,
        "queries": [
            {"entity_id": entity_id, "period": PERIOD, "outputs": outputs}
            for entity_id, outputs in queries.items()
        ],
    }
    proc = subprocess.run([str(M.ENGINE_BIN)], input=orjson.dumps(request), capture_output=True)
    if proc.returncode != 0:
        return proc, None
    got = {}
    for result in orjson.loads(proc.stdout)["results"]:
        for key, out in result["outputs"].items():
            got[(result["entity_id"], key.split("#", 1)[-1])] = float(out["value"]["value"])
    return proc, got


@settings(max_examples=80, deadline=None)
@given(generated=generated_programs(), households=populations)
def test_the_producers_order_is_the_one_the_engine_computes_correctly(generated, households):
    """If the producer picks an order, the engine binds it and every output is right.

    If it refuses because uses disagree, no order gets every output right. A
    typed use that pins no slot is refused; an untyped artifact keeps its legacy
    order and is checked against the engine just like any other accepted layout.
    """
    program, household_slot, uses, unpinned = generated
    reference = _reference(households, uses)
    if not uses & {"units_with_children", "units"}:
        household_slot = None  # tuh unused: no tuples to lay out

    def outputs(owner_slot: int):
        dataset = _inline_dataset(households, owner_slot, household_slot)
        return _run_inline(program, dataset, reference)

    try:
        layout = relation_layout({"program": program}, DEP, owner_kind=TU, member_kind=P)
    except ValueError as error:
        if "no tuple order is right" in str(error):
            event("refused: uses disagree")
            assert all(got != reference for _, got in (outputs(0), outputs(1)))
        else:
            event("refused: a use pins no slot")
            assert unpinned and "without showing" in str(error) and "'units'" in str(error), error
        return

    event(f"layout from {layout.basis}")
    if unpinned:
        dep = next(relation for relation in program["relations"] if relation["name"] == DEP)
        assert not dep.get("slot_entities") and layout.owner_slot == 1
    proc, got = outputs(layout.owner_slot)
    assert proc.returncode == 0, proc.stderr.decode()[:800]
    assert b"relation_slot_entity_mismatch" not in proc.stderr
    assert got == reference


# --- Differential: the inference against the engine's own -------------------
#
# ``_executable_slot_kinds`` ports the engine's usage inference. The strict
# engine exposes its result: send ``dep`` a tuple of ids of a kind no program
# mentions, and it names the kind it expected in every slot it knows. The
# generated programs cover every expression kind the port walks, derived
# relations (chained, alias entities), Scalar rules and versions. Dependencies
# stay acyclic so compilation reaches the binding check used as the oracle.

SENTINEL = "Sentinel"
EXPECTED_SLOT = re.compile(
    r"dataset relation `dep` tuple slot (\d) contains entity id `\w+`, expected `(\w+)`"
)
PROBE_RULES = [
    *BASE_RULES,
    _prog_rule("p_amount", P, {"kind": "input", "name": "amount"}),
    _prog_rule("rate", "Scalar", _lit(1)),
    _prog_rule(
        "hh_positive",
        HH,
        {
            "kind": "comparison",
            "left": {"kind": "input", "name": "hh_flag"},
            "op": "gt",
            "right": _lit(0),
        },
        judgment=True,
    ),
    _prog_rule(SENTINEL.lower(), SENTINEL, {"kind": "input", "name": "sentinel_flag"}),
]
SCALAR_REFS = ["p_amount", "tu_flag_rule", "hh_flag_rule", "rate"]
JUDGMENT_REFS = ["is_child", "hh_positive"]


@st.composite
def probe_programs(draw):
    relations_used = ["dep", "tuh"]
    rule_refs: list[str] = []

    def slots():
        current = draw(st.sampled_from([0, 1]))
        return current, 1 - current

    def scalar(depth: int) -> dict:
        kinds = ["literal", "input", "ref"]
        if depth > 0:
            kinds += [
                "add",
                "sub",
                "max",
                "ceil",
                "if",
                "no_match",
                "over",
                "lookup",
                "count",
                "sum",
            ]
        kind = draw(st.sampled_from(kinds))
        if kind == "literal":
            return _lit(draw(st.integers(0, 3)))
        if kind == "input":
            return draw(
                st.sampled_from(
                    [
                        {"kind": "input", "name": "x"},
                        {
                            "kind": "input_or_else",
                            "name": "x",
                            "default": {"kind": "integer", "value": 0},
                        },
                        {"kind": "period_start"},
                    ]
                )
            )
        if kind == "ref":
            return {"kind": "derived", "name": draw(st.sampled_from(SCALAR_REFS + rule_refs))}
        if kind in ("add", "max"):
            return {"kind": kind, "items": [scalar(depth - 1), scalar(depth - 1)]}
        if kind == "sub":
            return {"kind": "sub", "left": scalar(depth - 1), "right": scalar(depth - 1)}
        if kind == "ceil":
            return {"kind": "ceil", "value": scalar(depth - 1)}
        if kind == "if":
            return {
                "kind": "if",
                "condition": judgment(depth - 1),
                "then_expr": scalar(depth - 1),
                "else_expr": scalar(depth - 1),
            }
        if kind == "no_match":
            return {
                "kind": "no_match",
                "subject": scalar(depth - 1),
                "patterns": [scalar(depth - 1)],
            }
        if kind == "over":
            return {"kind": "over_periods", "over": "sum", "value": scalar(depth - 1)}
        if kind == "lookup":
            return {"kind": "parameter_lookup", "parameter": "table", "index": scalar(depth - 1)}
        current, related = slots()
        node = {
            "kind": "count_related" if kind == "count" else "sum_related",
            "relation": draw(st.sampled_from(relations_used)),
            "current_slot": current,
            "related_slot": related,
        }
        if kind == "sum":
            node["value"] = draw(
                st.sampled_from(
                    [{"kind": "input", "name": "amount"}]
                    + [{"kind": "derived", "name": n} for n in SCALAR_REFS + rule_refs]
                )
            )
        if draw(st.booleans()):
            node["where"] = judgment(depth - 1)
        return node

    def judgment(depth: int) -> dict:
        kinds = ["ref", "member", "comparison"]
        if depth > 0:
            kinds += ["and", "or", "not", "exactly_one"]
        kind = draw(st.sampled_from(kinds))
        if kind == "ref":
            return {"kind": "derived", "name": draw(st.sampled_from(JUDGMENT_REFS + rule_refs))}
        if kind == "member":
            current, related = slots()
            return {
                "kind": "relation_member",
                "relation": draw(st.sampled_from(relations_used)),
                "current_slot": current,
                "related_slot": related,
            }
        if kind == "comparison":
            return {
                "kind": "comparison",
                "left": scalar(max(depth - 1, 0)),
                "op": draw(st.sampled_from(["gt", "eq"])),
                "right": scalar(0),
            }
        if kind == "not":
            return {"kind": "not", "item": judgment(depth - 1)}
        return {
            "kind": kind,
            "items": [judgment(depth - 1) for _ in range(draw(st.integers(1, 2)))],
        }

    relations = [
        {"name": "dep", "arity": 2, "slot_entities": draw(st.permutations([TU, P]))},
        {"name": "tuh", "arity": 2},
    ]
    tuh_declared = draw(st.sampled_from([None, [TU, HH], [HH, TU]]))
    if tuh_declared:
        relations[1]["slot_entities"] = tuh_declared
    derived_names = [f"d{i}" for i in range(draw(st.sampled_from([0, 1, 1, 2])))]
    for name in derived_names:
        current, related = slots()
        derivation = {
            "source_relation": draw(st.sampled_from(["dep", "dep", *relations_used])),
            "current_slot": current,
            "related_slot": related,
            "predicate": judgment(draw(st.integers(0, 2))),
        }
        # An entity the rules also use exercises the alias mapping
        # (engine: executable_current_kind).
        entity = draw(st.sampled_from([None, TU, P, HH, TU]))
        if entity:
            derivation["entity"] = entity
        kinds = draw(st.sampled_from([[], [TU, P], [P, TU], [TU, HH], [HH, TU], [P, HH]]))
        if kinds:
            derivation["slot_entities"] = kinds
        relations.append({"name": name, "arity": 2, "derivation": derivation})
        relations_used.append(name)
    rules = list(PROBE_RULES)
    for i in range(draw(st.integers(1, 3))):
        entity = draw(st.sampled_from([TU, TU, P, HH, "Scalar"]))
        is_judgment = draw(st.booleans())

        def expression():
            return (
                judgment(draw(st.integers(1, 3)))
                if is_judgment
                else scalar(draw(st.integers(1, 3)))
            )

        rule = _prog_rule(f"r{i}", entity, expression(), judgment=is_judgment)
        if draw(st.integers(0, 3)) == 0:
            rule["versions"] = [
                {
                    "effective_from": "2018-01-01",
                    "semantics": rule["semantics"],
                    "expr": expression(),
                }
                for _ in range(draw(st.integers(1, 2)))
            ]
        rules.append(rule)
        rule_refs.append(rule["name"])
    return {"relations": relations, "derived": rules}


def _sentinel_record(entity_id: str) -> dict:
    return {
        "name": "sentinel_flag",
        "entity": SENTINEL,
        "entity_id": entity_id,
        "interval": IV,
        "value": {"kind": "integer", "value": 1},
    }


def _engine_expected_kinds(program: dict) -> list[str | None]:
    dataset = {
        "inputs": [_sentinel_record("s0"), _sentinel_record("s1")],
        "relations": [{"name": "dep", "tuple": ["s0", "s1"], "interval": IV}],
    }
    request = {"mode": "fast", "program": program, "dataset": dataset, "queries": []}
    proc = subprocess.run([str(M.ENGINE_BIN)], input=orjson.dumps(request), capture_output=True)
    stderr = proc.stderr.decode()
    expected: list[str | None] = [None, None]
    if proc.returncode != 0:
        assert "strict dataset relation entity validation failed" in stderr, stderr[:800]
        for match in EXPECTED_SLOT.finditer(stderr):
            expected[int(match.group(1))] = match.group(2)
    return expected


@functools.cache
def _strict_engine() -> bool:
    probe = {"relations": [{"name": "dep", "arity": 2, "slot_entities": [TU, P]}], "derived": []}
    request = {"mode": "fast", "program": probe, "dataset": {}, "queries": []}
    proc = subprocess.run([str(M.ENGINE_BIN)], input=orjson.dumps(request), capture_output=True)
    return proc.returncode == 0 and b'"relation_binding": "strict"' in proc.stdout


@settings(max_examples=500, deadline=None)
@given(program=probe_programs())
def test_inferred_slot_kinds_match_the_strict_engines(program) -> None:
    if not _strict_engine():
        pytest.skip("needs the strict relation binding of axiom-rules-engine#190")
    relations = {r["name"]: r for r in program["relations"]}
    uses = _executable_slot_kinds(program, relations, "dep")
    inferred = [next(iter(kinds)) if len(kinds) == 1 else None for kinds in uses.slot_kinds]
    if not uses.recorded:  # an unused relation is checked in declared order
        inferred = list(relations["dep"]["slot_entities"])

    assert inferred == _engine_expected_kinds(program)


def _nested_units(outer_current: int, inner: dict, tuh_declared: list[str] | None) -> dict:
    """A Household rule counting its tax units for which ``inner`` is positive."""
    where = {"kind": "comparison", "left": inner, "op": "gt", "right": _lit(0)}
    tuh = {"name": "tuh", "arity": 2}
    if tuh_declared:
        tuh["slot_entities"] = tuh_declared
    return {
        "relations": [{"name": "dep", "arity": 2, "slot_entities": [TU, P]}, tuh],
        "derived": [
            *PROBE_RULES,
            _prog_rule("units_with_children", HH, _count("tuh", outer_current, where)),
        ],
    }


def _member_under_if_in_a_predicate() -> dict:
    member = {"kind": "relation_member", "relation": "dep", "current_slot": 0, "related_slot": 1}
    branch = {"kind": "if", "condition": member, "then_expr": _lit(1), "else_expr": _lit(0)}
    derived_relation = {
        "name": "d",
        "arity": 2,
        "derivation": {
            "source_relation": "claims",
            "current_slot": 0,
            "related_slot": 1,
            "entity": TU,
            "slot_entities": [TU, P],
            "predicate": {"kind": "comparison", "left": branch, "op": "eq", "right": _lit(1)},
        },
    }
    return {
        "relations": [
            {"name": "dep", "arity": 2, "slot_entities": [TU, P]},
            {"name": "claims", "arity": 2, "slot_entities": [TU, P]},
            derived_relation,
        ],
        "derived": [*PROBE_RULES, _prog_rule("n_d", TU, _count("d", 0))],
    }


# Shapes a round-1 review ran against the strict engine: uses nested in another
# relation's predicate (#140-#179 slots, post-#179 slots, an untyped outer
# relation) and a membership test under `if` in a derived relation's predicate.
ENGINE_COUNTEREXAMPLES = {
    "nested-legacy-slots": _nested_units(1, _count("dep", 1, CHILD), [TU, HH]),
    "nested-post-179": _nested_units(1, _count("dep", 0, CHILD), [TU, HH]),
    "nested-in-untyped-outer": _nested_units(1, _count("dep", 1, CHILD), None),
    "member-under-if-in-predicate": _member_under_if_in_a_predicate(),
}


@pytest.mark.parametrize("name", sorted(ENGINE_COUNTEREXAMPLES))
def test_inference_and_layout_agree_with_the_engine_on_review_counterexamples(name) -> None:
    if not _strict_engine():
        pytest.skip("needs the strict relation binding of axiom-rules-engine#190")
    program = ENGINE_COUNTEREXAMPLES[name]
    relations = {r["name"]: r for r in program["relations"]}
    uses = _executable_slot_kinds(program, relations, "dep")
    inferred = [next(iter(kinds)) if len(kinds) == 1 else None for kinds in uses.slot_kinds]
    expected = _engine_expected_kinds(program)
    assert inferred == expected and None not in expected

    layout = relation_layout({"program": program}, "dep", owner_kind=TU, member_kind=P)
    assert layout.slot_kinds == tuple(expected)


def _compare(left: dict, op: str, right: dict) -> dict:
    return {"kind": "comparison", "left": left, "op": op, "right": right}


def _constant(holds: bool) -> dict:
    return _compare(_lit(1), "eq" if holds else "ne", _lit(1))


def _indicator(condition: dict) -> dict:
    return {"kind": "if", "condition": condition, "then_expr": _lit(1), "else_expr": _lit(0)}


def _membership_predicates(current_slot: int) -> dict[str, dict]:
    member = {
        "kind": "relation_member",
        "relation": DEP,
        "current_slot": current_slot,
        "related_slot": 1 - current_slot,
    }
    scalar = _indicator(member)
    return {
        "direct": member,
        "if-condition-in-comparison": _compare(scalar, "eq", _lit(1)),
        "comparison-right-operand": _compare(_lit(1), "eq", scalar),
        "if-then-branch": _compare(
            {
                "kind": "if",
                "condition": _constant(True),
                "then_expr": scalar,
                "else_expr": _lit(0),
            },
            "eq",
            _lit(1),
        ),
        "if-else-branch": _compare(
            {
                "kind": "if",
                "condition": _constant(False),
                "then_expr": _lit(0),
                "else_expr": scalar,
            },
            "eq",
            _lit(1),
        ),
        "add": _compare({"kind": "add", "items": [scalar, _lit(0)]}, "eq", _lit(1)),
        "sub-mul-div": _compare(
            {
                "kind": "sub",
                "left": {
                    "kind": "div",
                    "left": {"kind": "mul", "left": scalar, "right": _lit(1)},
                    "right": _lit(1),
                },
                "right": _lit(0),
            },
            "eq",
            _lit(1),
        ),
        "max-min-ceil-floor": _compare(
            {
                "kind": "max",
                "items": [
                    _lit(0),
                    {
                        "kind": "min",
                        "items": [
                            _lit(1),
                            {"kind": "ceil", "value": {"kind": "floor", "value": scalar}},
                        ],
                    },
                ],
            },
            "eq",
            _lit(1),
        ),
        "and-or-not": {
            "kind": "or",
            "items": [
                _constant(False),
                {
                    "kind": "and",
                    "items": [
                        _constant(True),
                        {"kind": "not", "item": {"kind": "not", "item": member}},
                    ],
                },
            ],
        },
    }


@pytest.mark.parametrize("wrapper", sorted(_membership_predicates(0)))
@pytest.mark.parametrize("current_slot", [0, 1])
@pytest.mark.parametrize("mode", ["fast", "explain"])
def test_membership_context_and_producer_order_match_engine_execution(
    wrapper: str, current_slot: int, mode: str
) -> None:
    """Every context-preserving wrapper consumes exactly the inferred order."""
    if not _strict_engine():
        pytest.skip("needs the strict relation binding of axiom-rules-engine#190")
    program = _member_under_if_in_a_predicate()
    program["relations"][2]["derivation"]["predicate"] = _membership_predicates(current_slot)[
        wrapper
    ]
    relations = {relation["name"]: relation for relation in program["relations"]}
    uses = _executable_slot_kinds(program, relations, DEP)
    inferred = [next(iter(kinds)) if len(kinds) == 1 else None for kinds in uses.slot_kinds]
    assert inferred == _engine_expected_kinds(program)

    layout = relation_layout({"program": program}, DEP, owner_kind=TU, member_kind=P)
    assert layout.owner_slot == current_slot
    dataset = _inline_dataset([[[5]]], layout.owner_slot, None)
    dataset["relations"].append({"name": "claims", "tuple": ["tu0", "p0"], "interval": IV})
    reference = {("tu0", "n_d"): 1.0}
    proc, got = _run_inline(program, dataset, reference, mode=mode)
    assert proc.returncode == 0, proc.stderr.decode()[:800]
    assert got == reference

    dataset["relations"][0]["tuple"] = _flipped(layout).tuple_for("tu0", "p0")
    proc, _ = _run_inline(program, dataset, reference, mode=mode)
    assert proc.returncode != 0
    assert b"strict dataset relation entity validation failed" in proc.stderr


@pytest.mark.parametrize("scope", ["rule", "rule-if", "rule-where", "predicate-where"])
def test_out_of_context_membership_implies_no_orientation(scope: str) -> None:
    """Explain rejects these sites; strict binding keeps dep's declared order."""
    if not _strict_engine():
        pytest.skip("needs the strict relation binding of axiom-rules-engine#190")
    program = _member_under_if_in_a_predicate()
    derivation = program["relations"][2]["derivation"]
    derivation["predicate"] = _constant(True)
    member = {"kind": "relation_member", "relation": DEP, "current_slot": 1, "related_slot": 0}
    output = "invalid_member"
    if scope == "predicate-where":
        derivation["predicate"] = _compare(_count("claims", 1, member), "gt", _lit(0))
        output = "n_d"
    else:
        expression = {
            "rule": member,
            "rule-if": _indicator(member),
            "rule-where": _count("claims", 0, member),
        }[scope]
        program["derived"].append(_prog_rule(output, TU, expression, judgment=scope == "rule"))
    relations = {relation["name"]: relation for relation in program["relations"]}
    uses = _executable_slot_kinds(program, relations, DEP)
    assert not uses.recorded and not any(uses.slot_kinds)
    assert _engine_expected_kinds(program) == [TU, P]
    layout = relation_layout({"program": program}, DEP, owner_kind=TU, member_kind=P)
    assert layout.basis == "declared" and layout.owner_slot == 0

    dataset = _inline_dataset([[[5]]], layout.owner_slot, None)
    dataset["relations"].append({"name": "claims", "tuple": ["tu0", "p0"], "interval": IV})
    proc, _ = _run_inline(program, dataset, {("tu0", output): 1.0}, mode="explain")
    assert proc.returncode != 0
    assert (
        b"relation predicate `dep` can only be evaluated inside a derived relation" in proc.stderr
    )
    assert b"strict dataset relation entity validation failed" not in proc.stderr


def test_untyped_unpinned_nested_count_keeps_the_working_legacy_request(tmp_path: Path) -> None:
    """The review's v0.1.1 artifact counts 1 with the unchanged legacy request."""
    relation = M.FED_CTC_RELATION_NAME
    nested = _compare(_count(relation, 1), "eq", _lit(0))
    rule = _prog_rule(MAXIMUM, TU, _count(relation, 1, nested))
    rule["id"] = M.FED_CTC_OUTPUT_IDS[MAXIMUM]
    artifact = {
        "artifact_format_version": 2,
        "engine_version": "0.1.1",
        "program": {"relations": [{"name": relation, "arity": 2}], "derived": [rule]},
        "metadata": {
            "evaluation_order": [MAXIMUM],
            "fast_path": {"strategy": "generic_bulk", "compatible": True, "blockers": []},
        },
    }
    path = write_artifact(tmp_path, "untyped-nested-ctc.json", artifact)
    projection = FedCtcProjection(
        n_tax_units=1,
        n_persons=1,
        period_year=2026,
        tax_unit_weight=np.ones(1),
        tax_unit_inputs={},
        person_inputs={},
        person_sort=np.array([0]),
        relation_offsets=np.array([0, 1]),
        qualifying_children_per_tu=np.array([1]),
        other_dependents_per_tu=np.array([0]),
        filing_status=np.array([0]),
    )
    legacy = RelationLayout.legacy(relation, owner_kind=TU, member_kind=P)
    request = _build_ctc_request_bytes(projection, 2026, (MAXIMUM,), layout=_layout(path))
    assert request == _build_ctc_request_bytes(projection, 2026, (MAXIMUM,), layout=legacy)
    assert orjson.loads(request)["dataset"]["relations"][0]["tuple"] == ["p0", "tu0"]
    proc = _run(path, request)
    assert proc.returncode == 0, proc.stderr.decode()[:800]
    assert _outputs(proc.stdout) == {MAXIMUM: 1.0}
    np.testing.assert_array_equal(M._execute_ctc(projection, path, 2026, (MAXIMUM,))[MAXIMUM], [1])

    wrong = _build_ctc_request_bytes(projection, 2026, (MAXIMUM,), layout=_flipped(legacy))
    proc = _run(path, wrong)
    assert proc.returncode == 0, proc.stderr.decode()[:800]
    assert _outputs(proc.stdout) == {MAXIMUM: 0.0}


def test_untyped_unpinned_use_cannot_override_an_incompatible_member_side_use() -> None:
    """Neither order serves both known member-side and unknown nested uses."""
    program = {
        "relations": [{"name": DEP, "arity": 2}, {"name": TUH, "arity": 2}],
        "derived": [
            *BASE_RULES,
            _prog_rule("n_units", P, _count(DEP, 1)),
            _prog_rule("units", HH, _count(TUH, 1, _compare(_count(DEP, 1), "gt", _lit(0)))),
        ],
    }
    with pytest.raises(ValueError, match="without showing"):
        relation_layout({"program": program}, DEP, owner_kind=TU, member_kind=P)

    households = [[[5]]]
    reference = _reference(households, {"n_units", "units"})
    observed = []
    for owner_slot in (0, 1):
        proc, got = _run_inline(program, _inline_dataset(households, owner_slot, 1), reference)
        assert proc.returncode == 0, proc.stderr.decode()[:800]
        assert got != reference
        observed.append(got)
    assert observed == [
        {("p0", "n_units"): 1.0, ("h0", "units"): 0.0},
        {("p0", "n_units"): 0.0, ("h0", "units"): 1.0},
    ]


@pytest.mark.parametrize("subject_has_use", [False, True])
def test_no_match_subject_is_executable_but_its_patterns_are_labels(subject_has_use: bool) -> None:
    """An opposite-order error label never vetoes the order the engine uses."""
    if not _strict_engine():
        pytest.skip("needs the strict relation binding of axiom-rules-engine#190")
    no_match = {
        "kind": "no_match",
        "subject": _count(DEP, 1) if subject_has_use else _lit(0),
        "patterns": [_count(DEP, 0)],
    }
    guard = {
        "kind": "if",
        "condition": _constant(True),
        "then_expr": _lit(1),
        "else_expr": no_match,
    }
    program = {
        "relations": [{"name": DEP, "arity": 2, "slot_entities": [TU, P]}],
        "derived": [
            *PROBE_RULES,
            _prog_rule("n", TU, _lit(1) if subject_has_use else _count(DEP, 1)),
            _prog_rule("guard", TU, guard),
        ],
    }
    relations = {relation["name"]: relation for relation in program["relations"]}
    uses = _executable_slot_kinds(program, relations, DEP)
    inferred = [next(iter(kinds)) if len(kinds) == 1 else None for kinds in uses.slot_kinds]
    assert inferred == _engine_expected_kinds(program) == [P, TU]
    layout = relation_layout({"program": program}, DEP, owner_kind=TU, member_kind=P)
    assert layout.owner_slot == 1
    reference = {("tu0", "n"): 1.0, ("tu0", "guard"): 1.0}
    proc, got = _run_inline(program, _inline_dataset([[[5]]], 1, None), reference)
    assert proc.returncode == 0, proc.stderr.decode()[:800]
    assert got == reference
    proc, _ = _run_inline(program, _inline_dataset([[[5]]], 0, None), reference)
    assert proc.returncode != 0
    assert b"strict dataset relation entity validation failed" in proc.stderr
