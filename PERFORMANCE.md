# Performance audit

> Branch: `perf-audit`. Numbers measured on Apple M-series, local
> filesystem, warm Python process. Network round-trip from a browser
> through Vercel → Modal adds ~300–800 ms on top of every figure here.

## Headline

| Program · scope · kind | Total | Engine subprocess | Dominant cost |
|---|---|---|---|
| **Federal CTC** · US · baseline | **3.4 s** | 2.4 s (69%) | engine + JSON I/O |
| Federal CTC · US · reform | 3.4 s | 2.3 s (69%) | engine + JSON I/O |
| **Federal income tax** (§1(j)) · US · baseline | **1.3 s** | 0.87 s (68%) | engine + JSON I/O |
| Federal income tax · US · reform | 1.3 s | 0.89 s (70%) | engine + JSON I/O |
| **CO SNAP** · CO · baseline | **3.7 s** | 3.5 s (94%) | engine (rules complexity) |

Reform compile + YAML patch is **trivial** — under 100 ms total.

## Phase-by-phase breakdown

`scripts/profile_run.py` measures each phase. Sample run:

### Federal CTC · nationwide · baseline (3.4 s)

| # | Phase | Time | % | Notes |
|---|---|---|---|---|
| 1 | ECPS h5 load | 9 ms | 0.3% | h5py reads only the columns we need |
| 2 | Projection | 10 ms | 0.3% | numpy ops over 30k tax units |
| 5 | Build CompiledExecutionRequest dict | **256 ms** | 7.5% | Python loop allocating ~614k dicts |
| 6 | JSON encode (141 MB) | **422 ms** | 12.4% | stdlib `json.dumps` |
| 7 | Engine subprocess (`run-compiled`) | **2 365 ms** | 69.2% | Rust eval + JSON I/O over pipe |
| 8 | JSON decode (152 MB) | **323 ms** | 9.5% | stdlib `json.loads` |
| 9 | Decode + aggregate | 31 ms | 0.9% | numpy reductions |

### Federal income tax (§1(j)) · nationwide · baseline (1.3 s)

| # | Phase | Time | % |
|---|---|---|---|
| 1 | ECPS h5 load | 23 ms | 1.8% |
| 2 | Projection | 3 ms | 0.3% |
| 5 | Build request | 83 ms | 6.5% |
| 6 | JSON encode (53 MB) | 160 ms | 12.6% |
| 7 | Engine subprocess | **866 ms** | 68.5% |
| 8 | JSON decode (38 MB) | 111 ms | 8.8% |
| 9 | Aggregate | 19 ms | 1.5% |

### CO SNAP · CO (413 hh) · baseline (3.7 s)

| # | Phase | Time | % |
|---|---|---|---|
| 5 | Build request | 59 ms | 1.6% |
| 6 | JSON encode (41 MB) | 119 ms | 3.2% |
| 7 | Engine subprocess | **3 521 ms** | **94.5%** |
| 8 | JSON decode (18 MB) | 32 ms | 0.9% |

CO SNAP per-household engine cost = **8.5 ms** (3.5 s / 413 hh) vs CTC at
**0.08 ms/TU** (2.4 s / 30k). The CO SNAP RuleSpec has 168 derived rules
and ~200 input slots per entity vs CTC's 9 derived + 7 inputs — the
rules themselves are ~100× more complex per entity.

## What's actually slow

**Three things consume 90% of every call:**

1. **Engine subprocess** (68–94%). Spawning the Rust binary, piping a
   50–150 MB JSON request to its stdin, having it parse + evaluate, and
   reading a similar-size response. Process startup is fixed at ~50 ms;
   the rest is JSON I/O over a pipe + the actual evaluation.

2. **JSON encode** (3–13%). stdlib `json.dumps` on a list of 100k–600k
   small dicts.

3. **JSON decode** (1–10%). Same shape, response side.

The Python loop building the request dict is meaningful (1.6–8%) but
secondary. ECPS load, projection, aggregation are negligible (<2%
combined).

**Why is the request payload so big?**

The engine demands every input slot for every queried entity — there's
no "use compiled defaults" mode. So we duplicate data:

* CO SNAP requires ~200 slots × 413 hh + ~200 slots × 1057 persons =
  **~280k input records** for one CO request.
* CTC requires duplicating tax-unit-level inputs (filing_status, SSN
  flags) onto every Person too, because the engine can't tell those
  slots are TaxUnit-only. **614k records** for the CTC request — 2.5×
  what fed-tax (240k) needs for the same population.

## What the dense engine path can do

The engine has a `CompiledDenseProgram` Python binding that takes numpy
columns directly and returns numpy arrays — no JSON, no subprocess, no
slot-by-slot dict construction. It's the same Rust evaluator under the
hood.

We tested all three programs:

| Program | Dense compile | Verdict |
|---|---|---|
| **§1(j) federal income tax** | ✓ 4 ms | works |
| **§24(h) CTC** | ✗ | fails: "where-clause predicates cannot yet reference derived values (`ctc_qualifying_child_under_subsection_h`)" |
| **CO SNAP** | ✗ | fails: same restriction |

§1(j) head-to-head, nationwide (30k tax units, identical $1 850 B output):

| Path | Time | Speedup |
|---|---|---|
| JSON subprocess | 1 348 ms | 1× |
| Dense | 25 ms | **58×** |

Dense skips:
* JSON encode + decode (160 + 111 ms)
* Subprocess startup + pipe I/O (~50 + 800 ms)
* Per-record dict allocation (83 ms)

For programs the dense path can compile, this is a one-shot ~40–60×
win with no functional change.

## Optimization paths, in priority order

### A. Switch §1(j) to the dense path · 1 day · 50–60× speedup on that program

- Add a `_execute_dense()` branch in `run/microsim.py` that, when the
  program supports dense compile, uses `CompiledDenseProgram.execute()`.
- Translate `proj.inputs` (full RuleSpec ids) to bare slot names by
  splitting on `#input.`.
- `ParameterOverride` reform path needs a quick adaptation: rebuild
  the dense compile after YAML patching, ~5 ms recompile.
- **Federal income tax goes from 1.3 s → ~75 ms end-to-end (≈18×).**
- Doesn't help CTC or SNAP (engine limitation).

### B. orjson for the JSON path · 1 hour · ~3–5× faster encode/decode

- `orjson.dumps` is 4–8× faster than `json.dumps` on dict lists; same
  for `loads`.
- Saves ~280 ms on CTC (450 → 170), ~180 ms on fed-tax.
- Trivial drop-in (import + dumps/loads call sites, handle bytes vs str).

### C. Don't replicate TaxUnit inputs onto Person rows for CTC · 0.5 day · ~50% smaller payload for CTC

- We currently duplicate `filing_status_is_joint_return`, the SSN flags,
  etc. onto every Person. The engine demands them at every queried
  entity scope.
- Two routes:
  - **Patch-the-yaml**: declare those slots' `entity:` explicitly so the
    engine knows where they live. Probably needs an upstream
    rules-us-co change.
  - **Cache-the-default-block**: build the duplicated input chunks once,
    reuse across reforms. Ours change between sliders, so this only
    helps reforms.

### D. Cache the request-build artefacts · 1 hour · ~250 ms saved per reform

- A reform changes only the **artifact** (we recompile), not the input
  records. Today we rebuild the full dict + re-serialise on every reform
  click. A keyed cache of `(program, scope, year)` → request_json would
  serve every reform from the cached encoded bytes; only the artifact
  path differs.
- 250 ms (build) + 400 ms (encode) saved per CTC reform = ~650 ms.
- Frontend already caches baseline numbers; this caches the upstream
  request bytes.

### E. Long-lived engine process (skip subprocess startup per call) · 1 day · ~50–100 ms saved

- Engine binary spawn + symbol load is ~50 ms minimum.
- Either:
  - Spawn one persistent engine subprocess and write requests to it via
    a length-prefixed pipe protocol.
  - Or use the in-process `CompiledDenseProgram` (option A) which
    eliminates the subprocess entirely.
- (E) is moot if we land (A); they're the same fix surfaced differently.

### F. Skip default-fill for CO SNAP · upstream change · could save ~1–2 s

- CO SNAP sends ~280k input records to fill 200 slots × every entity,
  most at compiled defaults.
- If the engine grew a "use compiled defaults for unset inputs" mode,
  we'd send ~10k records (only ECPS-populated slots) → maybe 80% smaller
  payload → 1–2 s faster.
- This is an upstream `axiom-rules-engine` feature request, not
  something we control. Logged here as a wishlist.

### G. Pre-warm the engine binary · trivial · ~50 ms saved on cold path

- `subprocess.Popen` paid every call. Could keep one warm via a thread.
- Marginal. Probably not worth implementing once (E) ships.

### H. Streaming input encoding · 1 day · uncertain win

- For very large requests (CTC at 141 MB), build the JSON in chunks
  written directly to subprocess stdin instead of materialising the
  whole string in memory. Reduces peak memory; may not reduce wall
  clock since the engine has to read it anyway.

## Recommended sequencing

1. **Ship A (dense for §1(j))** first — biggest single win, minimal risk.
2. Then **B (orjson)** — quick blanket win for everything that still
   uses the JSON path.
3. Then **D (request caching)** — pure UX win on reforms, no engine
   changes.
4. Open an upstream issue on `axiom-rules-engine` for **F** (compiled
   defaults) and the dense compiler's "where-clause referencing derived
   values" support. Both unblock CTC and SNAP for dense / lean payloads.

Together (A + B + D) take CTC nationwide from **3.4 s → ~2.5 s**; fed
income tax from **1.3 s → ~75 ms (dense) ; ~600 ms (JSON+orjson if dense
is unavailable for any reason)**.

## Reproducing

```bash
git checkout perf-audit
.venv/bin/python scripts/profile_run.py --program all --reform --repeats 2
```

Phase definitions and the dense-vs-json head-to-head live in
`scripts/profile_run.py`.
