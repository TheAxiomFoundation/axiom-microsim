# PROGRESS — PR #19 gate-finding fixes (`fix/pe-compare-timeouts`)

Peer merge-review returned `changes_requested` with four findings. This file
tracks the fix pass. Scope is limited to those findings; no unrelated refactors.

## State

Working from PR #19 head `77b4928`. Branch `fix/pe-compare-timeouts`.

## Findings

| # | Finding | Status |
|---|---------|--------|
| 1 | SECURITY — unvalidated `state` reaches a filesystem path + `pickle.load` | **done** (cbdadce) |
| 2 | No tests for the new caching/timeout behaviour; module caches leak between tests | **done** |
| 3 | undici `headersTimeout` (300s default) can cut the 790s `/api/compare` window | not started |
| 4 | `_COMPARE_CACHE` / `_BASELINE_RESULT_CACHE` unbounded; cache hits replay stale `elapsed_seconds` | **done** (cbdadce) |

## Done

- **Finding 1** — `StateScope` allowlist (`US` + the 50 states + DC, reusing the
  loader's `STATE_FIPS`) on `MicrosimRequest`, `CompareRequest` and the
  `/ecps-stats` query param; invalid scopes are a 422 before the PE subprocess
  runs. `scripts/compute_pe_one.py` additionally refuses off-shape
  `state`/`program` in `_cache_path` so it is safe standalone.
- **Finding 4** — both module caches are bounded LRUs (`OrderedDict`, 64
  entries) via `_lru_get` / `_lru_put`; a compare cache hit returns a copy whose
  `elapsed_seconds` is this request's wait, not the original cold run's.
- **Finding 2** — `tests/conftest.py` autouse fixture resets `_COMPARE_CACHE`,
  `_COMPARE_INFLIGHT` and `_BASELINE_RESULT_CACHE` around every test (disabling
  it turns 6 tests red, so it is load-bearing). Three new test modules cover
  result-cache hits, single-flight dedup, `TimeoutExpired` → 504, LRU bounds,
  `_cached_baseline` reuse, the `compute_pe_one` pickle cache (hit / corrupt /
  truncated / empty / atomic replace) and state-scope validation on both sides.
  Each behaviour was mutation-checked: reverting the fix turns the test red.

## Next

1. Finding 3 — explicit undici dispatcher in `web/src/app/api/compare/route.ts`.
2. Verify: `uv run pytest -q`, `ruff check` / `ruff format --check`, `bun run build` in `web/`.

## Verification so far

- `uv run pytest -q` → 125 passed, 5 skipped.
- `uv run ruff check axiom_microsim tests` + `ruff format --check` → clean.
