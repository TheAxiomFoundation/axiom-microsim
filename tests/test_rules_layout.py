"""Compile from either rules layout, with or without ``--rulespec-root`` (issue #25).

rulespec-us has a flat layout (``rulespec-us/statutes/...`` beside a separate
``rulespec-us-co`` checkout) and a monorepo layout (``rulespec-us/us/...``,
``rulespec-us/us-co/...``). Three engine generations read them:

* ``rooted``, like axiom-rules-engine 753ce7d and 89571cc (0.2.2): ``compile
  --help`` exits 0 and names ``--rulespec-root``; ``compile`` requires an
  absolute root named ``rulespec-<country>`` and refuses a root whose top level
  holds ``policies/``, ``regulations/`` or ``statutes/``.
* ``release``, like 98af0dc (0.1.2): rejects ``--help`` and ``--rulespec-root``
  as unknown compile arguments, and names modules correctly from either layout.
* ``legacy``, like f2412104, the SHA modal_app.py pins: rejects them the same
  way, and names a monorepo module after its jurisdiction directory too
  (``us:us/statutes/26/24/h``).

Those behaviours were observed by running each binary. The fakes below
reproduce only them, so these tests need no engine; ``test_engine_compile.py``
drives the same builders against a real binary when one is configured.
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

from axiom_microsim.project.federal_income_tax import FedIncomeTaxProjection
from axiom_microsim.run import microsim as M
from axiom_microsim.run.microsim import ParameterOverride

CTC = "statutes/26/24/h.yaml"
INCOME_TAX = "statutes/26/1/j.yaml"
MAX_ALLOTMENT = "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml"
BRACKETS = "policies/irs/rev-proc-2025-32/income-tax-brackets.yaml"
CO_SNAP = M.CO_SNAP_PROGRAM_REL

# file under each repo → (parameter, version body) the fake rules declare.
RULES_US_FILES = {
    CTC: ("ctc_child_amount_under_subsection_h", {"formula": "2200"}),
    INCOME_TAX: ("income_tax_main_rates", {"formula": "0"}),
    MAX_ALLOTMENT: ("snap_maximum_allotment_table", {"values": {1: 298, 2: 546}}),
    BRACKETS: ("income_tax_bracket_rates", {"values": {1: 0.1, 2: 0.12}}),
}
RULES_US_CO_FILES = {CO_SNAP: ("co_snap_example_parameter", {"formula": "10"})}

CTC_OVERRIDE = ParameterOverride(
    "rules-us", CTC, "ctc_child_amount_under_subsection_h", "set_formula", formula="3000"
)
ALLOTMENT_OVERRIDE = ParameterOverride(
    "rules-us", MAX_ALLOTMENT, "snap_maximum_allotment_table", "scale_values", multiplier=2.0
)
BRACKETS_OVERRIDE = ParameterOverride(
    "rules-us", BRACKETS, "income_tax_bracket_rates", "scale_values", multiplier=1.5
)
CO_OVERRIDE = ParameterOverride(
    "rules-us-co", CO_SNAP, "co_snap_example_parameter", "set_formula", formula="20"
)

FAKE_ENGINE = r"""
import json
import os
import sys
from pathlib import Path

FLAT_DIRS = ("policies", "regulations", "statutes")


def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


args = sys.argv[1:]
with open(os.environ["FAKE_ENGINE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args[:1] != ["compile"]:
    fail(f"unknown command `{args[0] if args else ''}`")
if args[1:] == ["--help"]:
    if KIND != "rooted":
        fail("unknown compile argument `--help`")
    print("usage: axiom-rules-engine compile --program <absolute rules.yaml> "
          "--rulespec-root <absolute rulespec-cc> --output <compiled.json>")
    sys.exit(0)
opts = {}
rest = iter(args[1:])
for arg in rest:
    if arg in ("--program", "--output") or (arg == "--rulespec-root" and KIND == "rooted"):
        opts[arg] = next(rest)
    else:
        fail(f"unknown compile argument `{arg}`")
program = Path(opts["--program"])
if KIND == "rooted":
    if "--rulespec-root" not in opts:
        fail("invalid RuleSpec repository root configuration: at least one explicit "
             "rulespec-<country> root is required")
    root = Path(opts["--rulespec-root"])
    if not root.is_absolute() or root.resolve() != root or not root.name.startswith("rulespec-"):
        fail(f"invalid RuleSpec repository root configuration: `{root}` is not canonical")
    for name in FLAT_DIRS:
        if (root / name).is_dir():
            fail(f"invalid RuleSpec repository root configuration: root-level `{name}/` is "
                 f"forbidden in `{root}`; content belongs below a direct jurisdiction directory")
    if not program.is_absolute() or not program.is_relative_to(root):
        fail(f"`{program}` is not inside a configured root")
    country, parts = root.name.removeprefix("rulespec-"), program.relative_to(root).parts
else:
    anchor = max(i for i, part in enumerate(program.parts) if part.startswith("rulespec-"))
    country, parts = program.parts[anchor].removeprefix("rulespec-"), program.parts[anchor + 1:]
if KIND != "legacy" and parts and (parts[0] == country or parts[0].startswith(country + "-")):
    country, parts = parts[0], parts[1:]
module = country + ":" + "/".join(parts).removesuffix(".yaml")
Path(opts["--output"]).write_text(json.dumps({"program": {"derived": [{"id": module + "#out"}]}}))
if os.environ.get("FAKE_ENGINE_FAIL_AFTER_WRITE"):
    fail("write failed after the artifact was opened")
"""


def _write_rules(directory: Path, files: dict[str, tuple[str, dict]]) -> None:
    yaml = YAML()
    for rel, (parameter, version) in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            yaml.dump({"rules": [{"name": parameter, "versions": [version]}]}, f)


def _monorepo(root: Path) -> Path:
    _write_rules(root / "us", RULES_US_FILES)
    _write_rules(root / "us-co", RULES_US_CO_FILES)
    # Neighbours a stage must leave behind.
    _write_rules(root / "us-ca", {"statutes/x.yaml": ("ca", {"formula": "1"})})
    (root / "bulk").mkdir()
    (root / "bulk" / "big.json").write_text("{}")
    (root / "README.md").write_text("monorepo\n")
    return root


class Rig:
    """Fake rules trees and a fake engine, wired into the runner module."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.log = tmp_path / "engine.log"
        monkeypatch.setenv("FAKE_ENGINE_LOG", str(self.log))
        # Scratch sits behind a symlink, as the system temp dir does on macOS,
        # so a root that was not made canonical fails the rooted fake.
        (tmp_path / "scratch-real").mkdir()
        scratch_parent = tmp_path / "scratch"
        scratch_parent.symlink_to(tmp_path / "scratch-real")
        counter = iter(range(10_000))
        monkeypatch.setattr(
            M.tempfile,
            "mkdtemp",
            lambda prefix="tmp", **_: str(_mkdir(scratch_parent / f"{prefix}{next(counter)}")),
        )
        monkeypatch.setattr(M, "ARTIFACTS_DIR", tmp_path / "artifacts")
        monkeypatch.setattr(
            M, "CO_SNAP_BASELINE_ARTIFACT", tmp_path / "artifacts" / "co-snap.compiled.json"
        )
        monkeypatch.setattr(M, "_BASELINE_RULES_SCRATCH", None)
        M._probe_rulespec_root_flag.cache_clear()

    def engine(self, kind: str) -> Path:
        path = self.tmp / f"engine-{kind}"
        path.write_text(f"#!{sys.executable}\nKIND = {kind!r}\n{FAKE_ENGINE}")
        path.chmod(0o755)
        self.monkeypatch.setattr(M, "ENGINE_BIN", path)
        return path

    def flat_rules(self, name: str = "rules-us") -> tuple[Path, Path]:
        us, co = self.tmp / name, self.tmp / f"{name}-co"
        _write_rules(us, RULES_US_FILES)
        _write_rules(co, RULES_US_CO_FILES)
        self.monkeypatch.setattr(M, "RULES_US_DIR", us)
        self.monkeypatch.setattr(M, "RULES_US_CO_DIR", co)
        return us, co

    def monorepo(self, name: str = "rulespec-us") -> Path:
        root = _monorepo(self.tmp / name)
        self.monkeypatch.setattr(M, "RULES_US_DIR", root)
        # The monorepo holds us-co/; a separate Colorado checkout plays no part.
        self.monkeypatch.setattr(M, "RULES_US_CO_DIR", self.tmp / "absent-rules-us-co")
        return root

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def compiles(self) -> list[list[str]]:
        return [call for call in self.calls() if call[1:] != ["--help"]]


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path


def _option(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _read_rule(path: Path) -> dict:
    return YAML().load(path.read_text())["rules"][0]["versions"][0]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


# Each builder, the program it compiles, and an override it accepts.
BUILDERS = {
    "ctc": (M._ctc_artifact_for, "rules-us", CTC, CTC_OVERRIDE),
    "co-snap": (M._artifact_for, "rules-us-co", CO_SNAP, ALLOTMENT_OVERRIDE),
    "income-tax-json": (M._fed_artifact_for, "rules-us", INCOME_TAX, BRACKETS_OVERRIDE),
}


# --- Layout detection ----------------------------------------------------------


def test_layout_reads_the_top_level(tmp_path: Path) -> None:
    flat, mono = tmp_path / "flat", tmp_path / "mono"
    _write_rules(flat, RULES_US_FILES)
    _monorepo(mono)
    assert M._rules_layout(flat) == "flat"
    assert M._rules_layout(mono) == "jurisdiction"


@pytest.mark.parametrize("dirs", [(), ("us", "statutes")])
def test_layout_refuses_a_tree_it_cannot_place(tmp_path: Path, dirs: tuple[str, ...]) -> None:
    for name in dirs:
        (tmp_path / name).mkdir()
    with pytest.raises(ValueError, match="cannot tell the RuleSpec layout"):
        M._rules_layout(tmp_path)


# --- Engine probe --------------------------------------------------------------


@pytest.mark.parametrize(("kind", "takes_root"), [("rooted", True), ("release", False)])
def test_probe_reads_the_compile_contract(rig: Rig, kind: str, takes_root: bool) -> None:
    rig.engine(kind)
    assert M._engine_takes_rulespec_root() is takes_root


def test_probe_runs_once_per_binary_build(rig: Rig) -> None:
    binary = rig.engine("release")
    assert not M._engine_takes_rulespec_root()
    assert not M._engine_takes_rulespec_root()
    assert rig.calls() == [["compile", "--help"]]

    # A rebuilt binary at the same path is probed again.
    binary.write_text(binary.read_text().replace("KIND = 'release'", "KIND = 'rooted'"))
    assert M._engine_takes_rulespec_root()
    assert rig.calls() == [["compile", "--help"]] * 2


# --- Flat layout: the command production runs today ----------------------------


@pytest.mark.parametrize("kind", ["legacy", "release"])
@pytest.mark.parametrize("builder", sorted(BUILDERS))
@pytest.mark.parametrize("reform", [False, True])
def test_flat_compiles_run_the_pre_25_command(
    rig: Rig, kind: str, builder: str, reform: bool
) -> None:
    """``compile --program <staged program> --output <artifact>``, nothing more."""
    build, repo, program, override = BUILDERS[builder]
    rig.flat_rules()
    rig.engine(kind)
    artifact, scratch = build([override] if reform else None)

    (argv,) = rig.compiles()
    staged = "rulespec-us-co" if repo == "rules-us-co" else "rulespec-us"
    assert argv[:2] == ["compile", "--program"] and argv[3:] == ["--output", str(artifact)]
    assert Path(argv[2]).parts[-len(Path(program).parts) - 1 :] == (staged, *Path(program).parts)
    assert artifact.exists()
    if scratch is not None:
        assert Path(argv[2]).is_relative_to(scratch)
        shutil.rmtree(scratch)


# --- Monorepo layout ----------------------------------------------------------


@pytest.mark.parametrize("builder", sorted(BUILDERS))
@pytest.mark.parametrize("reform", [False, True])
def test_rooted_engine_gets_the_staged_root(rig: Rig, builder: str, reform: bool) -> None:
    build, repo, program, override = BUILDERS[builder]
    rig.monorepo()
    rig.engine("rooted")
    artifact, scratch = build([override] if reform else None)

    (argv,) = rig.compiles()
    root, program_path = Path(_option(argv, "--rulespec-root")), Path(_option(argv, "--program"))
    assert root.is_absolute() and root.resolve() == root and root.name == "rulespec-us"
    assert program_path == root / M.JURISDICTION_DIRS[repo] / program
    assert _option(argv, "--output") == str(artifact)
    assert json.loads(artifact.read_text())["program"]["derived"][0]["id"].startswith(
        f"{M.JURISDICTION_DIRS[repo]}:{program.removesuffix('.yaml')}#"
    )
    if scratch is not None:
        assert root.is_relative_to(scratch.resolve())
        shutil.rmtree(scratch)


def test_release_engine_compiles_the_monorepo_without_a_root(rig: Rig) -> None:
    rig.monorepo()
    rig.engine("release")

    artifact, scratch = M._ctc_artifact_for([CTC_OVERRIDE])

    (argv,) = rig.compiles()
    assert "--rulespec-root" not in argv
    assert Path(_option(argv, "--program")) == scratch / "rulespec-us" / "us" / CTC
    shutil.rmtree(scratch)


@pytest.mark.parametrize("include_co", [False, True])
def test_stage_copies_only_the_jurisdictions_the_programs_read(rig: Rig, include_co: bool) -> None:
    rig.monorepo()

    scratch = M._staged_rules_trees(include_co=include_co)

    staged = scratch / "rulespec-us"
    expected = ["us", "us-co"] if include_co else ["us"]
    assert sorted(p.name for p in staged.iterdir()) == expected
    assert (staged / "us" / CTC).is_file()
    assert M._scratch_tree(scratch).layout == "jurisdiction"
    shutil.rmtree(scratch)


def test_stage_names_a_missing_jurisdiction(rig: Rig) -> None:
    root = rig.monorepo()
    shutil.rmtree(root / "us-co")
    with pytest.raises(FileNotFoundError, match="us-co/ missing"):
        M._staged_rules_trees(include_co=True)


@pytest.mark.parametrize(
    ("override", "staged_rel"),
    [(ALLOTMENT_OVERRIDE, Path("us") / MAX_ALLOTMENT), (CO_OVERRIDE, Path("us-co") / CO_SNAP)],
)
def test_monorepo_overrides_patch_their_jurisdiction_in_the_stage(
    rig: Rig, override: ParameterOverride, staged_rel: Path
) -> None:
    """``repo`` + ``file_relative`` keep their flat-era meaning; sources stay untouched."""
    root = rig.monorepo()
    rig.engine("rooted")
    before = _read_rule(root / staged_rel)

    _, scratch = M._artifact_for([override])

    patched = _read_rule(scratch / "rulespec-us" / staged_rel)
    assert patched != before
    if override.patch_kind == "scale_values":
        assert dict(patched["values"]) == {1: 596, 2: 1092}
    else:
        assert patched["formula"] == override.formula
    assert _read_rule(root / staged_rel) == before
    shutil.rmtree(scratch)


@pytest.mark.parametrize("escape", ["/etc/passwd", "../us-ca/statutes/x.yaml", "../../x.yaml"])
def test_monorepo_overrides_cannot_leave_their_jurisdiction(rig: Rig, escape: str) -> None:
    rig.monorepo()
    rig.engine("rooted")
    override = ParameterOverride("rules-us", escape, "x", "scale_values", multiplier=2.0)

    with pytest.raises(ValueError, match="file_relative"):
        M._ctc_artifact_for([override])
    assert rig.compiles() == []


# --- Mismatches fail with the reason -------------------------------------------


def test_rooted_engine_refusing_flat_rules_says_why(rig: Rig) -> None:
    rig.flat_rules()
    rig.engine("rooted")
    with pytest.raises(RuntimeError, match="root-level `policies/` is forbidden"):
        M._ctc_artifact_for(None)


@pytest.mark.parametrize("reform", [False, True])
def test_flat_only_engine_on_the_monorepo_is_named(rig: Rig, reform: bool) -> None:
    """f2412104 compiles the monorepo's CTC as ``us:us/statutes/26/24/h``; say so.

    Every time: the misnamed artifact must not stay behind, or the next run
    would reuse the cached baseline and fail later with a vaguer error.
    """
    rig.monorepo()
    rig.engine("legacy")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="reads only the flat layout"):
            M._ctc_artifact_for([CTC_OVERRIDE] if reform else None)
    assert len(rig.compiles()) == 2
    assert not list(M.ARTIFACTS_DIR.iterdir())


# Each reform builder with each way it can fail after staging. The dense reform
# compiles in process, so only a bad override can fail it here.
FAILED_REFORMS = [
    (builder, failure)
    for builder in [*sorted(BUILDERS), "income-tax-dense"]
    for failure in ("unknown parameter", "compile refused")
    if (builder, failure) != ("income-tax-dense", "compile refused")
]


@pytest.mark.parametrize("layout", ["flat", "jurisdiction"])
@pytest.mark.parametrize(("builder", "failure"), FAILED_REFORMS)
def test_a_failed_reform_leaves_no_stage_or_artifact(
    rig: Rig, layout: str, builder: str, failure: str
) -> None:
    """The builder raises before the caller could clean up, so it must."""
    if layout == "flat":
        rig.flat_rules()
    else:
        rig.monorepo()
    # A rooted engine refuses a flat root, and a legacy one misnames the
    # monorepo's modules, which the check refuses; the release one reads both.
    refusing = {"flat": "rooted", "jurisdiction": "legacy"}[layout]
    rig.engine(refusing if failure == "compile refused" else "release")
    if builder == "income-tax-dense":
        build, override = M._patched_program_for_fed_income_tax, BRACKETS_OVERRIDE
    else:
        build, _, _, override = BUILDERS[builder]
    if failure == "unknown parameter":
        override = ParameterOverride(
            override.repo, override.file_relative, "no_such_parameter", "scale_values", 2.0
        )

    with pytest.raises((KeyError, RuntimeError)):
        build([override])

    assert not list((rig.tmp / "scratch").iterdir())
    assert not M.ARTIFACTS_DIR.exists() or not list(M.ARTIFACTS_DIR.iterdir())


@pytest.mark.parametrize("reform", [False, True])
def test_a_refused_compile_leaves_no_partial_artifact(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, reform: bool
) -> None:
    """A partial baseline would be reused as the cache on the next call."""
    rig.flat_rules()
    rig.engine("release")
    monkeypatch.setenv("FAKE_ENGINE_FAIL_AFTER_WRITE", "1")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="write failed after the artifact was opened"):
            M._ctc_artifact_for([CTC_OVERRIDE] if reform else None)

    assert len(rig.compiles()) == 2
    assert not list(M.ARTIFACTS_DIR.iterdir())
    assert not list((rig.tmp / "scratch").iterdir())


def test_a_missing_checkout_is_named(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(M, "RULES_US_DIR", rig.tmp / "no-such-rules-us")
    for build in (M._baseline_rules_tree, M._staged_rules_trees):
        with pytest.raises(FileNotFoundError, match="rules-us missing"):
            build()


# --- Baseline tree for the dense path -----------------------------------------


def test_canonical_checkouts_serve_the_baseline_in_place(rig: Rig) -> None:
    flat_us, flat_co = rig.flat_rules(name="rulespec-us")
    assert M._baseline_rules_tree() == M._RulesTree("flat", flat_us, flat_co)

    root = rig.monorepo(name="checkouts/rulespec-us")
    assert M._baseline_rules_tree() == M._RulesTree("jurisdiction", root.resolve())
    assert M._BASELINE_RULES_SCRATCH is None


@pytest.mark.parametrize("layout", ["flat", "jurisdiction"])
def test_other_checkouts_stage_one_baseline_copy(rig: Rig, layout: str) -> None:
    if layout == "flat":
        rig.flat_rules(name="rules-us")
    else:
        rig.monorepo(name="checkout")

    first = M._baseline_rules_tree()
    second = M._baseline_rules_tree()

    assert first == second and first.layout == layout
    assert first.root == M._BASELINE_RULES_SCRATCH / "rulespec-us"
    assert (first.repo_dir("rules-us") / INCOME_TAX).is_file()
    shutil.rmtree(M._BASELINE_RULES_SCRATCH)


# --- Dense binding --------------------------------------------------------------


class _RootedDense:
    """The binding's ``from_file`` as of axiom-rules-engine 753ce7d.

    Like the engine, it refuses a root or program path that isn't canonical.
    """

    calls: list[dict] = []

    @classmethod
    def from_file(cls, path, *, rulespec_roots, entity=None, outputs=None):
        roots = list(rulespec_roots)
        for candidate in (path, *roots):
            if Path(candidate).resolve() != Path(candidate):
                raise ValueError(f"`{candidate}` is an alias; pass its exact canonical path")
        cls.calls.append({"path": path, "rulespec_roots": roots, "entity": entity})
        return cls()

    def execute(self, **kwargs):
        return {"outputs": {"income_tax_main_rates": [7.0]}}


class _UnrootedDense(_RootedDense):
    """The binding's ``from_file`` as of f2412104."""

    calls: list[dict] = []

    @classmethod
    def from_file(cls, path, *, entity=None):
        cls.calls.append({"path": path, "entity": entity})
        return cls()


@pytest.mark.parametrize("reform", [False, True])
@pytest.mark.parametrize(
    ("binding", "layout"), [(_RootedDense, "jurisdiction"), (_UnrootedDense, "flat")]
)
def test_dense_compile_passes_roots_only_to_a_binding_that_takes_them(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, binding: type, layout: str, reform: bool
) -> None:
    """A reform compiles from the stage, which sits behind the Rig's symlink."""
    if layout == "flat":
        rig.flat_rules(name="rulespec-us")
    else:
        rig.monorepo()
    binding.calls.clear()
    monkeypatch.setitem(
        sys.modules,
        "axiom_rules_engine.dense",
        types.SimpleNamespace(CompiledDenseProgram=binding),
    )
    if reform:
        program, scratch = M._patched_program_for_fed_income_tax([BRACKETS_OVERRIDE])
        tree = M._scratch_tree(scratch)
        assert scratch.resolve() != scratch
    else:
        tree = M._baseline_rules_tree()
        program = tree.repo_dir("rules-us") / INCOME_TAX
    projection = FedIncomeTaxProjection(
        n_tax_units=1,
        period_year=2026,
        tax_unit_weight=np.ones(1),
        inputs={"us:statutes/26/1/j#input.taxable_income": np.array([1000])},
        agi_proxy=np.zeros(1),
        standard_deduction=np.zeros(1),
    )

    out = M._execute_fed_income_tax_dense(
        projection, program, 2026, ("income_tax_main_rates",), tree
    )

    assert out["income_tax_main_rates"].tolist() == [7.0]
    (call,) = binding.calls
    if binding is _RootedDense:
        assert call == {
            "path": str(program.resolve()),
            "rulespec_roots": [str(tree.root.resolve())],
            "entity": M.TAX_UNIT,
        }
    else:
        assert call == {"path": str(program), "entity": M.TAX_UNIT}


# --- Property: an accepted override stays in its jurisdiction ------------------

SEGMENTS = ["us", "us-co", "us-ca", "..", ".", "", "statutes", "h.yaml", "link", "%2e%2e", "\\"]


@pytest.fixture(scope="module")
def staged_monorepo(tmp_path_factory: pytest.TempPathFactory) -> M._RulesTree:
    scratch = tmp_path_factory.mktemp("staged")
    root = _monorepo(scratch / "rulespec-us")
    # A symlink inside a jurisdiction that leads to a neighbour must not count.
    (root / "us" / "link").symlink_to(root / "us-ca")
    return M._scratch_tree(scratch)


@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    repo=st.sampled_from(sorted(M.JURISDICTION_DIRS)),
    file_relative=st.one_of(
        st.lists(st.sampled_from(SEGMENTS), max_size=6).map("/".join),
        st.lists(st.sampled_from(SEGMENTS), max_size=6).map(lambda s: "/" + "/".join(s)),
        st.text(max_size=30),
    ),
)
def test_accepted_overrides_resolve_inside_their_jurisdiction(
    staged_monorepo: M._RulesTree, repo: str, file_relative: str
) -> None:
    # Spelled out here, not taken from repo_dir, so a wrong mapping fails too.
    jurisdiction = (
        staged_monorepo.root / {"rules-us": "us", "rules-us-co": "us-co"}[repo]
    ).resolve()
    try:
        target = M._resolve_override_target(staged_monorepo.repo_dir(repo), file_relative)
    except ValueError:
        return
    assert target.is_relative_to(jurisdiction)
