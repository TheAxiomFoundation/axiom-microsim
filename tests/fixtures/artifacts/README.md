# Compiled artifact fixtures

Real engine output, committed verbatim so the tuple-order tests run in CI
without the Rust binary. Do not hand-edit them. A test that needs another
artifact shape derives it in code and says so.

The three CTC artifacts are compiled from the same source,
rulespec-us `d9a03f17` `statutes/26/24/h.yaml` (git blob `abb7c3c7`). That is
the rulespec-us SHA `modal_app.py` pins. It declares `dependent_of_tax_unit`
with `arguments: [TaxUnit, Person]`.

| File | Compiled by | Relation shape |
|---|---|---|
| `federal-ctc.engine-v0.1.1.compiled.json` | axiom-rules-engine 0.1.1 (release/v0.1 line) | no `slot_entities`; `count_related` with `current_slot: 1` (legacy) |
| `federal-ctc.engine-v0.2.2.compiled.json` | release v0.2.2 (`2c0e1ed`, 2026-08-20), which has #140 but not #179 | `slot_entities: [TaxUnit, Person]`, but `count_related` with `current_slot: 1`; the compiler warns `relation_orientation_mismatch` |
| `federal-ctc.engine-main-5a29e03.compiled.json` | axiom-rules-engine main `5a29e03` (after #179) | `slot_entities: [TaxUnit, Person]`; `current_slot: 0` |
| `co-snap.engine-9106f44.compiled.json.gz` | axiom-rules-engine `9106f44`, the SHA `scripts/setup_engine.sh` pins (co-snap-cliffs `scripts/build-artifacts.sh`, 2026-05-15) | `member_of_household`, untyped; five Household `count_related` nodes with `current_slot: 1` |

Notes:

* Main `5a29e03` and release v0.2.2 both write `engine_version: "0.2.2"`, so
  that field cannot tell a post-#179 artifact from a pre-#179 one. The slots
  can.
* The v0.2.2 binary is the release's `aarch64-apple-darwin` asset (tarball
  sha256 `42cd7441…63a7d8`, matching the release's `.sha256` file).
* The strict-binding engine from axiom-rules-engine#190 compiles `h.yaml` to a
  file byte-identical to the main `5a29e03` fixture.
* The CO SNAP artifact is gzipped. It predates `artifact_format_version`, so
  current engines do not load it. The tests use it only to read tuple order.
* v0.2.2 and later engines need the jurisdiction layout, so `h.yaml` was
  placed at `rulespec-us/us/statutes/26/24/h.yaml` for them. They also need
  `--rulespec-root` as an absolute, canonical path:

```sh
v0.1.1 compile --program /abs/rulespec-us/statutes/26/24/h.yaml \
  --output federal-ctc.engine-v0.1.1.compiled.json
v0.2.2 compile --program /abs/rulespec-us/us/statutes/26/24/h.yaml \
  --rulespec-root /abs/rulespec-us --output federal-ctc.engine-v0.2.2.compiled.json
main-5a29e03 compile --program /abs/rulespec-us/us/statutes/26/24/h.yaml \
  --rulespec-root /abs/rulespec-us --output federal-ctc.engine-main-5a29e03.compiled.json
```
