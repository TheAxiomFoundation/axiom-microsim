#!/usr/bin/env bash
# Set up the local engine + rules trees so axiom-microsim can run baseline
# and reform compiles. Mirrors the SHAs pinned in modal_app.py.
#
# Run from repo root:
#   bash scripts/setup_engine.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/engine"

# Keep these three in step with modal_app.py — a local run that compiles a
# different rulespec than production is a silent wrong-number machine.
AXIOM_RULES_ENGINE_SHA="f2412104e45c49d5b90818da38211fac70419d52"
RULESPEC_US_SHA="557d15516986488a77b0f995f0940c66edd85154"
RULESPEC_US_CO_SHA="ba00673d73c19f262d542cfa597b0b365a1313b7"

CO_SNAP_REL="policies/cdhs/snap/fy-2026-benefit-calculation.yaml"
FED_INCOME_TAX_REL="statutes/26/1/j.yaml"
FED_CTC_REL="statutes/26/24.yaml"

mkdir -p "$ENGINE_DIR/artifacts"
cd "$ENGINE_DIR"

clone_at() {
  local repo="$1" sha="$2" dir="$3"
  # Old checkouts carry these paths as symlinks into a teammate's home
  # directory; they dangle everywhere else and would break `git clone`.
  if [[ -L "$dir" ]]; then
    echo "replacing symlink $dir -> $(readlink "$dir")"
    rm "$dir"
  fi
  if [[ -d "$dir/.git" ]]; then
    (cd "$dir" && git fetch --quiet origin && git checkout --quiet "$sha")
  else
    git clone --quiet "$repo" "$dir"
    (cd "$dir" && git checkout --quiet "$sha")
  fi
}

clone_at https://github.com/TheAxiomFoundation/axiom-rules-engine.git "$AXIOM_RULES_ENGINE_SHA" axiom-rules-engine
clone_at https://github.com/TheAxiomFoundation/rulespec-us.git         "$RULESPEC_US_SHA"          rules-us
clone_at https://github.com/TheAxiomFoundation/rulespec-us-co.git      "$RULESPEC_US_CO_SHA"       rules-us-co

# Build engine binary (idempotent — cargo skips if up-to-date).
(cd axiom-rules-engine && cargo build --release)

BIN="$ENGINE_DIR/axiom-rules-engine/target/release/axiom-rules-engine"

# Baseline artifact per program, same slugs modal_app.py compiles.
compile_program() {
  local slug="$1" program="$2"
  "$BIN" compile --program "$program" --output "$ENGINE_DIR/artifacts/$slug.compiled.json" >/dev/null
  echo "  $slug.compiled.json"
}

echo "Compiling baseline artifacts:"
compile_program co-snap            "$ENGINE_DIR/rules-us-co/$CO_SNAP_REL"
compile_program federal-income-tax "$ENGINE_DIR/rules-us/$FED_INCOME_TAX_REL"
compile_program federal-ctc        "$ENGINE_DIR/rules-us/$FED_CTC_REL"

echo
echo "Engine ready."
echo "  binary    : $BIN"
echo "  artifacts : $ENGINE_DIR/artifacts"
echo
echo "Next: build the dense PyO3 extension if you haven't:"
echo "  cd $ENGINE_DIR/axiom-rules-engine && \\"
echo "    maturin develop --release --manifest-path python-ext/Cargo.toml"
