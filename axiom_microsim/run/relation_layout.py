"""Tuple order for the two-slot relations this producer sends, read from the artifact.

The compiled artifact, not this producer, decides which tuple slot holds which
entity. axiom-rules-engine#179 (merged 2026-09-18) made an artifact compiled
from typed RuleSpec aggregate a two-slot relation in its declared argument
order. §24(h) declares ``dependent_of_tax_unit`` with ``arguments: [TaxUnit,
Person]``, so a post-#179 artifact counts dependents with ``current_slot: 0``
and needs ``[tax_unit, person]`` tuples. Older artifacts aggregate every
relation in the legacy direction: current entity in slot 1, related entity in
slot 0 (``infer_slots`` in the engine's ``src/formula.rs``). That is the
``[person, tax_unit]`` order this producer always sent. Sent to the other kind
of artifact, a tuple is counted on the wrong slot and the result is wrong with
exit 0. axiom-rules-engine#190 turns that into a binding error by default.

So the order is read from the artifact the request runs against. The kinds
each slot must hold come from the same inference the engine's strict binding
uses (``docs/rulespec.md``, "Compiled artifacts carry declared argument kinds";
``relation_usage_records`` in ``src/model.rs``, axiom-rules-engine#190):

1. Executable usage decides for a relation the program uses. Each
   ``count_related``, ``sum_related`` or ``relation_member`` node on the
   relation, directly or through a derived relation whose source it is, puts
   its evaluating entity in its ``current_slot``. Membership only contributes
   inside a derived relation's predicate, using its bound (current, related)
   ids. Scalar operands preserve that binding; nested aggregate predicates
   and period reductions clear it. The aggregating entity is the enclosing
   rule's, or the related entity of the aggregate whose predicate contains
   the node. The entity of the derived rules its value and predicate read goes
   in its ``related_slot``. The declared kinds fill a single slot left unknown.
   This covers artifacts compiled between axiom-rules-engine#140 and #179,
   which declare kinds but still aggregate in the legacy direction.
2. Declared ``slot_entities``, only for a relation the program never evaluates.
3. Otherwise the legacy direction, which reproduces today's requests exactly.

The owner id goes in the slot whose inferred kind is the owner's, or opposite
the slot whose kind is the member's. Three cases deliberately raise where the
engine would run, or fail less clearly:

* Uses that disagree about the owner's slot. The engine skips the conflicting
  slots, and some output is wrong under either order.
* A use that pins neither slot, unless the relation is untyped and its known
  uses permit legacy order. For example, a count nested in an untyped relation's
  predicate with no ``where``. #179 re-orients only the uses whose entity it
  can see, and a legacy compile puts whichever entity evaluates a
  use in slot 1, so it may disagree with the other uses. Strict binding cannot
  check it either. A derived relation's own definition is not such a use (the
  aggregates over it are), nor is the predicate of a derived relation nothing
  evaluates. Untyped relations preserve the legacy direction when known uses
  agree with it or supply no orientation; an unpinned use alone does not
  disprove the working legacy order.
* A relation evaluated only by uses that put neither the owner's nor the
  member's kind in any slot. No order of these ids serves them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import orjson

# Pre-#179 convention: the aggregating (current) entity is in slot 1.
LEGACY_OWNER_SLOT = 1

_AGGREGATES = ("count_related", "sum_related")
# Derived rules of this entity have no per-entity kind (engine: SCALAR_ENTITY).
_SCALAR_ENTITY = "Scalar"


@dataclass(frozen=True)
class RelationLayout:
    """Where the owner id (tax unit, household) and member id (person) go."""

    relation: str
    owner_kind: str
    member_kind: str
    owner_slot: int
    basis: Literal["usage", "declared", "legacy"]
    declared: tuple[str, ...] = ()

    @classmethod
    def legacy(cls, relation: str, *, owner_kind: str, member_kind: str) -> RelationLayout:
        return cls(relation, owner_kind, member_kind, LEGACY_OWNER_SLOT, "legacy")

    @property
    def member_slot(self) -> int:
        return 1 - self.owner_slot

    @property
    def slot_kinds(self) -> tuple[str, str]:
        """The entity kind each tuple slot receives, in slot order."""
        kinds = [self.member_kind, self.member_kind]
        kinds[self.owner_slot] = self.owner_kind
        return kinds[0], kinds[1]

    def tuple_for(self, owner_id: str, member_id: str) -> list[str]:
        pair = [member_id, member_id]
        pair[self.owner_slot] = owner_id
        return pair


def relation_layout_for_artifact(
    artifact_path: Path | str, relation: str, *, owner_kind: str, member_kind: str
) -> RelationLayout:
    artifact = orjson.loads(Path(artifact_path).read_bytes())
    return relation_layout(artifact, relation, owner_kind=owner_kind, member_kind=member_kind)


def relation_layout(
    artifact: Mapping[str, Any], relation: str, *, owner_kind: str, member_kind: str
) -> RelationLayout:
    """Read the tuple order for ``relation`` from a compiled artifact."""
    if owner_kind == member_kind:
        raise ValueError(f"owner and member kinds must differ, both are {owner_kind!r}")
    program = artifact.get("program", artifact)
    relations = {r["name"]: r for r in program.get("relations") or []}
    name = _resolve_relation_name(relations, relation)
    if name is None:
        raise ValueError(f"compiled artifact declares no relation {relation!r}")
    schema = relations[name]
    if schema.get("arity") != 2:
        raise ValueError(f"relation {name!r} has arity {schema.get('arity')}, expected 2")
    declared = tuple(schema.get("slot_entities") or ())

    uses = _executable_slot_kinds(program, relations, name)
    votes: dict[int, set[str]] = {}
    for slot, kinds in enumerate(uses.slot_kinds):
        for kind, citing in kinds.items():
            if kind == owner_kind:
                votes.setdefault(slot, set()).update(citing)
            elif kind == member_kind:
                votes.setdefault(1 - slot, set()).update(citing)
    if len(votes) > 1:
        raise ValueError(
            f"compiled artifact aggregates relation {name!r} with the {owner_kind} id in "
            f"slot 0 (in {sorted(votes[0])}) and in slot 1 (in {sorted(votes[1])}); "
            "no tuple order is right for every use"
        )
    # Preserve old untyped requests only when known uses permit legacy order.
    # An unpinned nested use can conflict with a known nonlegacy orientation.
    if uses.unpinned and (declared or 1 - LEGACY_OWNER_SLOT in votes):
        raise ValueError(
            f"compiled artifact uses relation {name!r} in {sorted(uses.unpinned)} without "
            f"showing which slot holds the {owner_kind} id, and strict binding cannot check "
            "that use either; refusing to guess"
        )
    if votes:
        (owner_slot,) = votes
        return RelationLayout(relation, owner_kind, member_kind, owner_slot, "usage", declared)
    if uses.unpinned and not any(uses.slot_kinds):
        return RelationLayout(relation, owner_kind, member_kind, LEGACY_OWNER_SLOT, "legacy")
    if uses.evaluated:
        raise ValueError(
            f"compiled artifact uses relation {name!r} in {sorted(uses.evaluated)}, but no use "
            f"puts a {owner_kind} or {member_kind} in either slot; refusing to guess"
        )

    if len(declared) == 2:
        if declared.count(owner_kind) == 1:
            owner_slot = declared.index(owner_kind)
            return RelationLayout(
                relation, owner_kind, member_kind, owner_slot, "declared", declared
            )
        if declared.count(member_kind) == 1:
            owner_slot = 1 - declared.index(member_kind)
            return RelationLayout(
                relation, owner_kind, member_kind, owner_slot, "declared", declared
            )
    return RelationLayout(relation, owner_kind, member_kind, LEGACY_OWNER_SLOT, "legacy", declared)


def _resolve_relation_name(relations: Mapping[str, Any], reference: str) -> str | None:
    # The engine accepts an exact relation name, or a public id whose
    # `#relation.<name>` fragment names one (Program::resolve_relation_name).
    if reference in relations:
        return reference
    _, sep, fragment = reference.partition("#relation.")
    if sep and fragment in relations:
        return fragment
    return None


@dataclass
class _Uses:
    """Entity kinds the program's uses of one relation put in each slot."""

    # Rules with any use of the relation (the engine's notion of "used"), those
    # that evaluate it, and those whose use leaves both slots' kinds unknown. A
    # derived relation's definition, or the predicate of one nothing evaluates,
    # does not evaluate it.
    recorded: set[str] = field(default_factory=set)
    evaluated: set[str] = field(default_factory=set)
    unpinned: set[str] = field(default_factory=set)
    # slot_kinds[slot][kind] = rules whose use puts ``kind`` in ``slot``.
    slot_kinds: tuple[dict[str, set[str]], dict[str, set[str]]] = field(
        default_factory=lambda: ({}, {})
    )


def _executable_slot_kinds(
    program: Mapping[str, Any], relations: Mapping[str, Any], target: str
) -> _Uses:
    """The kinds each use of ``target`` puts in each slot, as the engine infers them.

    A port of ``relation_usage_records`` in the engine's ``src/model.rs``
    (axiom-rules-engine#190), walking the artifact's serialized expressions.
    """
    derived_entity = {rule["name"]: rule.get("entity") for rule in program.get("derived") or []}
    live = _evaluated_relations(program, relations)
    uses = _Uses()

    def add_usage(
        relation: str,
        current_slot: int,
        related_slot: int,
        current: str | None,
        related: str | None,
        citing: str,
        stack: tuple[str, ...] = (),
        *,
        evaluates: bool = True,
    ) -> list[str | None]:
        # Engine: add_relation_usage. ``evaluates`` is False only for a derived
        # relation's definition, whose aggregates are recorded through it.
        schema = relations.get(relation)
        if schema is None:
            return []
        slots: list[str | None] = [None] * schema.get("arity", 2)
        if current_slot < len(slots):
            slots[current_slot] = current
        if related_slot < len(slots):
            slots[related_slot] = related
        _complete_single_unknown_slot(schema.get("slot_entities") or [], slots)
        if relation == target:
            uses.recorded.add(citing)
            for slot, kind in enumerate(slots[:2]):
                if kind is not None:
                    uses.slot_kinds[slot].setdefault(kind, set()).add(citing)
            if evaluates:
                uses.evaluated.add(citing)
                if not any(slots[:2]):
                    uses.unpinned.add(citing)
        derivation = schema.get("derivation")
        if derivation and relation not in stack:
            # A use of a derived relation also uses its source relation.
            add_usage(
                derivation["source_relation"],
                derivation["current_slot"],
                derivation["related_slot"],
                _at(slots, current_slot),
                _at(slots, related_slot),
                citing,
                (*stack, relation),
                evaluates=evaluates,
            )
        return slots

    def current_kind(relation: str, current_slot: int, owner: str | None) -> str | None:
        # Engine: executable_current_kind.
        derivation = (relations.get(relation) or {}).get("derivation")
        if derivation and owner is not None and derivation.get("entity") == owner:
            return _at(derivation.get("slot_entities") or [], current_slot)
        return owner

    def related_kind(node: Mapping[str, Any]) -> str | None:
        # Engine: related_entity_kind. The one entity of the derived rules the
        # aggregate's value and predicate read, outside nested aggregates.
        kinds: set[str] = set()
        _referenced_entities(node.get("value"), derived_entity, kinds)
        _referenced_entities(node.get("where"), derived_entity, kinds)
        return kinds.pop() if len(kinds) == 1 else None

    def visit(
        node: Any,
        entity: str | None,
        relation_context: tuple[str | None, str | None] | None,
        citing: str,
        evaluates: bool = True,
    ) -> None:
        # Engine: collect_scalar_relation_usages / collect_judgment_relation_usages.
        if isinstance(node, list):
            for item in node:
                visit(item, entity, relation_context, citing, evaluates)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind in _AGGREGATES:
            relation = node["relation"]
            slots = add_usage(
                relation,
                node["current_slot"],
                node["related_slot"],
                current_kind(relation, node["current_slot"], entity),
                related_kind(node),
                citing,
                evaluates=evaluates,
            )
            # The predicate runs on the related id.
            visit(node.get("where"), _at(slots, node["related_slot"]), None, citing, evaluates)
            return
        if kind == "relation_member":
            # Membership needs the two ids bound by a derived predicate.
            # Outside it the evaluator rejects the test, so no orientation
            # follows from the enclosing entity alone.
            if relation_context is None:
                return
            current, related = relation_context
            add_usage(
                node["relation"],
                node["current_slot"],
                node["related_slot"],
                current,
                related,
                citing,
                evaluates=evaluates,
            )
            return
        if kind == "no_match":
            # Patterns label an error; only the subject is evaluated.
            visit(node.get("subject"), entity, relation_context, citing, evaluates)
            return
        if kind == "over_periods":
            # Period reductions never execute in a derived predicate's context.
            visit(node.get("value"), entity, None, citing, evaluates)
            visit(node.get("n"), entity, None, citing, evaluates)
            return
        # Scalar operands, comparisons and judgment combinators all keep
        # the same pair of bound ids (engine: collect_*_relation_usages).
        for value in node.values():
            visit(value, entity, relation_context, citing, evaluates)

    for rule in program.get("derived") or []:
        citing = rule.get("id") or rule["name"]
        # Runtime version selection ignores the base semantics whenever
        # explicit versions exist; so does the engine's usage walk.
        versions = rule.get("versions") or []
        for expr in [v.get("expr") for v in versions] if versions else [rule.get("expr")]:
            visit(expr, rule.get("entity"), None, citing)

    for relation_name, schema in relations.items():
        derivation = schema.get("derivation")
        if not derivation:
            continue
        declared = derivation.get("slot_entities") or []
        current = _at(declared, derivation["current_slot"])
        related = _at(declared, derivation["related_slot"])
        add_usage(
            derivation["source_relation"],
            derivation["current_slot"],
            derivation["related_slot"],
            current,
            related,
            relation_name,
            evaluates=False,
        )
        # The predicate of a derived relation nothing evaluates affects no output.
        visit(
            derivation.get("predicate"),
            related,
            (current, related),
            relation_name,
            relation_name in live,
        )

    return uses


def _evaluated_relations(program: Mapping[str, Any], relations: Mapping[str, Any]) -> set[str]:
    """Relations some rule reads, directly or through the derived relations it reads."""
    live: set[str] = set()
    for rule in program.get("derived") or []:
        _referenced_relations(rule.get("versions") or rule.get("expr"), live)
    frontier = list(live)
    while frontier:
        derivation = (relations.get(frontier.pop()) or {}).get("derivation")
        if not derivation:
            continue
        reached = {derivation["source_relation"]}
        _referenced_relations(derivation.get("predicate"), reached, relation_context=True)
        frontier.extend(reached - live)
        live |= reached
    return live


def _referenced_relations(node: Any, out: set[str], *, relation_context: bool = False) -> None:
    if isinstance(node, list):
        for item in node:
            _referenced_relations(item, out, relation_context=relation_context)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("kind")
    if kind in _AGGREGATES:
        out.add(node["relation"])
        _referenced_relations(node.get("where"), out)
        return
    if kind == "relation_member":
        if relation_context:
            out.add(node["relation"])
        return
    if kind == "no_match":
        _referenced_relations(node.get("subject"), out, relation_context=relation_context)
        return
    if kind == "over_periods":
        _referenced_relations(node.get("value"), out)
        _referenced_relations(node.get("n"), out)
        return
    for value in node.values():
        _referenced_relations(value, out, relation_context=relation_context)


def _referenced_entities(node: Any, derived_entity: Mapping[str, Any], out: set[str]) -> None:
    # Engine: collect_referenced_derived_entities / collect_scalar_derived_entities.
    if isinstance(node, list):
        for item in node:
            _referenced_entities(item, derived_entity, out)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("kind")
    if kind in _AGGREGATES:
        return  # a nested aggregate reads its own related ids
    if kind == "derived":
        entity = derived_entity.get(node.get("name"))
        if entity is not None and entity != _SCALAR_ENTITY:
            out.add(entity)
        return
    # This separate entity-reference inference also inspects no_match patterns,
    # matching the reference; executable usage and liveness visit only subject.
    for value in node.values():
        _referenced_entities(value, derived_entity, out)


def _complete_single_unknown_slot(declared: list[str], slots: list[str | None]) -> None:
    # Engine: complete_single_unknown_slot. The declaration, as a multiset of
    # kinds, fills a single unknown slot once the known ones are removed.
    if len(declared) != len(slots):
        return
    unknown = [slot for slot, kind in enumerate(slots) if kind is None]
    if len(unknown) != 1:
        return
    remaining = list(declared)
    for kind in slots:
        if kind is None:
            continue
        if kind not in remaining:
            return
        remaining.remove(kind)
    if len(remaining) == 1:
        slots[unknown[0]] = remaining[0]


def _at(values: list[Any], index: int) -> Any:
    return values[index] if 0 <= index < len(values) else None
