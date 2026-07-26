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

## D7 — The CTC headline is the credit after phase-out, not the final value

**Decision.** `federal-ctc` runs the parent `statutes/26/24.yaml` program and
reports `ctc_before_advance_payments`: the §24 credit after the §24(b)(1)
income phase-out, using §24(h)'s post-2017 amounts. The PolicyEngine
comparison requests `ctc`, the same quantity. Neither side applies the
§26(a) tax-liability limitation or the §24(d) refundable split.

**Why.** v1 summed `ctc_maximum_before_phase_out_under_subsection_h` while
the UI called it "Annual CTC cost" and offered a phase-out-threshold
slider, and the comparison asked PolicyEngine for post-phase-out
`ctc_value` (issue #11). Moving the slider changed PE's number and not
Axiom's, which reads as the engines disagreeing when they were computing
different quantities.

Going further — to PE's `ctc_value` — is not a labelling problem, it is a
modelling one: `ctc_value = min(ctc, ctc_limiting_tax_liability +
refundable_ctc)`, and `ctc_limiting_tax_liability` is income tax before
credits net of *every other* non-refundable credit, computed on a
no-SALT branch. This microsim's §1(j) leg is ordinary-rate tax only. A
partial liability limit would have replaced a visible mismatch with an
invisible one.

**How to apply.** The headline output is named once, as
`FED_CTC_HEADLINE_OUTPUT` in `run/microsim.py`. The label and the
PolicyEngine variable travel with it in `server.MEASURES` and reach the UI
in the response's `measure` block — a client that captions the number
locally can drift from it again, so don't. When the liability limit and
refundable split are modelled, change all three in that one registry.
