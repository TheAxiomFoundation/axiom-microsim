# PROGRESS — PR #19 gate-finding fixes (`fix/pe-compare-timeouts`)

Peer merge-review returned `changes_requested` with four findings. This file
tracks the fix pass. Scope is limited to those findings; no unrelated refactors.

## State

Working from PR #19 head `77b4928`. Branch `fix/pe-compare-timeouts`.

## Findings

| # | Finding | Status |
|---|---------|--------|
| 1 | SECURITY — unvalidated `state` reaches a filesystem path + `pickle.load` | not started |
| 2 | No tests for the new caching/timeout behaviour; module caches leak between tests | not started |
| 3 | undici `headersTimeout` (300s default) can cut the 790s `/api/compare` window | not started |
| 4 | `_COMPARE_CACHE` / `_BASELINE_RESULT_CACHE` unbounded; cache hits replay stale `elapsed_seconds` | not started |

## Done

(nothing yet)

## Next

1. Finding 1 — validate `state` at the API boundary + defensively in `scripts/compute_pe_one.py`.
2. Finding 4 — bound both caches with a small LRU; recompute `elapsed_seconds` on a hit.
3. Finding 2 — `tests/conftest.py` autouse cache reset + direct tests.
4. Finding 3 — explicit undici dispatcher in `web/src/app/api/compare/route.ts`.
5. Verify: `uv run pytest -q`, `ruff check` / `ruff format --check`, `bun run build` in `web/`.
