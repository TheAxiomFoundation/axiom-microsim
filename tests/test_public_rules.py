"""The public scales read rule-owned baselines and the catalogue fits the rules."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st
from ruamel.yaml import YAML

from axiom_microsim.public_contract import ALLOWED_TARGETS, OverrideIn
from axiom_microsim.run import microsim as runtime


def _patch_version(directory: Path, version: dict, override: runtime.ParameterOverride) -> dict:
    """Run the real patcher against a fresh, rule-owned numeric baseline."""
    path = directory / "parameter.yaml"
    yaml = YAML()
    with path.open("w") as stream:
        yaml.dump({"rules": [{"name": override.parameter, "versions": [version]}]}, stream)
    runtime._patch_yaml(path, override)
    with path.open() as stream:
        return yaml.load(stream)["rules"][0]["versions"][0]


def _runtime_scale(kind: str, multiplier: float | None) -> runtime.ParameterOverride:
    return runtime.ParameterOverride(
        repo="rules-us",
        file_relative="parameter.yaml",
        parameter="parameter",
        patch_kind=kind,
        multiplier=multiplier,
    )


@pytest.mark.parametrize("kind", ["scale_values", "scale_formula"])
def test_patch_yaml_zero_multiplier_removes_the_rule_baseline(tmp_path: Path, kind: str) -> None:
    version = {"values": {1: 209, 2: 299}} if kind == "scale_values" else {"formula": "218"}
    patched = _patch_version(tmp_path, version, _runtime_scale(kind, 0))
    if kind == "scale_values":
        assert patched["values"] == {1: 0, 2: 0}
    else:
        assert float(patched["formula"]) == 0


@pytest.mark.parametrize("kind", ["scale_values", "scale_formula"])
def test_patch_yaml_missing_multiplier_preserves_the_rule_baseline(
    tmp_path: Path, kind: str
) -> None:
    version = {"values": {1: 209}} if kind == "scale_values" else {"formula": "218"}
    patched = _patch_version(tmp_path, version, _runtime_scale(kind, None))
    if kind == "scale_values":
        assert patched["values"] == {1: 209}
    else:
        assert float(patched["formula"]) == 218


# Use source amounts in hundreds so the expected products are exact integers,
# independently of the patcher's existing money/rate rounding policy.
@st.composite
def _public_table_scales(draw):
    target, policy = draw(
        st.sampled_from(
            [
                (target, policy)
                for target, policy in ALLOWED_TARGETS.items()
                if "scale_values" in policy.kinds
            ]
        )
    )
    hundredths = draw(st.integers(int(policy.minimum * 100), int(policy.maximum * 100)))
    return OverrideIn(
        repo=target[0],
        file_relative=target[1],
        parameter=target[2],
        patch_kind="scale_values",
        multiplier=hundredths / 100,
    ), hundredths


@settings(deadline=None)
@given(
    rows=st.lists(st.integers(1, 10_000), min_size=1, max_size=8),
    scale=_public_table_scales(),
)
@example(
    rows=[209, 299],
    scale=(
        OverrideIn(
            repo="rules-us",
            file_relative="policies/usda/snap/fy-2026-cola/deductions.yaml",
            parameter="snap_standard_deduction_48_states_dc_table",
            patch_kind="scale_values",
            multiplier=0,
        ),
        0,
    ),
)
def test_scale_values_multiplies_each_rule_owned_row(rows: list[int], scale) -> None:
    public, hundredths = scale
    values = {key: value * 100 for key, value in enumerate(rows, 1)}
    with tempfile.TemporaryDirectory() as directory:
        patched = _patch_version(Path(directory), {"values": values}, public.to_runtime())
    assert patched["values"] == {key: value * hundredths for key, value in enumerate(rows, 1)}


@settings(deadline=None)
@given(baseline=st.integers(1, 1_000_000), hundredths=st.integers(0, 200))
@example(baseline=218, hundredths=0)
def test_scale_formula_multiplies_the_rule_owned_numeric_literal(
    baseline: int, hundredths: int
) -> None:
    multiplier = hundredths / 100
    with tempfile.TemporaryDirectory() as directory:
        patched = _patch_version(
            Path(directory),
            {"formula": str(baseline)},
            _runtime_scale("scale_formula", multiplier),
        )
    assert float(patched["formula"]) == round(baseline * multiplier, 2)


@pytest.mark.parametrize(
    ("repo", "relative", "parameter", "policy"),
    [(*target, policy) for target, policy in ALLOWED_TARGETS.items()],
    ids=[target[2] for target in ALLOWED_TARGETS],
)
def test_catalogued_target_exists_and_accepts_its_kinds(
    repo: str, relative: str, parameter: str, policy
) -> None:
    # Follow the engine tests: inspect the runtime's configured location and
    # self-skip only when that tree is absent, never when a target is missing.
    root = {"rules-us": runtime.RULES_US_DIR, "rules-us-co": runtime.RULES_US_CO_DIR}[repo]
    if not root.exists():
        pytest.skip(f"rules tree not found at {root}; set AXIOM_RULES_US_DIR")
    path = root / relative
    assert path.is_file(), f"catalogued rule file does not exist: {path}"
    with path.open() as stream:
        document = YAML(typ="safe").load(stream)
    rules = {rule["name"]: rule for rule in document["rules"]}
    assert parameter in rules, f"catalogued parameter {parameter!r} not in {path}"
    versions = rules[parameter].get("versions", [])
    assert versions, f"catalogued parameter {parameter!r} has no versions in {path}"
    version = versions[0]
    for kind in policy.kinds:
        if kind == "scale_values":
            assert isinstance(version.get("values"), dict) and version["values"], (
                f"{path}:{parameter} needs versions[0].values for {kind}"
            )
        elif kind in {"scale_formula", "set_scalar"}:
            assert math.isfinite(float(str(version["formula"]))), (
                f"{path}:{parameter} needs a numeric versions[0].formula for {kind}"
            )
        else:
            pytest.fail(f"add a rules-shape check for new public kind {kind!r}")


@settings(deadline=None)
@given(hundredths=st.integers(50, 200), baseline=st.integers(1, 1_000_000))
def test_public_formula_scale_uses_the_current_rule_literal(hundredths: int, baseline: int) -> None:
    """A rules pin may change the source literal without a new Python baseline."""
    override = OverrideIn(
        repo="rules-us",
        file_relative="policies/usda/snap/fy-2026-cola/maximum-allotments.yaml",
        parameter="snap_maximum_allotment_additional_member",
        patch_kind="scale_formula",
        multiplier=hundredths / 100,
    ).to_runtime()
    with tempfile.TemporaryDirectory() as directory:
        patched = _patch_version(Path(directory), {"formula": str(baseline)}, override)
    assert float(patched["formula"]) == round(baseline * (hundredths / 100), 2)
