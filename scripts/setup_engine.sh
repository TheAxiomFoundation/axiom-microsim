#!/usr/bin/env bash
# Set up the local engine + rules trees so axiom-microsim can run baseline
# and reform compiles. Mirrors the SHAs pinned in modal_app.py.
#
# Run from repo root:
#   bash scripts/setup_engine.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/engine"

AXIOM_RULES_ENGINE_SHA="9106f44e34ec3eae92a1adf2246560c5eac00094"
RULESPEC_US_SHA="2f3a30991e1f8279c2fa664e51f068a63d905591"
RULESPEC_US_CO_SHA="ba00673d73c19f262d542cfa597b0b365a1313b7"

CO_SNAP_REL="policies/cdhs/snap/fy-2026-benefit-calculation.yaml"

mkdir -p "$ENGINE_DIR/artifacts"
cd "$ENGINE_DIR"

clone_at() {
  local repo="$1" sha="$2" dir="$3"
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
"$BIN" compile \
  --program "$ENGINE_DIR/rules-us-co/$CO_SNAP_REL" \
  --output "$ENGINE_DIR/artifacts/co-snap.compiled.json"

echo
echo "Engine ready."
echo "  binary    : $BIN"
echo "  artifact  : $ENGINE_DIR/artifacts/co-snap.compiled.json"
echo
echo "Next: build the dense PyO3 extension if you haven't:"
echo "  cd $ENGINE_DIR/axiom-rules-engine && \\"
echo "    maturin develop --release --manifest-path python-ext/Cargo.toml"
