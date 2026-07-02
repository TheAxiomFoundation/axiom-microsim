# Deploy

Two services under PolicyEngine accounts — same layout as
[axiom-co-snap](https://github.com/TheAxiomFoundation/co-snap-cliffs/blob/main/DEPLOY.md):

| Where | What | Why |
|---|---|---|
| **Modal** (`axiom-microsim`) | `axiom-rules-engine` binary + `rules-us` + `rules-us-co` + pre-compiled program artifacts + the dense PyO3 binding + `policyengine_us` (for `/compare`), behind a FastAPI app exposing `/microsim`, `/compare`, and `/ecps-stats`. Reads the pinned populace artifact from a Modal Volume. | Vercel can't run native binaries, hold the rule trees on disk, or import PolicyEngine. |
| **Vercel** (`axiom-microsim`) | Next.js app — three program panels (CTC / federal income tax / CO SNAP), reform sliders, decile + winners-losers charts, the `/api/*` routes that proxy to Modal. | Standard Next.js deploy target. |

Vercel reads the Modal URL from `AXIOM_MICROSIM_URL`. Locally, when that
env var points at a `uvicorn` process on `:8765`, `npm run dev` works
without Modal.

## 1. Deploy the engine to Modal

```bash
# One-time: install + auth into PolicyEngine's Modal workspace.
pip install modal
modal token set --token-id <id> --token-secret <secret>   # PolicyEngine workspace

# Deploy. First build is heavy (~10-15 min) because of the Rust build +
# PolicyEngine install. Subsequent deploys reuse the cached layers
# unless ENGINE_VERSION or any pinned SHA in modal_app.py changes.
modal deploy modal_app.py
```

Modal prints a public URL of the form:

```
https://policyengine--axiom-microsim-web.modal.run
```

Copy it. Verify:

```bash
curl https://policyengine--axiom-microsim-web.modal.run/health
# → {"status":"ok"}
```

## 2. Upload the pinned populace artifact to the Modal Volume

The `/microsim` endpoint reads the pinned **populace** artifact
(`populace_us_2024.h5`) mounted at `/data/populace`. Populate the volume
once with the pinned dense release (NOT HF `latest` — see
`axiom_microsim/data/populace_loader.py` for the #278 rationale). The
loader sha256-verifies the file against the pin on first read, so it must
be the exact pinned revision:

```bash
REV=populace-us-2024-f0af251-703bd81a565c-20260620T201958Z

# Download the pinned artifact (dataset repo, immutable revision).
huggingface-cli download policyengine/populace-us populace_us_2024.h5 \
  --repo-type dataset --revision "$REV" --local-dir /tmp/pop

# Push to the Modal Volume the image is wired to read.
modal volume put axiom-microsim-populace /tmp/pop/populace_us_2024.h5
```

> Migration note: the old `axiom-microsim-ecps` volume (Enhanced CPS from
> the deprecated `policyengine-us-data`) is retired — do not repopulate it.
> The image sets `AXIOM_POPULACE_US_H5` to the mounted populace file.

Smoke test the full path:

```bash
curl -sS -X POST https://policyengine--axiom-microsim-web.modal.run/microsim \
  -H "Content-Type: application/json" \
  -d '{"program":"federal-ctc","state":"US","year":2026,"overrides":[]}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"OK: \${d['baseline']['annual_cost']/1e9:.1f}B\")"
```

To re-deploy after a `rules-us` change, bump `ENGINE_VERSION` in
`modal_app.py` and run `modal deploy` again.

## 3. Deploy the frontend to Vercel

```bash
cd web

# One-time: link to a Vercel project under the PolicyEngine team.
npm i -g vercel
vercel login
vercel link --scope policyengine
```

Set the env var Vercel needs (paste the Modal URL from step 1, all envs):

```bash
vercel env add AXIOM_MICROSIM_URL
```

Deploy:

```bash
vercel --prod
```

After Vercel deploys, the Next.js `/api/*` routes pick up
`AXIOM_MICROSIM_URL` and proxy every request to Modal. The `/compare`
endpoint takes ~10-100 s in production (PolicyEngine warm-up) — the
Vercel function is configured with a 300s timeout to allow this.

## What lives where

* **`modal_app.py`** — Modal image definition + asgi_app entrypoint. Pinned
  SHAs for the engine and rulespec repos.
* **`axiom_microsim/`** — Python package: server, projections, runner,
  aggregators. Same code runs locally under uvicorn.
* **`scripts/compute_pe_one.py`** — one-shot PE oracle. Subprocessed by
  `/compare`. Has to live in the same image as the FastAPI app.
* **`web/`** — Next.js app + API proxy routes.
* **`web/vercel.json`** — function timeouts (compare gets 300s).
