"""Tuple order is read from the compiled artifact, not hard-coded (issue #23).

The fixture tests pin the three artifact generations the CTC program has been
compiled by. The synthetic-program tests pin how the reader walks an artifact:
which nodes vote, which are ignored, and when it refuses to guess.
"""

from __future__ import annotations

import pytest

from _artifacts import (
    CO_SNAP_UNTYPED,
    CTC_TYPED,
    CTC_TYPED_LEGACY_SLOTS,
    CTC_UNTYPED,
    load_artifact,
)
from axiom_microsim.run.microsim import (
    CO_SNAP_RELATION_NAME,
    FED_CTC_RELATION_NAME,
    HOUSEHOLD,
    PERSON,
    TAX_UNIT,
)
from axiom_microsim.run.relation_layout import (
    LEGACY_OWNER_SLOT,
    RelationLayout,
    _executable_slot_kinds,
    relation_layout,
)

R = FED_CTC_RELATION_NAME


def ctc_layout(artifact: dict) -> RelationLayout:
    return relation_layout(artifact, R, owner_kind=TAX_UNIT, member_kind=PERSON)


# --- Real compiled artifacts -------------------------------------------------


def test_untyped_artifact_keeps_the_legacy_member_first_order() -> None:
    layout = ctc_layout(load_artifact(CTC_UNTYPED))

    assert (layout.owner_slot, layout.basis, layout.declared) == (1, "usage", ())
    assert layout.tuple_for(owner_id="tu0", member_id="p0") == ["p0", "tu0"]
    assert layout.slot_kinds == (PERSON, TAX_UNIT)


def test_post_179_artifact_puts_the_tax_unit_first() -> None:
    layout = ctc_layout(load_artifact(CTC_TYPED))

    assert (layout.owner_slot, layout.basis) == (0, "usage")
    assert layout.declared == (TAX_UNIT, PERSON)
    assert layout.tuple_for(owner_id="tu0", member_id="p0") == ["tu0", "p0"]
    assert layout.slot_kinds == (TAX_UNIT, PERSON)


def test_declared_kinds_do_not_override_legacy_executable_slots() -> None:
    # Engines between #140 and #179 carried [TaxUnit, Person] but still
    # aggregated from slot 1. The engine's strict binding expects
    # [Person, TaxUnit] there, so the declaration alone would be wrong.
    layout = ctc_layout(load_artifact(CTC_TYPED_LEGACY_SLOTS))

    assert (layout.owner_slot, layout.basis) == (1, "usage")
    assert layout.declared == (TAX_UNIT, PERSON)


def test_co_snap_artifact_keeps_the_legacy_member_first_order() -> None:
    artifact = load_artifact(CO_SNAP_UNTYPED)
    # The artifact names the relation bare; requests use its public id.
    assert [r["name"] for r in artifact["program"]["relations"]] == ["member_of_household"]

    layout = relation_layout(
        artifact, CO_SNAP_RELATION_NAME, owner_kind=HOUSEHOLD, member_kind=PERSON
    )

    assert (layout.owner_slot, layout.basis) == (1, "usage")
    assert layout.tuple_for(owner_id="h0", member_id="p0") == ["p0", "h0"]


# --- Synthetic programs: how the reader walks an artifact --------------------


def _aggregate(current_slot: int, *, kind: str = "count_related", where=None) -> dict:
    node = {
        "kind": kind,
        "relation": R,
        "current_slot": current_slot,
        "related_slot": 1 - current_slot,
    }
    if kind == "sum_related":
        node["value"] = {"kind": "input", "name": "amount"}
    if where is not None:
        node["where"] = where
    return node


def _rule(name: str, entity: str, expr: dict, *, versions: list[dict] | None = None) -> dict:
    rule = {"name": name, "entity": entity, "semantics": "scalar", "expr": expr}
    if versions is not None:
        rule["versions"] = [
            {"effective_from": "2018-01-01", "semantics": "scalar", "expr": v} for v in versions
        ]
    return rule


def _program(*rules: dict, declared: list[str] | None = None, extra_relations=()) -> dict:
    relation = {"name": R, "arity": 2}
    if declared is not None:
        relation["slot_entities"] = declared
    return {"program": {"relations": [relation, *extra_relations], "derived": list(rules)}}


@pytest.mark.parametrize(
    ("declared", "owner_slot"),
    [([TAX_UNIT, PERSON], 0), ([PERSON, TAX_UNIT], 1)],
)
def test_unused_typed_relation_follows_its_declaration(declared, owner_slot) -> None:
    layout = ctc_layout(_program(declared=declared))
    assert (layout.owner_slot, layout.basis) == (owner_slot, "declared")


def test_unused_untyped_relation_keeps_the_legacy_order() -> None:
    layout = ctc_layout(_program())
    assert (layout.owner_slot, layout.basis) == (LEGACY_OWNER_SLOT, "legacy")
    assert layout == RelationLayout.legacy(R, owner_kind=TAX_UNIT, member_kind=PERSON)


def test_aggregate_nested_in_arithmetic_and_conditionals_votes() -> None:
    literal = {"kind": "literal", "value": {"kind": "integer", "value": 2}}
    expr = {
        "kind": "if",
        "condition": {
            "kind": "comparison",
            "left": _aggregate(0),
            "op": "gt",
            "right": literal,
        },
        "then_expr": {"kind": "mul", "left": _aggregate(0, kind="sum_related"), "right": literal},
        "else_expr": literal,
    }
    layout = ctc_layout(_program(_rule("credit", TAX_UNIT, expr)))
    assert (layout.owner_slot, layout.basis) == (0, "usage")


def test_member_side_use_votes_for_the_other_slot() -> None:
    # A Person rule aggregating over its tax unit reads itself from its
    # current slot, so the tax unit is in the related slot.
    layout = ctc_layout(_program(_rule("my_unit_size", PERSON, _aggregate(1))))
    assert layout.owner_slot == 0


def test_explicit_versions_replace_the_base_expression() -> None:
    # Runtime selection ignores the base semantics once versions exist.
    rule = _rule("n", TAX_UNIT, _aggregate(1), versions=[_aggregate(0)])
    assert ctc_layout(_program(rule)).owner_slot == 0


def test_relation_member_votes_with_the_enclosing_entity() -> None:
    member = {"kind": "relation_member", "relation": R, "current_slot": 0, "related_slot": 1}
    rule = {"name": "has", "entity": TAX_UNIT, "semantics": "judgment", "expr": member}
    assert ctc_layout(_program(rule)).owner_slot == 0


def test_use_through_a_derived_relation_votes_on_its_source() -> None:
    derived_relation = {
        "name": "qualifying_member",
        "arity": 2,
        "derivation": {
            "source_relation": R,
            "current_slot": 1,
            "related_slot": 0,
            "entity": TAX_UNIT,
            "slot_entities": [PERSON, TAX_UNIT],
            "predicate": {"kind": "derived", "name": "is_qualifying"},
        },
    }
    over_derived = {
        "kind": "count_related",
        "relation": "qualifying_member",
        "current_slot": 1,
        "related_slot": 0,
    }
    program = _program(
        _rule("n", TAX_UNIT, over_derived),
        declared=[TAX_UNIT, PERSON],
        extra_relations=[derived_relation],
    )
    assert ctc_layout(program).owner_slot == 1


# Nested and indirect uses. These mirror counterexamples an independent review
# ran against the strict #190 engine: the order chosen here is the one it binds.

TUH = "tax_unit_of_household"
IS_CHILD = {
    "name": "is_child",
    "entity": PERSON,
    "semantics": "judgment",
    "expr": {"kind": "derived", "name": "age_under_17"},
}
LITERAL_0 = {"kind": "literal", "value": {"kind": "integer", "value": 0}}


def _units_with_children(outer_current_slot: int, inner: dict, *, outer_declared=None) -> dict:
    """A Household rule counting its tax units for which ``inner`` is positive."""
    outer = {
        "kind": "count_related",
        "relation": TUH,
        "current_slot": outer_current_slot,
        "related_slot": 1 - outer_current_slot,
        "where": {"kind": "comparison", "left": inner, "op": "gt", "right": LITERAL_0},
    }
    tuh = {"name": TUH, "arity": 2}
    if outer_declared is not None:
        tuh["slot_entities"] = outer_declared
    return _program(
        IS_CHILD,
        _rule("units_with_children", HOUSEHOLD, outer),
        declared=[TAX_UNIT, PERSON],
        extra_relations=[tuh],
    )


def test_nested_use_evaluates_for_the_outer_related_entity() -> None:
    # #140-#179 shape: every aggregate legacy (1, 0). The inner count runs on
    # the tax unit in the outer relation's slot 0, so it sits in slot 1.
    legacy = _units_with_children(
        1, _aggregate(1, where=IS_CHILD["expr"]), outer_declared=[TAX_UNIT, HOUSEHOLD]
    )
    assert (ctc_layout(legacy).owner_slot, ctc_layout(legacy).basis) == (1, "usage")
    # Post-#179 shape of the same program: the inner count re-oriented to 0.
    post_179 = _units_with_children(
        1, _aggregate(0, where=IS_CHILD["expr"]), outer_declared=[TAX_UNIT, HOUSEHOLD]
    )
    assert ctc_layout(post_179).owner_slot == 0


def test_nested_use_in_an_untyped_relation_reads_its_predicate_entity() -> None:
    # #179 leaves the inner count at (1, 0) when the outer relation is untyped.
    # Its predicate reads a Person rule, so slot 0 holds the person and the
    # declaration completes slot 1 with the tax unit.
    program = _units_with_children(1, _aggregate(1, where={"kind": "derived", "name": "is_child"}))
    assert ctc_layout(program).owner_slot == 1


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "untyped"])
@pytest.mark.parametrize("pinned_slot", [None, 0, 1], ids=["alone", "beside-0", "beside-1"])
def test_a_use_that_pins_no_slot_raises(declared: bool, pinned_slot: int | None) -> None:
    # A bare count nested in an untyped relation's predicate: nothing says
    # which entity evaluates it. #179 re-orients only the uses whose entity it
    # can see, and a legacy compile puts whichever entity evaluates a use in
    # slot 1, so it may disagree with any pinned use, alone or not. Strict
    # binding cannot check it either.
    program = _units_with_children(1, _aggregate(1))
    if not declared:
        program["program"]["relations"][0].pop("slot_entities")
    if pinned_slot is not None:
        program["program"]["derived"].append(_rule("n", TAX_UNIT, _aggregate(pinned_slot)))
    with pytest.raises(ValueError, match=r"in \['units_with_children'\].*refusing to guess"):
        ctc_layout(program)


def test_a_derived_relations_own_definition_is_not_an_unpinned_use() -> None:
    # The definition names no kinds, but the aggregate over it is pinned
    # through the derivation's slots.
    derived_relation = {
        "name": "child_of_unit",
        "arity": 2,
        "derivation": {
            "source_relation": R,
            "current_slot": 1,
            "related_slot": 0,
            "predicate": {"kind": "derived", "name": "is_child"},
        },
    }
    over_derived = {
        "kind": "count_related",
        "relation": "child_of_unit",
        "current_slot": 1,
        "related_slot": 0,
    }
    program = _program(
        IS_CHILD, _rule("n", TAX_UNIT, over_derived), extra_relations=[derived_relation]
    )
    layout = ctc_layout(program)
    assert (layout.owner_slot, layout.basis) == (1, "usage")


def test_relation_member_under_a_comparison_drops_the_derived_relation_context() -> None:
    # In a derived relation's predicate, `and`/`or`/`not` keep the relation's
    # (current, related) kinds; a comparison resets them, so the membership
    # test runs on the related (Person) id.
    member = {"kind": "relation_member", "relation": R, "current_slot": 0, "related_slot": 1}
    one = {"kind": "literal", "value": {"kind": "integer", "value": 1}}
    branch = {"kind": "if", "condition": member, "then_expr": one, "else_expr": LITERAL_0}
    derived_relation = {
        "name": "d",
        "arity": 2,
        "derivation": {
            "source_relation": "claims",
            "current_slot": 0,
            "related_slot": 1,
            "entity": TAX_UNIT,
            "slot_entities": [TAX_UNIT, PERSON],
            "predicate": {"kind": "comparison", "left": branch, "op": "eq", "right": one},
        },
    }
    claims = {"name": "claims", "arity": 2, "slot_entities": [TAX_UNIT, PERSON]}
    program = _program(declared=[TAX_UNIT, PERSON], extra_relations=[claims, derived_relation])
    assert ctc_layout(program).owner_slot == 1

    derived_relation["derivation"]["predicate"] = {"kind": "and", "items": [member]}
    assert ctc_layout(program).owner_slot == 0


def test_related_slot_kind_comes_from_the_value_rule_entity() -> None:
    # Only the summed value's rule says what the related slot holds.
    total = _aggregate(1, kind="sum_related")
    total["value"] = {"kind": "derived", "name": "child_amount"}
    child_amount = {"name": "child_amount", "entity": PERSON, "semantics": "scalar", "expr": {}}
    program = _units_with_children(0, total)
    program["program"]["relations"][0].pop("slot_entities")
    program["program"]["derived"].append(child_amount)
    assert ctc_layout(program).owner_slot == 1


def test_use_from_an_unrelated_entity_is_not_attributable() -> None:
    # A Household rule counting the relation: neither slot can hold a tax unit.
    program = _program(_rule("n", HOUSEHOLD, _aggregate(0)), declared=[PERSON, TAX_UNIT])
    with pytest.raises(ValueError, match="refusing to guess"):
        ctc_layout(program)
    # Its predicate still says where the person goes.
    counted = _program(
        IS_CHILD,
        _rule("n", HOUSEHOLD, _aggregate(0, where={"kind": "derived", "name": "is_child"})),
    )
    assert ctc_layout(counted).owner_slot == 0


def test_aggregate_over_an_entity_alias_reads_the_derivations_declared_kind() -> None:
    # A rule of an alias entity counts a derived relation declared for that
    # entity: its current slot holds the declared kind, not the alias.
    # (Engine: executable_current_kind.)
    alias_relation = {
        "name": "filer_dependents",
        "arity": 2,
        "derivation": {
            "source_relation": R,
            "current_slot": 1,
            "related_slot": 0,
            "entity": "Filer",
            "slot_entities": [PERSON, TAX_UNIT],
            "predicate": {"kind": "derived", "name": "is_child"},
        },
    }
    over_alias = {
        "kind": "count_related",
        "relation": "filer_dependents",
        "current_slot": 1,
        "related_slot": 0,
    }
    program = _program(
        IS_CHILD,
        _rule("n", "Filer", over_alias),
        declared=[TAX_UNIT, PERSON],
        extra_relations=[alias_relation],
    )
    assert ctc_layout(program).owner_slot == 1
    # The derivation's own record votes the same way, so check the rule's.
    relations = {r["name"]: r for r in program["program"]["relations"]}
    uses = _executable_slot_kinds(program["program"], relations, R)
    assert uses.slot_kinds[1] == {TAX_UNIT: {"n", "filer_dependents"}}


def test_scalar_rules_do_not_name_the_related_entity() -> None:
    # Nested where nothing else pins it, a sum's related kind comes from the
    # rules it reads; a Scalar rate says nothing about which entity that is.
    total = _aggregate(1, kind="sum_related", where={"kind": "derived", "name": "is_child"})
    total["value"] = {"kind": "derived", "name": "rate"}
    rate = {"name": "rate", "entity": "Scalar", "semantics": "scalar", "expr": LITERAL_0}
    program = _units_with_children(1, total)
    program["program"]["derived"].append(rate)
    assert ctc_layout(program).owner_slot == 1


def test_definitions_chained_through_derived_relations_are_not_unpinned_uses() -> None:
    def derived(name: str, source: str) -> dict:
        return {
            "name": name,
            "arity": 2,
            "derivation": {
                "source_relation": source,
                "current_slot": 1,
                "related_slot": 0,
                "predicate": {"kind": "derived", "name": "is_child"},
            },
        }

    program = _program(
        IS_CHILD,
        _rule("n", TAX_UNIT, _aggregate(0)),
        declared=[TAX_UNIT, PERSON],
        extra_relations=[derived("d1", R), derived("d2", "d1")],
    )
    assert ctc_layout(program).owner_slot == 0


def test_only_an_evaluated_derived_relations_predicate_counts() -> None:
    member = {"kind": "relation_member", "relation": R, "current_slot": 1, "related_slot": 0}
    over_households = {
        "name": "d",
        "arity": 2,
        "derivation": {
            "source_relation": TUH,
            "current_slot": 1,
            "related_slot": 0,
            "predicate": member,
        },
    }
    program = _program(
        _rule("n", TAX_UNIT, _aggregate(0)),
        declared=[TAX_UNIT, PERSON],
        extra_relations=[{"name": TUH, "arity": 2}, over_households],
    )
    # Nothing reads `d`, so its predicate affects no output.
    assert ctc_layout(program).owner_slot == 0

    over_d = {"kind": "count_related", "relation": "d", "current_slot": 1, "related_slot": 0}
    program["program"]["derived"].append(_rule("m", HOUSEHOLD, over_d))
    with pytest.raises(ValueError, match=r"in \['d'\].*refusing to guess"):
        ctc_layout(program)


def test_conflicting_uses_raise_instead_of_guessing() -> None:
    program = _program(_rule("a", TAX_UNIT, _aggregate(0)), _rule("b", TAX_UNIT, _aggregate(1)))
    with pytest.raises(ValueError, match="no tuple order is right for every use") as excinfo:
        ctc_layout(program)
    assert "'a'" in str(excinfo.value) and "'b'" in str(excinfo.value)


def test_missing_relation_raises() -> None:
    with pytest.raises(ValueError, match="declares no relation"):
        relation_layout(_program(), "us:x#relation.absent", owner_kind=TAX_UNIT, member_kind=PERSON)


def test_non_binary_relation_raises() -> None:
    artifact = {"program": {"relations": [{"name": R, "arity": 3}], "derived": []}}
    with pytest.raises(ValueError, match="arity 3"):
        ctc_layout(artifact)


def test_owner_and_member_kinds_must_differ() -> None:
    with pytest.raises(ValueError, match="must differ"):
        relation_layout(_program(), R, owner_kind=PERSON, member_kind=PERSON)
