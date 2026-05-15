"""Modal deployment for axiom-microsim.

Hosts the FastAPI app from ``axiom_microsim.server``, which carries
three programs (CO SNAP, federal income tax §1(j), federal CTC §24(h))
plus a live PolicyEngine comparison endpoint. Image bundles:

  - ``axiom-rules-engine`` Rust binary
  - ``rules-us`` + ``rules-us-co`` rulespec trees (pinned commits)
  - Pre-compiled artifacts for all three programs
  - The dense PyO3 binding (built with maturin)
  - ``policyengine_us`` + ``policyengine_core`` for the /compare endpoint

Same wire shape as ``uvicorn axiom_microsim.server:app`` locally — the
Vercel app reads ``AXIOM_MICROSIM_URL`` and POSTs there.

Deploy::

    modal deploy modal_app.py

First deploy is heavy (~10-15 min) because of the Rust build + PE
install. Subsequent deploys reuse cached layers unless ``ENGINE_VERSION``
or any pinned SHA changes.
"""

from __future__ import annotations

import modal


app = modal.App("axiom-microsim")

# Bump when any pinned SHA below changes so the layer rebuilds.
ENGINE_VERSION = "v0.2.1-3-programs"

# Pinned SHAs.
# rules-us at current main — has §1(j), §24(h), §32, §63 etc. that we need.
# rules-us-co pinned back to the SHA axiom-co-snap uses; current main has
# a YAML formula with `\` syntax the engine doesn't yet parse.
AXIOM_RULES_ENGINE_SHA = "f2412104e45c49d5b90818da38211fac70419d52"
RULESPEC_US_SHA = "d9a03f172d5d2753ec3557b4e56f778f7f72b819"
RULESPEC_US_CO_SHA = "ba00673d73c19f262d542cfa597b0b365a1313b7"

PROGRAMS_TO_COMPILE: dict[str, tuple[str, str]] = {
    # slug → (in-image dir, program path within repo)
    # NOTE: dir names use the `rulespec-` prefix the engine expects per
    # commit b95c73f ("Rename RuleSpec engine repo bindings"). Even
    # though the GitHub repos are named `rules-us` / `rules-us-co`, the
    # engine's import resolver looks for `rulespec-{prefix}` siblings.
    "co-snap": ("rulespec-us-co", "policies/cdhs/snap/fy-2026-benefit-calculation.yaml"),
    "federal-income-tax": ("rulespec-us", "statutes/26/1/j.yaml"),
    "federal-ctc": ("rulespec-us", "statutes/26/24/h.yaml"),
}

# ECPS .h5 lives on a Modal Volume so cold starts don't pay the download.
# Populate once with `modal volume put axiom-microsim-ecps enhanced_cps_2024.h5`.
ECPS_VOLUME = modal.Volume.from_name("axiom-microsim-ecps", create_if_missing=True)
ECPS_MOUNT = "/data/ecps"


_compile_cmds = [
    f"/opt/axiom-rules-engine/target/release/axiom-rules-engine compile "
    f"--program /opt/{repo}/{path} "
    f"--output /opt/artifacts/{slug}.compiled.json"
    for slug, (repo, path) in PROGRAMS_TO_COMPILE.items()
]


image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install(
        "git", "curl", "build-essential", "pkg-config", "libssl-dev", "ca-certificates",
    )
    .run_commands(
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs "
        "| sh -s -- -y --default-toolchain stable --profile minimal",
    )
    .run_commands(
        f"echo 'engine: {ENGINE_VERSION}'",
        # Engine + rulespec checkouts at pinned SHAs. Clone as
        # `rulespec-us` / `rulespec-us-co` so the engine's import
        # resolver finds them via ancestor traversal.
        "git clone https://github.com/TheAxiomFoundation/axiom-rules-engine.git /opt/axiom-rules-engine",
        f"cd /opt/axiom-rules-engine && git checkout {AXIOM_RULES_ENGINE_SHA}",
        "git clone https://github.com/TheAxiomFoundation/rules-us.git /opt/rulespec-us",
        f"cd /opt/rulespec-us && git checkout {RULESPEC_US_SHA}",
        "git clone https://github.com/TheAxiomFoundation/rules-us-co.git /opt/rulespec-us-co",
        f"cd /opt/rulespec-us-co && git checkout {RULESPEC_US_CO_SHA}",
        # Build the Rust CLI binary.
        ". $HOME/.cargo/env && cd /opt/axiom-rules-engine && cargo build --release",
        # Compile every program's baseline artifact.
        "mkdir -p /opt/artifacts",
        *_compile_cmds,
    )
    .pip_install(
        "fastapi>=0.110",
        "uvicorn>=0.27",
        "pydantic>=2.6",
        "h5py>=3.10",
        "numpy>=1.26",
        "pandas>=2.1",
        "ruamel.yaml>=0.18",
        "maturin>=1.7",
    )
    .run_commands(
        # Install the axiom-rules-engine Python package + dense PyO3 ext.
        ". $HOME/.cargo/env && "
        "pip install /opt/axiom-rules-engine/python && "
        "maturin build --release --manifest-path /opt/axiom-rules-engine/python-ext/Cargo.toml --out /tmp/wheels && "
        "pip install /tmp/wheels/*.whl",
    )
    .pip_install(
        # PolicyEngine — used by /compare for live oracle comparison.
        "policyengine_us>=1.0",
        "policyengine_core>=3.0",
    )
    .add_local_dir(
        ".", "/opt/axiom-microsim",
        ignore=["**/node_modules", "**/.next", "web/**", ".venv/**", "engine/**"],
        # copy=True so we can run `pip install /opt/axiom-microsim` after.
        # Without it Modal mounts the dir at container startup, but build
        # steps can't see it.
        copy=True,
    )
    .run_commands("pip install /opt/axiom-microsim")
    .env({
        "AXIOM_ARTIFACTS_DIR": "/opt/artifacts",
        "AXIOM_RULES_US_DIR": "/opt/rulespec-us",
        "AXIOM_RULES_US_CO_DIR": "/opt/rulespec-us-co",
        "AXIOM_RULES_ENGINE_BINARY": "/opt/axiom-rules-engine/target/release/axiom-rules-engine",
        "AXIOM_ECPS_PATH": f"{ECPS_MOUNT}/enhanced_cps_2024.h5",
        # /compare subprocesses into a Python with policyengine_us. In
        # Modal that's the SAME interpreter the FastAPI app runs in.
        "AXIOM_PE_PYTHON": "/usr/local/bin/python",
    })
)


@app.function(
    image=image,
    volumes={ECPS_MOUNT: ECPS_VOLUME},
    timeout=600,
    memory=8192,
    # PE microsim warmup is heavy; keep one container hot to avoid
    # paying the cold start on every reform run.
    min_containers=1,
)
@modal.asgi_app()
def web():
    from axiom_microsim.server import app as fastapi_app
    return fastapi_app
