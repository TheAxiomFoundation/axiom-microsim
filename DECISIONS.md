# Decisions

## D1 — Strictly PE-free runtime

**Decision.** No `policyengine_*` import anywhere in the request path. The
Enhanced CPS `.h5` is read with `h5py` directly; rule evaluation goes through
`axiom_rules_engine.dense.CompiledDenseProgram`.

**Why.** The whole point of the Axiom stack is to run policy rules without
coupling to PE's internals. `axiom-programs` already permits PE for input
loading; `axiom-microsim` does not, so we can claim a zero-PE production path
and ship it under that banner.

**How to apply.** If a future need surfaces a derived ECPS variable PE
computes (SPM threshold, AGI, OASDI breakdowns), we either (a) bake it into
a one-off offline dataset artifact in R2 — using PE *once* in build time, then
never — or (b) encode it in `rulespec-us`. We do not call PE at runtime.

## D2 — Dense columnar batch eval

**Decision.** The runtime uses `CompiledDenseProgram.execute(inputs=…,
relations=…)` with numpy column inputs, not the per-case `ExecutionRequest`
JSON path the co-snap binary uses.

**Why.** The whole-state ECPS is on the order of 10⁴ households per state;
per-case JSON serialisation + subprocess pipes would dominate. The dense
path is one Rust call with already-laid-out numpy buffers.

**How to apply.** Every program added to this repo needs a dense projection
layer (`project/<slug>.py`). The projection is hand-coded for v1; if we add
many programs we'll generalise.

## D3 — v1 omits SPM / poverty impact

**Decision.** v1 ships cost / distributional / winners-losers. SPM impact is
deferred.

**Why.** SPM thresholds and SPM-unit composition are PE-derived variables;
neither is in the raw ECPS. Replicating SPM in `rulespec-us` is multi-week
scope; baking SPM thresholds into a dataset is the more likely path but
requires a separate decision on artifact format and refresh cadence.

**How to apply.** When SPM lands, the choice is encoded here as D7 or
similar. Until then, the API surface omits poverty fields rather than
returning placeholder zeros.

## D4 — Reform mechanism: in-memory RuleSpec YAML patch

**Decision.** Reforms are expressed as a list of `{path, value}` overrides;
the runner patches the imported RuleSpec YAML on a per-request scratch tree
and recompiles (~70 ms). Output arrays are produced for baseline and reform
in the same request.

**Why.** This is exactly what `axiom-co-snap` does. Reusing the pattern
keeps the contract identical between the household sweep app and the
microsim app, so a reform that "looks right" in co-snap can be moved here
unchanged.

**How to apply.** New reformable parameters should be exposed as parameter
paths in the frontend; the request body shape stays
`{program, scope, year, overrides}`.

## D5 — Repo boundary

**Decision.** New repo `axiom-microsim`. `axiom-programs` stays the
oracle/comparator harness; `axiom-microsim` owns weighted aggregation and
the production-style FastAPI/Modal path.

**Why.** `axiom-programs` already mixes a PE-via-h5 input path with its
oracle comparator. Mixing in a strictly-PE-free production microsim would
muddle its identity.

**How to apply.** Concept and case primitives that are useful to both
(`Concepts`, `Case`) can be lifted into a shared package later if drift
becomes painful. For v1 we duplicate just the small piece needed and move
on.

## D6 — Same FastAPI app, two transports

**Decision.** `axiom_microsim/server.py` defines one FastAPI app.
`modal_app.py` mounts it under `@modal.asgi_app()`. Locally it runs under
`uvicorn`. The Next.js app talks to `AXIOM_MICROSIM_URL` regardless of
which transport is on the other side.

**Why.** Halves the surface area to test. Avoids the trap where Modal
diverges silently from local dev.

## D7 — Relation tuple order comes from the compiled artifact

**Decision.** Two-slot relation tuples (`dependent_of_tax_unit` for the
CTC, `member_of_household` for CO SNAP) are ordered by the compiled
artifact the request runs against, not by the producer.
`axiom_microsim/run/relation_layout.py` reads the order. It ports the
engine's own inference (`relation_usage_records`, axiom-rules-engine#190).
Each `count_related` or `sum_related` node on the relation puts its evaluating
entity in its `current_slot`. That holds directly or through a derived relation.
`relation_member` uses only the two ids bound by a derived relation's predicate;
scalar operands, comparisons, `if` conditions and branches, and arithmetic
preserve that context. Nested aggregate filters and period reductions clear it.
Membership outside that context implies no orientation. Only `no_match.subject`
is an executable use; its patterns label errors. The entity of the rules its
value and predicate read goes in its `related_slot`. The owner goes wherever
those kinds place it.
The declared `slot_entities` decide only for a relation the program never
evaluates. With neither, the legacy slot 1 applies. Uses that disagree, or a
typed relation's use that pins no slot, raise rather than guess. Untyped relations
with unpinned uses keep legacy slot 1 when known uses permit that order. Known
nonlegacy uses still block this compatibility fallback.
Input records carry their real entity kinds (`TaxUnit`, `Household`, `Person`).

**Why.** axiom-rules-engine#179 made artifacts compiled from typed RuleSpec
aggregate in declared argument order. §24(h) declares `[TaxUnit, Person]`,
so a post-#179 artifact needs `[tax_unit, person]`. Older artifacts,
including those from the engine `modal_app.py` pins, aggregate from slot 1
and need `[person, tax_unit]`. The wrong order counts no dependents and
exits 0. axiom-rules-engine#190 rejects it under strict binding when the
artifact declares slot kinds, but not for untyped artifacts. Artifacts
compiled between #140 and #179 declare `[TaxUnit, Person]` yet still
aggregate from slot 1, so the declaration alone is not enough (issue #23).

**How to apply.** A new relation-emitting builder takes a `RelationLayout`
from `relation_layout_for_artifact(...)` and builds tuples with
`layout.tuple_for(owner_id=..., member_id=...)`. Request caches key on
`layout.slot_kinds`. Never send `"relation_binding": "lenient"`.
