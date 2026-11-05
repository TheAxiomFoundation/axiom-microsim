"""The builders against a real engine and rules tree (issue #25).

Uses the binary and checkouts the app itself reads: ``$AXIOM_RULES_ENGINE_BINARY``,
``$AXIOM_RULES_US_DIR`` and, for a flat tree, ``$AXIOM_RULES_US_CO_DIR``. Self-skips
when they are absent, as in CI. Set them to a candidate engine and rulespec-us
before a pin move: each program's baseline and a reform must compile, and the
artifact must name every output id the runner requests.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from axiom_microsim.run import microsim as M
from axiom_microsim.run.microsim import ParameterOverride

pytestmark = pytest.mark.skipif(
    not (M.ENGINE_BIN.exists() and M.RULES_US_DIR.exists()),
    reason=(
        f"engine binary or rules missing ({M.ENGINE_BIN}, {M.RULES_US_DIR}); set "
        "AXIOM_RULES_ENGINE_BINARY and AXIOM_RULES_US_DIR"
    ),
)

# Builder, the program's output ids, and a reform the web app's levers send.
PROGRAMS = {
    "federal-ctc": (
        M._ctc_artifact_for,
        M.FED_CTC_OUTPUT_IDS.values(),
        ParameterOverride(
            "rules-us",
            "statutes/26/24/h.yaml",
            "ctc_child_amount_under_subsection_h",
            "set_formula",
            formula="3000",
        ),
    ),
    "co-snap": (
        M._artifact_for,
        M.DEFAULT_OUTPUT_IDS.values(),
        ParameterOverride(
            "rules-us",
            "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml",
            "snap_maximum_allotment_table",
            "scale_values",
            multiplier=1.1,
        ),
    ),
    "federal-income-tax": (
        M._fed_artifact_for,
        M.FED_INCOME_TAX_OUTPUT_IDS.values(),
        ParameterOverride(
            "rules-us",
            "policies/irs/rev-proc-2025-32/income-tax-brackets.yaml",
            "income_tax_bracket_rates",
            "scale_values",
            multiplier=1.1,
        ),
    ),
}


@pytest.fixture(autouse=True)
def _scratch_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(M, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(
        M, "CO_SNAP_BASELINE_ARTIFACT", tmp_path / "artifacts" / "co-snap.compiled.json"
    )


@pytest.mark.parametrize("reform", [False, True], ids=["baseline", "reform"])
@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_program_compiles_with_the_ids_the_runner_requests(program: str, reform: bool) -> None:
    build, output_ids, override = PROGRAMS[program]

    artifact, scratch = build([override] if reform else None)
    try:
        data = artifact.read_bytes()
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
            artifact.unlink(missing_ok=True)

    missing = [i for i in output_ids if f'"{i}'.encode() not in data]
    assert not missing, f"{program} artifact names none of {missing}"


def test_flat_baseline_matches_the_pre_25_command(tmp_path: Path) -> None:
    """Same bytes as ``compile --program <rulespec-us copy> --output``, run directly."""
    if M._rules_layout(M.RULES_US_DIR) != "flat":
        pytest.skip("the pre-#25 command compiles only the flat layout")
    staged = tmp_path / "direct" / "rulespec-us"
    shutil.copytree(M.RULES_US_DIR, staged)
    direct = tmp_path / "direct.json"
    subprocess.run(
        [
            str(M.ENGINE_BIN),
            "compile",
            "--program",
            str(staged / M.FED_CTC_PROGRAM_REL),
            "--output",
            str(direct),
        ],
        check=True,
        capture_output=True,
    )

    artifact, _ = M._ctc_artifact_for(None)

    assert artifact.read_bytes() == direct.read_bytes()
