"""The guard must stay *wired into* every patch site, not merely exist.

``test_override_path_confinement`` pins the helper's semantics. These tests pin
the callers: reverting any ``_patch_yaml`` site to a raw ``root / file_relative``
join while leaving the helper in place would pass that module and fail here.

Two layers, because neither alone is sufficient:

* the AST test always runs and names every offending call site exactly, with no
  dependency on rule data. It is a regression guard against the literal raw-join
  revert, NOT a general security proof: an aliased or wrapper-mediated call, or a
  locally shadowed guard, would evade it. The behavioural layer is what actually
  protects;
* the behavioural tests drive the real builders against a synthetic rules tree,
  proving the rejection happens in production code paths rather than only in the
  helper's unit tests. They stop short of ``_compile``, so they need no engine.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from axiom_microsim.run import microsim as M
from axiom_microsim.run.microsim import ParameterOverride

GUARD = "_resolve_override_target"
ESCAPES = ["/etc/passwd", "../../escaped.yaml"]


def _patch_yaml_call_sites() -> list[ast.Call]:
    source = Path(M.__file__).read_text()
    tree = ast.parse(source)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_patch_yaml"
    ]


def test_every_patch_site_is_guarded() -> None:
    """Every `_patch_yaml` target must be a `_resolve_override_target(...)` call."""
    calls = _patch_yaml_call_sites()
    assert calls, "expected to find _patch_yaml call sites; did the function get renamed?"

    unguarded = [
        call.lineno
        for call in calls
        if not (
            call.args
            and isinstance(call.args[0], ast.Call)
            and isinstance(call.args[0].func, ast.Name)
            and call.args[0].func.id == GUARD
        )
    ]
    assert not unguarded, (
        f"_patch_yaml called with an unguarded path at line(s) {unguarded}: "
        f"every call must pass {GUARD}(root, ov.file_relative)"
    )


def test_all_known_patch_sites_are_present() -> None:
    """Guard against a site being deleted rather than fixed."""
    assert len(_patch_yaml_call_sites()) == 4, (
        "expected 4 _patch_yaml call sites; if a builder was added or removed, "
        "update this count so new sites cannot slip in unguarded"
    )


@pytest.fixture
def fake_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal stand-in for the real rule trees, plus a sentinel to escape to.

    `mkdtemp` is redirected under `tmp_path` so the scratch tree is a known
    depth below it. Without that the builders' scratch lands in the system temp
    dir, `scratch/rules-us/../../escaped.yaml` resolves somewhere else entirely,
    and the sentinel assertion would be cosmetic rather than checking the actual
    destination a traversal would hit.
    """
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    monkeypatch.setattr(
        M.tempfile, "mkdtemp", lambda **kw: str(_mkdtemp_under(scratch_parent, **kw))
    )
    for name in ("rules-us", "rules-us-co"):
        tree = tmp_path / name
        (tree / "policies").mkdir(parents=True)
        (tree / "policies" / "p.yaml").write_text("rules: []\n")
        monkeypatch.setattr(M, "RULES_US_DIR" if name == "rules-us" else "RULES_US_CO_DIR", tree)
    # scratch/<mkdtemp>/rules-us/../../escaped.yaml  ->  scratch/escaped.yaml
    sentinel = scratch_parent / "escaped.yaml"
    sentinel.write_text("untouched\n")
    monkeypatch.setattr(M, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(shutil, "copytree", _shallow_copytree)
    return sentinel


def _mkdtemp_under(parent: Path, **kwargs) -> Path:
    d = parent / f"{kwargs.get('prefix', 'tmp')}{len(list(parent.iterdir()))}"
    d.mkdir()
    return d


def _shallow_copytree(src, dst, **kwargs) -> None:
    Path(dst).mkdir(parents=True, exist_ok=True)
    for item in Path(src).rglob("*"):
        rel = item.relative_to(src)
        if item.is_dir():
            (Path(dst) / rel).mkdir(parents=True, exist_ok=True)
        else:
            (Path(dst) / rel).write_bytes(item.read_bytes())


@pytest.mark.parametrize(
    "builder",
    [
        "_patched_program_for_fed_income_tax",
        "_artifact_for",
        "_ctc_artifact_for",
        "_fed_artifact_for",
    ],
)
@pytest.mark.parametrize("escape", ESCAPES)
def test_builders_reject_escaping_overrides(builder: str, escape: str, fake_rules: Path) -> None:
    override = ParameterOverride(
        repo="rules-us",
        file_relative=escape,
        parameter="anything",
        patch_kind="scale_values",
        multiplier=2.0,
    )

    with pytest.raises(ValueError) as excinfo:
        getattr(M, builder)([override])

    # Must be the guard rejecting, not an incidental failure downstream.
    assert "file_relative" in str(excinfo.value)
    assert fake_rules.read_text() == "untouched\n", "sentinel outside the rules root was modified"
