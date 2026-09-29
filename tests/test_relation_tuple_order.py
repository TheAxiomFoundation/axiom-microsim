"""Requests put each relation tuple in the order the compiled artifact expects (issue #23).

Invariants, checked by example on the real fixture artifacts and by property
over generated populations and generated artifacts:

* Binding. Every relation tuple places each entity id in the slot whose
  expected kind equals that id's labelled kind. The expected kinds are those
  the compiled program aggregates with: the declared order for a post-#179
  typed artifact, and current entity in slot 1 for older ones.
* Labels. Every entity id carries exactly one entity kind across its input
  records (TaxUnit, Household or Person, never the generic Entity), so the
  engine can derive its kind.
* Membership. Each person appears in exactly one tuple, paired with the tax
  unit or household it belongs to.
* Only order changes. Two layouts yield the same inputs and queries, and each
  tuple of one is the reverse of the other's.
* Legacy. Against an artifact whose uses agree and aggregate in the legacy
  direction, the request is byte-identical to the one this producer sent
  before the change. Every artifact the CTC and CO SNAP programs have been
  compiled to is such an artifact. One whose uses disagree now raises instead.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _artifacts import (
    CO_SNAP_UNTYPED,
    CTC_TYPED,
    CTC_TYPED_LEGACY_SLOTS,
    CTC_UNTYPED,
    load_artifact,
    swap_aggregate_slots,
    write_artifact,
)
from _legacy_request_builders import legacy_build_compiled_request, legacy_build_ctc_request_bytes
from axiom_microsim.data.ecps_loader import TaxUnitBatch
from axiom_microsim.project.co_snap import CoSnapProjection
from axiom_microsim.project.federal_ctc import FedCtcProjection, project as project_ctc
from axiom_microsim.run import microsim as M
from axiom_microsim.run.microsim import (
    CO_SNAP_RELATION_NAME,
    DEFAULT_OUTPUT_IDS,
    FED_CTC_DEFAULT_OUTPUTS,
    FED_CTC_RELATION_NAME,
    HOUSEHOLD,
    PERSON,
    TAX_UNIT,
    _build_compiled_request,
    _build_ctc_request_bytes,
)
from axiom_microsim.run.relation_layout import LEGACY_OWNER_SLOT, RelationLayout, relation_layout

CO_SNAP_OUTPUT_IDS = [DEFAULT_OUTPUT_IDS["snap_allotment"]]


# --- Helpers -----------------------------------------------------------------


def _batch(sizes: list[int], ages: list[int], order: list[int] | None = None) -> TaxUnitBatch:
    """A tax-unit batch; ``order`` shuffles persons so units are not contiguous."""
    tax_unit_index = np.repeat(np.arange(len(sizes)), sizes).astype(np.int64)
    if order is not None:
        tax_unit_index = tax_unit_index[order]
    return TaxUnitBatch(
        state="ZZ",
        year="2024",
        n_persons=len(ages),
        n_tax_units=len(sizes),
        person_tax_unit_index=tax_unit_index,
        tax_unit_weight=np.ones(len(sizes), dtype=np.float64),
        person_columns={"age": np.asarray(ages, dtype=np.int64)},
    )


def _co_snap_projection(sizes: list[int]) -> CoSnapProjection:
    offsets = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    return CoSnapProjection(
        n_households=len(sizes),
        n_persons=int(offsets[-1]),
        period_year=2026,
        household_inputs={"household_size": np.asarray(sizes, dtype=np.int64)},
        relation_offsets=offsets,
        person_inputs={"member_age": np.arange(offsets[-1], dtype=np.int64) % 90},
        household_weight=np.ones(len(sizes), dtype=np.float64),
        household_order=np.arange(len(sizes)),
    )


def _ctc_request(projection: FedCtcProjection, layout: RelationLayout) -> dict:
    return orjson.loads(
        _build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS, layout=layout)
    )


def _co_snap_request(projection: CoSnapProjection, layout: RelationLayout) -> dict:
    return _build_compiled_request(projection, 2026, CO_SNAP_OUTPUT_IDS, layout=layout)


def _labelled_kinds(request: dict) -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = {}
    for record in request["dataset"]["inputs"]:
        kinds.setdefault(record["entity_id"], set()).add(record["entity"])
    return kinds


def _assert_binds(request: dict, expected_kinds: tuple[str, str]) -> None:
    """Binding + labels: each tuple slot holds an id labelled with its expected kind."""
    kinds = _labelled_kinds(request)
    assert all(len(k) == 1 for k in kinds.values()), "an id carries more than one kind"
    assert "Entity" not in set().union(*kinds.values())
    for record in request["dataset"]["relations"]:
        assert len(record["tuple"]) == 2
        for slot, entity_id in enumerate(record["tuple"]):
            assert kinds[entity_id] == {expected_kinds[slot]}, (record, expected_kinds)


def _pairs(request: dict, owner_kind: str) -> Counter:
    """(owner id, member id) per tuple, read by label rather than by position."""
    kinds = _labelled_kinds(request)
    pairs = Counter()
    for record in request["dataset"]["relations"]:
        a, b = record["tuple"]
        pairs[(a, b) if kinds[a] == {owner_kind} else (b, a)] += 1
    return pairs


def _flipped(layout: RelationLayout) -> RelationLayout:
    return RelationLayout(
        layout.relation, layout.owner_kind, layout.member_kind, 1 - layout.owner_slot, "usage"
    )


def _ctc_layout(artifact: dict) -> RelationLayout:
    return relation_layout(artifact, FED_CTC_RELATION_NAME, owner_kind=TAX_UNIT, member_kind=PERSON)


def _co_snap_layout(artifact: dict) -> RelationLayout:
    return relation_layout(
        artifact, CO_SNAP_RELATION_NAME, owner_kind=HOUSEHOLD, member_kind=PERSON
    )


def _example_projection() -> FedCtcProjection:
    """A joint return with two qualifying children and one other dependent,
    and a single parent with one child."""
    batch = _batch(sizes=[5, 2], ages=[40, 38, 10, 5, 19, 30, 8])
    return project_ctc(batch, period_year=2026)


# --- Examples on the real compiled artifacts ---------------------------------


def test_untyped_artifact_request_is_byte_identical_to_the_legacy_request() -> None:
    projection = _example_projection()
    layout = _ctc_layout(load_artifact(CTC_UNTYPED))

    new = _build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS, layout=layout)

    assert new == legacy_build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS)


def test_post_179_artifact_request_sends_tax_unit_first() -> None:
    projection = _example_projection()
    request = _ctc_request(projection, _ctc_layout(load_artifact(CTC_TYPED)))

    assert [r["tuple"] for r in request["dataset"]["relations"]] == [
        ["tu0", "p0"],
        ["tu0", "p1"],
        ["tu0", "p2"],
        ["tu0", "p3"],
        ["tu0", "p4"],
        ["tu1", "p5"],
        ["tu1", "p6"],
    ]
    _assert_binds(request, (TAX_UNIT, PERSON))
    assert "relation_binding" not in request, "the producer must not opt out of strict binding"


def test_declared_kinds_with_legacy_slots_keep_the_legacy_request() -> None:
    projection = _example_projection()
    layout = _ctc_layout(load_artifact(CTC_TYPED_LEGACY_SLOTS))

    new = _build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS, layout=layout)

    assert new == legacy_build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS)


def test_co_snap_request_is_identical_to_the_legacy_request_for_its_artifact() -> None:
    projection = _co_snap_projection([3, 1, 4])
    layout = _co_snap_layout(load_artifact(CO_SNAP_UNTYPED))

    new = _co_snap_request(projection, layout)

    assert orjson.dumps(new) == orjson.dumps(
        legacy_build_compiled_request(projection, 2026, CO_SNAP_OUTPUT_IDS)
    )
    _assert_binds(new, (PERSON, HOUSEHOLD))


def test_request_cache_is_keyed_on_tuple_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # A baseline built for one artifact must not be served for an artifact
    # that expects the other order.
    monkeypatch.setattr(M, "_CTC_REQUEST_CACHE", M._BoundedRequestCache())
    projection = _example_projection()
    key = ("federal-ctc", "ZZ", 2026, FED_CTC_DEFAULT_OUTPUTS)
    legacy = _ctc_layout(load_artifact(CTC_UNTYPED))
    typed = _ctc_layout(load_artifact(CTC_TYPED))

    first = _build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS, key, layout=legacy)
    second = _build_ctc_request_bytes(projection, 2026, FED_CTC_DEFAULT_OUTPUTS, key, layout=typed)

    assert first != second
    _assert_binds(orjson.loads(second), (TAX_UNIT, PERSON))
    assert len(M._CTC_REQUEST_CACHE) == 2


def test_execute_reads_the_order_from_the_artifact_it_runs(monkeypatch, tmp_path) -> None:
    """``_execute_ctc`` derives the layout from ``artifact_path``; no engine needed."""
    monkeypatch.setattr(M, "_CTC_REQUEST_CACHE", M._BoundedRequestCache())
    sent: list[bytes] = []

    class Done:
        returncode = 0
        stdout = b'{"results": []}'
        stderr = b""

    def fake_run(cmd, input, capture_output):
        assert cmd[-1] == str(CTC_TYPED)
        sent.append(input)
        return Done()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    M._execute_ctc(_example_projection(), CTC_TYPED, 2026, FED_CTC_DEFAULT_OUTPUTS)

    _assert_binds(orjson.loads(sent[0]), (TAX_UNIT, PERSON))


@pytest.mark.parametrize("swapped", [False, True], ids=["artifact", "slots-swapped"])
def test_co_snap_executor_and_shelter_bridge_read_the_order_from_the_artifact(
    swapped: bool, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Every CO SNAP request, including both shelter-bridge requests, follows the artifact.

    The first run reports the federal shelter input missing, which sends
    ``_execute_compiled`` through the bridge: one request for the Colorado
    deduction, then the retry. No engine; the slot-swapped copy only moves the
    order the artifact asks for.
    """
    monkeypatch.setattr(M, "_CO_SNAP_REQUEST_CACHE", M._BoundedRequestCache())
    artifact = load_artifact(CO_SNAP_UNTYPED)
    if swapped:
        artifact = swap_aggregate_slots(artifact)
    path = write_artifact(tmp_path, "co-snap.json", artifact)
    sent: list[dict] = []

    class Proc:
        def __init__(self, returncode: int, stderr: bytes = b"") -> None:
            self.returncode, self.stdout, self.stderr = returncode, b'{"results": []}', stderr

    def fake_run(cmd, input, capture_output):
        assert cmd[-1] == str(path)
        sent.append(orjson.loads(input))
        if len(sent) == 1:
            return Proc(1, b"missing input `snap_excess_shelter_deduction`")
        return Proc(0)

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    M._execute_compiled(
        _co_snap_projection([2, 1]),
        path,
        2026,
        ("snap_allotment",),
        cache_key=("co-snap", "ZZ", 2026, ("snap_allotment",)),
    )

    assert len(sent) == 3
    expected = (HOUSEHOLD, PERSON) if swapped else (PERSON, HOUSEHOLD)
    for request in sent:
        _assert_binds(request, expected)


@pytest.mark.parametrize(
    ("builder", "layout"),
    [
        (
            lambda layout: _ctc_request(_example_projection(), layout),
            RelationLayout.legacy(CO_SNAP_RELATION_NAME, owner_kind=HOUSEHOLD, member_kind=PERSON),
        ),
        (
            lambda layout: _co_snap_request(_co_snap_projection([1]), layout),
            RelationLayout.legacy(FED_CTC_RELATION_NAME, owner_kind=TAX_UNIT, member_kind=PERSON),
        ),
    ],
)
def test_builders_reject_a_layout_for_another_relation(builder, layout) -> None:
    with pytest.raises(ValueError, match="does not describe"):
        builder(layout)


# --- Properties --------------------------------------------------------------


@st.composite
def tax_unit_batches(draw) -> TaxUnitBatch:
    sizes = draw(st.lists(st.integers(1, 6), min_size=1, max_size=6))
    n_persons = sum(sizes)
    ages = draw(st.lists(st.integers(0, 90), min_size=n_persons, max_size=n_persons))
    order = draw(st.permutations(range(n_persons)))
    return _batch(sizes, ages, list(order))


def _wrap(node: dict, how: str) -> dict:
    literal = {"kind": "literal", "value": {"kind": "integer", "value": 1}}
    if how == "add":
        return {"kind": "add", "items": [literal, node]}
    if how == "mul":
        return {"kind": "mul", "left": node, "right": literal}
    if how == "if":
        condition = {"kind": "comparison", "left": node, "op": "gt", "right": literal}
        return {"kind": "if", "condition": condition, "then_expr": node, "else_expr": literal}
    return node


@st.composite
def compiled_artifacts(draw, relation: str, owner: str, member: str):
    """(artifact, expected slot kinds) for each engine generation's compile.

    * untyped: no ``slot_entities``; every aggregate from the owner side with
      ``current_slot`` 1 (pre-#179 convention; the owner side is how the CTC
      and SNAP programs aggregate these relations);
    * typed-legacy-slots: ``slot_entities`` declared, still ``current_slot`` 1
      (engines between #140 and #179);
    * typed: ``slot_entities`` declared, each aggregate's ``current_slot`` at
      its rule entity's declared position (post-#179), from either side.

    The expected kinds follow the engine's rules for a compiled program:
    executable usage for a used relation, else the declaration, else legacy.
    """
    shape = draw(st.sampled_from(["untyped", "typed-legacy-slots", "typed"]))
    declared = None if shape == "untyped" else draw(st.permutations([owner, member]))
    rules = []
    for i in range(draw(st.integers(0, 4))):
        if shape == "typed":
            entity = draw(st.sampled_from([owner, member]))
            current = list(declared).index(entity)
        else:
            entity, current = owner, LEGACY_OWNER_SLOT
        kind = draw(st.sampled_from(["count_related", "sum_related"]))
        node = {
            "kind": kind,
            "relation": relation,
            "current_slot": current,
            "related_slot": 1 - current,
        }
        if kind == "sum_related":
            node["value"] = {"kind": "input", "name": "amount"}
        expr = _wrap(node, draw(st.sampled_from(["bare", "add", "mul", "if"])))
        rule = {"name": f"rule_{i}", "entity": entity, "semantics": "scalar", "expr": expr}
        if draw(st.booleans()):
            rule["versions"] = [
                {"effective_from": "2018-01-01", "semantics": "scalar", "expr": expr}
            ]
        rules.append(rule)

    schema = {"name": relation, "arity": 2}
    if declared is not None:
        schema["slot_entities"] = list(declared)
    legacy = (member, owner)
    if rules:
        expected = tuple(declared) if shape == "typed" else legacy
    else:
        expected = tuple(declared) if declared is not None else legacy
    artifact = {"program": {"relations": [schema], "derived": rules}}
    return artifact, expected, shape, bool(rules)


@settings(max_examples=150, deadline=None)
@given(
    batch=tax_unit_batches(),
    generated=compiled_artifacts(FED_CTC_RELATION_NAME, TAX_UNIT, PERSON),
)
def test_ctc_tuples_bind_to_the_slots_the_artifact_expects(batch, generated) -> None:
    artifact, expected, shape, used = generated
    projection = project_ctc(batch, period_year=2026)
    layout = _ctc_layout(artifact)

    request = _ctc_request(projection, layout)

    _assert_binds(request, expected)
    # Membership: sorted person j belongs to the unit the batch assigns it.
    tax_unit_of = batch.person_tax_unit_index[projection.person_sort]
    assert _pairs(request, TAX_UNIT) == Counter(
        (f"tu{tax_unit_of[j]}", f"p{j}") for j in range(batch.n_persons)
    )
    # Only order changes between layouts.
    other = _ctc_request(projection, _flipped(layout))
    assert other["dataset"]["inputs"] == request["dataset"]["inputs"]
    assert other["queries"] == request["queries"]
    assert [r["tuple"][::-1] for r in other["dataset"]["relations"]] == [
        r["tuple"] for r in request["dataset"]["relations"]
    ]
    # Legacy: untyped artifacts, and any artifact that aggregates in the
    # legacy direction, get exactly the request this producer sent before.
    if shape == "untyped" or expected == (PERSON, TAX_UNIT):
        assert orjson.dumps(request) == legacy_build_ctc_request_bytes(
            projection, 2026, FED_CTC_DEFAULT_OUTPUTS
        )


@settings(max_examples=60, deadline=None)
@given(
    sizes=st.lists(st.integers(1, 5), min_size=1, max_size=4),
    generated=compiled_artifacts(CO_SNAP_RELATION_NAME, HOUSEHOLD, PERSON),
)
def test_co_snap_tuples_bind_to_the_slots_the_artifact_expects(sizes, generated) -> None:
    artifact, expected, shape, used = generated
    projection = _co_snap_projection(sizes)
    layout = _co_snap_layout(artifact)

    request = _co_snap_request(projection, layout)

    _assert_binds(request, expected)
    household_of = np.repeat(np.arange(len(sizes)), sizes)
    assert _pairs(request, HOUSEHOLD) == Counter(
        (f"h{household_of[j]}", f"p{j}") for j in range(projection.n_persons)
    )
    if shape == "untyped" or expected == (PERSON, HOUSEHOLD):
        assert orjson.dumps(request) == orjson.dumps(
            legacy_build_compiled_request(projection, 2026, CO_SNAP_OUTPUT_IDS)
        )


@settings(max_examples=100, deadline=None)
@given(
    first=st.sampled_from([0, 1]),
    second=st.sampled_from([0, 1]),
    entity=st.sampled_from([TAX_UNIT, PERSON]),
)
def test_disagreeing_uses_never_yield_a_layout(first, second, entity) -> None:
    """Uses that put the same entity in different slots raise; agreeing uses do not."""
    rules = [
        {
            "name": f"r{i}",
            "entity": entity,
            "semantics": "scalar",
            "expr": {
                "kind": "count_related",
                "relation": FED_CTC_RELATION_NAME,
                "current_slot": slot,
                "related_slot": 1 - slot,
            },
        }
        for i, slot in enumerate((first, second))
    ]
    artifact = {
        "program": {
            "relations": [{"name": FED_CTC_RELATION_NAME, "arity": 2}],
            "derived": rules,
        }
    }
    if first == second:
        layout = _ctc_layout(artifact)
        assert layout.slot_kinds[first] == entity
    else:
        with pytest.raises(ValueError, match="no tuple order is right"):
            _ctc_layout(artifact)
