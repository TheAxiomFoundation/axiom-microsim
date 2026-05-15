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
* **Enhanced CPS** `enhanced_cps_2024.h5` — download from
  [huggingface.co/policyengine/policyengine-us-data](https://huggingface.co/policyengine/policyengine-us-data)
  to `~/Downloads/` (default) or set `AXIOM_ECPS_PATH`.

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

## Layout

```
axiom_microsim/
  data/ecps_loader.py        # h5py reader, state filter, EcpsBatch
  project/co_snap.py         # ECPS columns → CO SNAP dense inputs
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
