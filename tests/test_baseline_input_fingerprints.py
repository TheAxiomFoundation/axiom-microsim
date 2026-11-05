"""A baseline result belongs to the exact engine, rules/artifact and population."""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from axiom_microsim import server
from axiom_microsim.run import microsim


PROGRAMS = ["co-snap", "federal-ctc", "federal-income-tax"]


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for name in ("co-snap", "federal-ctc"):
        (artifacts / f"{name}.compiled.json").write_text("old artifact")
    rules = tmp_path / "rulespec-us"
    rules.mkdir()
    (rules / "main.yaml").write_text("old main")
    (rules / "import.yaml").write_text("old import")
    population = tmp_path / "population.h5"
    population.write_text("old population")
    engine = tmp_path / "engine"
    engine.write_text("old engine")
    monkeypatch.setattr(microsim, "ARTIFACTS_DIR", artifacts)
    monkeypatch.setattr(microsim, "CO_SNAP_BASELINE_ARTIFACT", artifacts / "co-snap.compiled.json")
    monkeypatch.setattr(microsim, "RULES_US_DIR", rules)
    monkeypatch.setattr(microsim, "ENGINE_BIN", engine)
    monkeypatch.delenv("AXIOM_ECPS_PATH", raising=False)
    monkeypatch.setenv("AXIOM_POPULACE_US_H5", str(population))
    return artifacts, rules, population, engine


def _replace(path, part):
    old = path.stat()
    if part == "path":
        replacement = path.with_name("replacement-" + path.name)
        replacement.write_bytes(path.read_bytes())
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        return replacement
    if part == "size":
        path.write_bytes(path.read_bytes() + b"new")
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    else:
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    return path


def _assert_recomputed(program, change):
    calls = []

    def compute():
        result = object()
        calls.append(result)
        return result

    first = server._cached_baseline(program, "US", 2026, compute)
    assert server._cached_baseline(program, "US", 2026, compute) is first
    change()
    second = server._cached_baseline(program, "US", 2026, compute)
    assert second is not first, "changed baseline inputs reused a stale result"
    assert server._cached_baseline(program, "US", 2026, compute) is second
    assert len(calls) == 2


@pytest.mark.parametrize("program", ["co-snap", "federal-ctc"])
@pytest.mark.parametrize("part", ["path", "size", "mtime_ns"])
def test_replaced_baseline_artifact_recomputes(inputs, monkeypatch, program, part):
    artifacts, _, _, _ = inputs

    def change():
        path = _replace(artifacts / f"{program}.compiled.json", part)
        if part == "path":
            if program == "co-snap":
                monkeypatch.setattr(microsim, "CO_SNAP_BASELINE_ARTIFACT", path)
            else:
                replacement_dir = artifacts / "replacement"
                replacement_dir.mkdir()
                path.rename(replacement_dir / f"{program}.compiled.json")
                monkeypatch.setattr(microsim, "ARTIFACTS_DIR", replacement_dir)

    _assert_recomputed(program, change)


@pytest.mark.parametrize("program", PROGRAMS)
@pytest.mark.parametrize("part", ["path", "size", "mtime_ns"])
def test_replaced_population_recomputes(inputs, monkeypatch, program, part):
    _, _, population, _ = inputs

    def change():
        path = _replace(population, part)
        monkeypatch.setenv("AXIOM_POPULACE_US_H5", str(path))

    _assert_recomputed(program, change)


@pytest.mark.parametrize("part", ["path", "size", "mtime_ns"])
def test_dense_income_tax_rules_tree_recomputes(inputs, monkeypatch, part):
    _, rules, _, _ = inputs

    def change():
        path = _replace(rules / "import.yaml", part)
        if part == "path":
            (rules / "import.yaml").unlink()
            assert path.exists()

    _assert_recomputed("federal-income-tax", change)


@pytest.mark.parametrize("program", ["co-snap", "federal-ctc"])
@pytest.mark.parametrize("part", ["path", "size", "mtime_ns"])
def test_replaced_subprocess_engine_recomputes(inputs, monkeypatch, program, part):
    _, _, _, engine = inputs

    def change():
        monkeypatch.setattr(microsim, "ENGINE_BIN", _replace(engine, part))

    _assert_recomputed(program, change)


def test_legacy_population_override_takes_precedence(inputs, monkeypatch, tmp_path):
    legacy = tmp_path / "legacy.h5"
    legacy.write_bytes(b"legacy")
    monkeypatch.setenv("AXIOM_ECPS_PATH", str(legacy))
    _assert_recomputed("co-snap", lambda: _replace(legacy, "mtime_ns"))


@settings(max_examples=20, deadline=None)
@given(st.lists(st.integers(2000, 2100), min_size=1, max_size=20))
def test_baseline_cache_reuses_identical_inputs_per_scope(years):
    seen = {}
    for year in years:
        result = server._cached_baseline("federal-ctc", "US", year, object)
        assert result is seen.setdefault(year, result)
    assert len(server._BASELINE_RESULT_CACHE) <= server._CACHE_MAX_ENTRIES


@pytest.mark.parametrize("part", ["path", "size", "mtime_ns", "version"])
def test_dense_engine_identity_recomputes(inputs, monkeypatch, tmp_path, part):
    import importlib.metadata
    import importlib.util
    from types import SimpleNamespace

    package = tmp_path / "axiom_rules_engine"
    package.mkdir()
    init = package / "__init__.py"
    init.write_bytes(b"engine")
    dense = package / "dense.py"
    dense.write_bytes(b"dense engine")
    identity = SimpleNamespace(origin=str(init), version="1.0")
    real_spec = importlib.util.find_spec
    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: identity if name == "axiom_rules_engine" else real_spec(name),
    )
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: identity.version if name == "axiom-rules-engine" else real_version(name),
    )

    def change():
        if part == "version":
            identity.version = "2.0"
        elif part == "path":
            new_package = tmp_path / "replacement-engine"
            new_package.mkdir()
            new_init = new_package / "__init__.py"
            new_init.write_bytes(init.read_bytes())
            new_dense = new_package / "dense.py"
            new_dense.write_bytes(dense.read_bytes())
            for old, new in [(init, new_init), (dense, new_dense)]:
                stat = old.stat()
                os.utime(new, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            identity.origin = str(new_init)
        else:
            _replace(dense, part)

    _assert_recomputed("federal-income-tax", change)


@pytest.mark.parametrize("part", ["path", "size", "mtime_ns"])
def test_dense_native_engine_replacement_recomputes(inputs, monkeypatch, tmp_path, part):
    import importlib.util
    from types import SimpleNamespace

    native = tmp_path / "axiom_rules_engine_dense.so"
    native.write_bytes(b"native engine")
    spec = SimpleNamespace(origin=str(native), submodule_search_locations=None)
    real_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: spec if name == "axiom_rules_engine_dense" else real_spec(name),
    )

    def change():
        spec.origin = str(_replace(native, part))

    _assert_recomputed("federal-income-tax", change)


def test_default_population_uses_cached_pin_without_download(inputs, monkeypatch):
    import huggingface_hub

    from axiom_microsim.data import populace_loader

    _, _, population, _ = inputs
    monkeypatch.delenv("AXIOM_POPULACE_US_H5")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: str(population))

    def forbid_download():
        raise AssertionError("cache identity downloaded population data")

    monkeypatch.setattr(populace_loader, "_download_pinned", forbid_download)
    _assert_recomputed("federal-ctc", lambda: _replace(population, "mtime_ns"))


def test_dense_rules_fingerprint_tracks_executed_staged_copy(inputs, monkeypatch, tmp_path):
    _, source, _, _ = inputs
    noncanonical = tmp_path / "source-rules"
    source.rename(noncanonical)
    staged = tmp_path / "stage"
    active = staged / "rulespec-us"
    active.mkdir(parents=True)
    imported = active / "import.yaml"
    imported.write_bytes(b"active rule")
    monkeypatch.setattr(microsim, "RULES_US_DIR", noncanonical)
    monkeypatch.setattr(microsim, "_BASELINE_RULES_SCRATCH", staged)
    _assert_recomputed("federal-income-tax", lambda: _replace(imported, "mtime_ns"))


def test_first_compile_is_cached_under_created_artifact_identity(inputs):
    artifacts, _, _, _ = inputs
    artifact = artifacts / "federal-ctc.compiled.json"
    artifact.unlink()
    calls = []

    def compile_and_compute():
        calls.append(1)
        artifact.write_bytes(b"created compiled artifact")
        return object()

    first = server._cached_baseline("federal-ctc", "US", 2026, compile_and_compute)
    assert server._cached_baseline("federal-ctc", "US", 2026, compile_and_compute) is first
    assert calls == [1]
