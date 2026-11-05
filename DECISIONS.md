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

**Decision.** Public reforms use the finite numeric lever catalogue described
in D8. The local runner also accepts richer `ParameterOverride` patches; it
patches the imported RuleSpec YAML on a per-request scratch tree
and recompiles (~70 ms). Output arrays are produced for baseline and reform
in the same request.

**Why.** The scratch-tree mechanism comes from `axiom-co-snap`. Public callers
need only the checked-in web sliders, so their narrower contract does not
expose the local runner's formula text or individual table-cell edits.

**How to apply.** Add public levers to both the frontend and D8's backend
catalogue. The request body is `{program, state, year, overrides}`.

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

## D8 — Public requests follow the web lever catalogue

**Decision.** `axiom_microsim/public_contract.py` accepts exact catalogue
targets and only `scale_values`, `scale_formula`, and `set_scalar`. Formula
text and individual table cells are refused. Uniform table scaling preserves
threshold order. `scale_formula` multiplies the numeric literal in the rules
tree; there is no Python copy of the additional-member baseline. `set_scalar`
formats a validated number as a literal in the local runtime's `set_formula`.

These numeric bounds come directly from the named sliders in
`web/src/lib/levers.ts`, including both endpoints:

| Slider | Axiom targets | Public range | PE counterpart |
|---|---|---|---|
| `max_allotment_scale` | SNAP maximum-allotment table and additional-member literal | multiplier 0.5–2 | none |
| `standard_deduction_scale` | SNAP standard-deduction table, 48 states/DC | multiplier 0–2 | none |
| `all_rates_scale` | All seven federal income-tax bracket rates | multiplier 0.5–1.5 | each baseline rate (0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37) times 0.5–1.5 |
| `joint_thresholds_scale` | Federal joint bracket thresholds | multiplier 0.5–2 | none |
| `single_thresholds_scale` | Federal single bracket thresholds | multiplier 0.5–2 | none |
| `ctc_child_amount` | CTC child amount | $0–$6,000 | `gov.irs.credits.ctc.amount.base[0].amount`, same range |
| `ctc_other_dependent_amount` | CTC other-dependent amount | $0–$2,500 | `gov.irs.credits.ctc.amount.adult_dependent`, same range |
| `ctc_joint_phase_out` | CTC joint phase-out threshold | $50,000–$1,000,000 | `gov.irs.credits.ctc.phase_out.threshold.JOINT`, same range |

These are continuous ranges; slider steps are presentation choices. Lever
payloads must be finite numbers; numeric strings and booleans are refused. The
two POST routes accept only normalized `CO` for `co-snap`, matching its frontend
`state_choices`. Federal programs accept the loader's 50 states plus DC and
`US`. Both routes accept only 2026, the year the web sends and the server
prewarms. A request for any other year would start a full run for a result the
site never shows, and the compare cache, the baseline cache and the PE disk
cache would each keep a copy.

Both public POST routes retain at most 16 overrides and 16 KiB of actual
body bytes. These headroom limits cover all simultaneous frontend levers
(nine Axiom targets, ten PE paths) while bounding parsing and work. Duplicate
targets are refused. File, parameter, and PE path lengths (200, 100, and 120
characters) allow the catalogue names with headroom; exact membership remains
required. State normalization and numeric checks run without reading rules.

**Deployment bound.** The Modal web function allows at most two containers,
each with four input slots and eight CPUs. No per-process work semaphore is
used: a web click starts `/microsim` and `/compare` together, and `_prewarm`
calls those handlers directly. A one-slot refusal would reject normal use.
The container cap bounds public scaling without that conflict.

**How to apply.** When a slider, target, or kind changes, update
`web/src/lib/levers.ts`, `web/src/lib/types.ts`, and the Axiom/PE catalogues
in `axiom_microsim/public_contract.py`. Update this table and
`tests/test_public_contract.py::test_every_frontend_lever_accepts_both_extremes`;
that test runs the real builders under Node, so keep its endpoint count in
sync with added or removed sliders. When the rules pin changes, update
`RULESPEC_US_SHA` and `ENGINE_VERSION` in `modal_app.py`, reconcile catalogue
paths/kinds with the new YAML, and run
`tests/test_public_rules.py::test_catalogued_target_exists_and_accepts_its_kinds`
against that pin. It checks every target's first-version shape and numeric
formulas, and skips only when the rules tree is absent. Keep the runtime scale
properties and public refusal properties green. The AST deployment test in
`tests/test_runtime_consistency.py` guards the container cap.

**Still open from issue #10.** Authentication, tightening CORS, mounting
rules read-only, and compile sandboxing remain separate work. The catalogue
and container cap do not implement those controls.
