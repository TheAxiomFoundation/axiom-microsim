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
| 3 | undici `headersTimeout` (300s default) can cut the 790s `/api/compare` window | **done** |
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
- **Finding 3** — `/api/compare` now calls undici's own `fetch` with an explicit
  `Agent({ headersTimeout, bodyTimeout })` covering the full 790s window, and
  pins `runtime = "nodejs"`. Measured on Node 25 before writing it: a foreign
  dispatcher passed to the *global* fetch throws `UND_ERR_INVALID_ARG` when the
  bundled and installed undici versions disagree, which is why the fetch and the
  Agent have to come from the same instance; `headersTimeout` applies per
  redirect hop; redirect handling, POST-to-GET on 303 and `AbortSignal` are
  unchanged from the global-fetch behaviour.

## Next

Nothing outstanding — push and report.

## Verification

- `uv run pytest -q` → 125 passed, 5 skipped.
- `uv run ruff check axiom_microsim tests` + `ruff format --check` → clean.
- `cd web && bun run build` + `bun run typecheck` → clean.
- `/api/compare` exercised end-to-end against a stub upstream through the
  production build (`bun run start`): a 200 body and a 422 upstream status both
  proxied through, with the upstream stalling before it emitted headers.

## Notes

- `undici@^7.29.0` was added to `web/`. The repo's committed lockfile is
  `package-lock.json`, which is what Vercel builds from, so that file carries
  the new entry; no `bun.lock` is committed, to avoid silently switching the
  deploy's package manager. The integrity hash was verified by computing sha512
  over the registry tarball, and the patched lockfile was round-tripped through
  `bun install` as a real consumer. Node's default package manager is blocked by
  a hook in this environment, so its own `ci` install could not be run directly.
- There is no JS test runner in this repo, so Finding 3 is covered by the
  manual end-to-end run above rather than by an automated test.
- Next bundles `undici` straight into `.next/server/app/api/compare/route.js`
  (the compiled route contains `UND_ERR_HEADERS_TIMEOUT`), so it needs no
  output-file-tracing entry and no `serverExternalPackages` change.
