# axiom-microsim

PE-free microsimulation over Enhanced CPS using `axiom-rules-engine`.

Companion to [`axiom-co-snap`](https://github.com/TheAxiomFoundation/co-snap-cliffs)
(single-household sweeps) and [`axiom-programs`](https://github.com/TheAxiomFoundation/axiom-programs)
(oracle comparison). Where co-snap shows what one household receives across an
earnings sweep, **axiom-microsim runs the same Axiom rules against an entire
ECPS state population and returns weighted aggregates** — cost, distributional
impact, reform vs baseline winners/losers.

The runtime path imports zero PolicyEngine code. The Enhanced CPS `.h5` file
is read with `h5py` directly; rule evaluation goes through the
`CompiledDenseProgram` columnar batch interface in `axiom-rules-engine`.

## v1 scope

- **Program**: Colorado SNAP only (matches co-snap-cliffs).
- **Outputs**: aggregate cost, weighted decile distribution, baseline-vs-reform
  winners/losers shares.
- **Reform mechanism**: parameter overrides via in-memory RuleSpec YAML patch
  (lifted from co-snap-cliffs).
- **Not in v1**: SPM / poverty impact (deferred — see `DECISIONS.md`),
  programs other than CO SNAP, federal programs.

## Architecture

```text
ECPS .h5 (h5py)
    │
    ▼
EcpsBatch (numpy columns + person→household offsets)
    │
    ▼
project/co_snap.py  ─────►  DenseCompiledProgram inputs
                                    │
                                    ▼  (axiom-rules-engine, Rust)
                            Output numpy arrays (snap_allotment, ...)
                                    │
                                    ▼
                           aggregate/{cost,distribution,reform}
                                    │
                                    ▼
                            JSON response
```

Two transports for the same handler:
- **Local**: `uv run uvicorn axiom_microsim.server:app --reload`
- **Modal**: `modal deploy modal_app.py` — same FastAPI app wrapped in `@modal.asgi_app()`

The Next.js app in `web/` reads `AXIOM_MICROSIM_URL` and POSTs to either.

## Local quickstart

### 0. Prereqs (one-time)

* **Python 3.11+** — the dense PyO3 extension targets ≥3.11. macOS:
  `brew install python@3.13`.
* **Rust + maturin** — to build the dense extension. `curl https://sh.rustup.rs | sh -s -- -y`
  then `pip install maturin`.
* **Population data** — none needed up front. The loader downloads the
  pinned **populace** artifact (`populace_us_2024.h5`) from Hugging Face on
  first use and sha256-verifies it. To avoid the download, point
  `AXIOM_POPULACE_US_H5` at a local copy of the pinned file (still
  verified). See "Population data" below for the pin and env vars.

Build the dense extension once in your `axiom-rules-engine` checkout:

```bash
cd ~/axiom-rules-engine
maturin develop --release --manifest-path python-ext/Cargo.toml
```

### 1. Python lib + server

```bash
cd ~/axiom-microsim
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e .[dev]
uv pip install --python .venv/bin/python -e ~/axiom-rules-engine/python

# Set up engine binary + rulespec checkouts + compile CO SNAP artifact
bash scripts/setup_engine.sh

# Verify the data path (no engine call yet — runs in <1s)
.venv/bin/pytest tests/ -v

# Run a baseline microsim from the CLI
.venv/bin/axiom-microsim run --program co-snap --state CO --year 2026

# Or start the local server (same handler Modal will host)
.venv/bin/uvicorn axiom_microsim.server:app --reload --port 8000
```

### 2. Frontend

```bash
cd web
cp .env.example .env.local        # set AXIOM_MICROSIM_URL=http://localhost:8000
npm install
npm run dev                       # http://localhost:3000
```

Adjust the SNAP max-allotment slider; the UI calls `/api/microsim`, which
proxies to `AXIOM_MICROSIM_URL/microsim`.

## Population data

The microsim reads its population from PolicyEngine's **populace** project
(migrated 2026-07-02 off the deprecated `policyengine-us-data` Enhanced CPS
path; plan A2). The source is **pinned** — a specific, hash-verified
release, resolved by `axiom_microsim/data/populace_loader.py`:

| Field | Value |
|---|---|
| Repo | `policyengine/populace-us` (Hugging Face **dataset**) |
| File | `populace_us_2024.h5` |
| Revision | `populace-us-2024-f0af251-703bd81a565c-20260620T201958Z` |
| sha256 | `16be6338f9d0b3c339883dae59949e995663b64cf145de6728b3dd0f916c5d5f` |

**Resolution order** (both loaders — `load_state`, `load_state_tax_units`):

1. explicit `path=` argument (either layout; auto-sniffed);
2. `$AXIOM_POPULACE_US_H5` — a local copy of the pinned file (still
   sha256-verified; skips the download, not the check);
3. the pinned `hf_hub_download` of the revision above, then verified;
4. `$AXIOM_ECPS_PATH` — **legacy** Enhanced CPS escape hatch (emits a
   `DeprecationWarning`; only used if it is set).

There is **no unpinned fallback**: a hash mismatch raises
`PopulaceVerificationError` ("refusing to run on unverified population
data") rather than silently running on the wrong data.

### Why pinned *dense*, not Hugging Face `latest`

`latest.json` and PolicyEngine bundle 4.18.8 currently point at the
*sparse* refit artifact
(`populace-us-2024-sparse-l0-refit-57k-…-national-only-20260701`), which
**zeroes untargeted input bases** — IRA/HSA/self-employed pension/childcare
and other engine inputs come back all-zero
([PolicyEngine/populace#278](https://github.com/PolicyEngine/populace/issues/278),
closed by pipeline-fix PR #279; a rebuilt sparse artifact is **not** yet
published/certified). We pin the **dense** `f0af251` release — the last
certified dense US population dataset — whose #278-class columns carry real
mass. When a post-#279 sparse (or newer dense) release is certified with
those bases confirmed non-zero, bump the pin in `populace_loader.py`.

### populace layout note

populace files are pandas `HDFStore` / PyTables tables (one compound-dtype
`table` dataset per entity: `person/table`, `household/table`, …), unlike
the legacy Enhanced CPS flat `variable/year` groups. The loader hides this
behind a column-reader adapter, so the same state / tax-unit filtering code
serves both. Two PolicyEngine-*derived* names the projections use
(`rent`, `taxable_unemployment_compensation`) are absent from populace's
input layer and map to their raw inputs (`pre_subsidy_rent`,
`unemployment_compensation`).

## Layout

```
axiom_microsim/
  data/populace_loader.py    # pinned populace: resolve + sha256-verify + read
  data/ecps_loader.py        # source resolution, state filter, EcpsBatch/TaxUnitBatch
  project/co_snap.py         # population columns → CO SNAP dense inputs
  run/microsim.py            # CompiledDenseProgram wrapper + reform overrides
  aggregate/cost.py          # Σ benefit × weight
  aggregate/distribution.py  # weighted decile groupby
  aggregate/reform.py        # baseline vs reform delta, winners/losers
  cli.py                     # `axiom-microsim run …`
  server.py                  # FastAPI POST /microsim
modal_app.py                 # Modal wrapper around server.py
engine/artifacts/            # compiled program JSONs (gitignored)
web/                         # Next.js frontend
tests/
DECISIONS.md
```

See `DECISIONS.md` for architectural rationale.
