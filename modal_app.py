"""Modal deployment for axiom-microsim.

Hosts the FastAPI app from ``axiom_microsim.server`` plus the
``axiom-rules-engine`` Rust binary, the Python dense binding (built with
maturin), the ``rules-us`` and ``rules-us-co`` rulespec trees, and a
prebuilt ``co-snap.compiled.json`` artifact.

Same wire shape as the local ``uvicorn axiom_microsim.server:app`` — the
Vercel app reads ``AXIOM_MICROSIM_URL`` and POSTs there regardless of
which side it's pointed at.

Deploy::

    modal deploy modal_app.py
"""

from __future__ import annotations

import modal


app = modal.App("axiom-microsim")

# Bump when any pinned SHA below changes so the layer rebuilds.
ENGINE_VERSION = "v0.1.0-co-snap"

# Pinned SHAs. Match axiom-co-snap so a reform expressed in either app
# evaluates to identical RuleSpec values.
AXIOM_RULES_ENGINE_SHA = "9106f44e34ec3eae92a1adf2246560c5eac00094"
RULESPEC_US_SHA = "2f3a30991e1f8279c2fa664e51f068a63d905591"
RULESPEC_US_CO_SHA = "ba00673d73c19f262d542cfa597b0b365a1313b7"

CO_SNAP_PROGRAM_REL = "policies/cdhs/snap/fy-2026-benefit-calculation.yaml"

# ECPS file is large (~50 MB compressed) — bake it in via a Modal Volume
# rather than the image so cold starts don't pay the download.
ECPS_VOLUME = modal.Volume.from_name("axiom-microsim-ecps", create_if_missing=True)
ECPS_MOUNT = "/data/ecps"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl", "build-essential", "pkg-config", "libssl-dev", "ca-certificates")
    .run_commands(
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs "
        "| sh -s -- -y --default-toolchain stable --profile minimal",
    )
    .run_commands(
        f"echo 'engine: {ENGINE_VERSION}'",
        # Engine
        "git clone https://github.com/TheAxiomFoundation/axiom-rules-engine.git /opt/axiom-rules-engine",
        f"cd /opt/axiom-rules-engine && git checkout {AXIOM_RULES_ENGINE_SHA}",
        # Rules
        "git clone https://github.com/TheAxiomFoundation/rulespec-us.git /opt/rules-us",
        f"cd /opt/rules-us && git checkout {RULESPEC_US_SHA}",
        "git clone https://github.com/TheAxiomFoundation/rulespec-us-co.git /opt/rules-us-co",
        f"cd /opt/rules-us-co && git checkout {RULESPEC_US_CO_SHA}",
        # Build CLI binary + compile baseline artifact
        ". $HOME/.cargo/env && cd /opt/axiom-rules-engine && cargo build --release",
        "mkdir -p /opt/artifacts",
        f"/opt/axiom-rules-engine/target/release/axiom-rules-engine compile "
        f"--program /opt/rules-us-co/{CO_SNAP_PROGRAM_REL} "
        f"--output /opt/artifacts/co-snap.compiled.json",
    )
    .pip_install(
        "fastapi>=0.110",
        "uvicorn>=0.27",
        "pydantic>=2.6",
        "h5py>=3.10",
        "numpy>=1.26",
        "ruamel.yaml>=0.18",
        "maturin>=1.7",
    )
    .run_commands(
        # Build the dense PyO3 extension into the image's site-packages.
        ". $HOME/.cargo/env && cd /opt/axiom-rules-engine && "
        "pip install /opt/axiom-rules-engine/python && "
        "maturin build --release --manifest-path python-ext/Cargo.toml --out /tmp/wheels && "
        "pip install /tmp/wheels/*.whl",
    )
    .add_local_dir(".", "/opt/axiom-microsim", ignore=["**/node_modules", "**/.next", "web/**"])
    .run_commands("pip install /opt/axiom-microsim")
    .env({
        "AXIOM_ARTIFACTS_DIR": "/opt/artifacts",
        "AXIOM_RULES_US_DIR": "/opt/rules-us",
        "AXIOM_RULES_US_CO_DIR": "/opt/rules-us-co",
        "AXIOM_RULES_ENGINE_BINARY": "/opt/axiom-rules-engine/target/release/axiom-rules-engine",
        "AXIOM_ECPS_PATH": f"{ECPS_MOUNT}/enhanced_cps_2024.h5",
    })
)


@app.function(
    image=image,
    volumes={ECPS_MOUNT: ECPS_VOLUME},
    timeout=300,
    memory=4096,
)
@modal.asgi_app()
def web():
    from axiom_microsim.server import app as fastapi_app
    return fastapi_app
