"""The on-disk PE baseline cache in ``scripts/compute_pe_one.py``.

Baseline PE runs are deterministic for the same request, population, PE
version and script. The script pickles them under ``$AXIOM_PE_CACHE_DIR``
and reuses them across subprocess invocations — that is what turns a
reform ``/compare`` from two full PolicyEngine sims into one. No real sim
runs here: ``_build_sim`` and ``_run_program`` are stubbed and the cache dir
is a tmp_path.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import pickle
import re
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


ROOT = Path(__file__).resolve().parents[1]
PE_SCRIPT = ROOT / "scripts" / "compute_pe_one.py"

PROGRAM = "federal-income-tax"
STATE = "US"
YEAR = 2026


def _load_pe_script_with_cache_dir(
    monkeypatch,
    cache_root: Path,
    *,
    population: Path | None = None,
    pe_version: str = "test-pe-1",
    script_path: Path = PE_SCRIPT,
):
    """Import the script fresh with ``AXIOM_PE_CACHE_DIR`` pointed at a tmp dir.

    ``_CACHE_DIR`` is resolved at import time, so the env var has to be set
    before the module body runs — this also covers the env-var wiring itself.
    """
    monkeypatch.setenv("AXIOM_PE_CACHE_DIR", str(cache_root))
    if population is None:
        population = cache_root.parent / "population.h5"
        if not population.exists():
            population.write_bytes(b"population fixture")
    monkeypatch.setenv("AXIOM_POPULACE_US_H5", str(population))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: pe_version)
    spec = importlib.util.spec_from_file_location("compute_pe_one_cachetest", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline(marker: int) -> dict:
    """A baseline shaped like a real one, cheap enough to pickle."""
    return {
        "scope": STATE,
        "axiom_output": "income_tax_main_rates",
        "pe_variable": "income_tax_main_rates",
        "values": np.linspace(0.0, 9_000.0, 10),
        "weights": np.ones(10),
        "axis": np.linspace(0.0, 200_000.0, 10),
        "annual_factor": 1.0,
        "higher_is_better": False,
        "poverty": None,
        "marker": marker,
    }


@pytest.fixture
def pe(monkeypatch, tmp_path):
    """The script with a tmp cache dir and both sim entry points stubbed."""
    module = _load_pe_script_with_cache_dir(monkeypatch, tmp_path / "pe-cache-root")

    module.built_sims = []
    module.program_runs = []

    def fake_build_sim(year, overrides):
        module.built_sims.append((year, overrides))
        return f"sim-{len(module.built_sims)}"

    def fake_run_program(sim, program, state, year):
        module.program_runs.append((sim, program, state, year))
        return _baseline(len(module.program_runs))

    monkeypatch.setattr(module, "_build_sim", fake_build_sim)
    monkeypatch.setattr(module, "_run_program", fake_run_program)
    return module


def _cache_dir(pe) -> Path:
    return pe._CACHE_DIR


def test_cache_dir_comes_from_the_env_var(pe, tmp_path):
    assert _cache_dir(pe) == tmp_path / "pe-cache-root" / "axiom-pe-baseline-cache"


def test_first_call_computes_and_writes_the_pickle(pe):
    result = pe._baseline_run_cached(PROGRAM, STATE, YEAR)

    assert result["marker"] == 1
    assert pe.built_sims == [(YEAR, None)]

    written = pe._cache_path(PROGRAM, STATE, YEAR)
    assert written.exists()
    with written.open("rb") as f:
        assert pickle.load(f)["marker"] == 1


def test_second_call_hits_the_pickle_and_skips_the_sim(pe):
    first = pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    second = pe._baseline_run_cached(PROGRAM, STATE, YEAR)

    assert len(pe.built_sims) == 1, "cache hit still built a PolicyEngine sim"
    assert second["marker"] == first["marker"] == 1
    np.testing.assert_array_equal(second["values"], first["values"])


def test_distinct_keys_get_distinct_cache_files(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    pe._baseline_run_cached(PROGRAM, "CO", YEAR)
    pe._baseline_run_cached("federal-ctc", STATE, YEAR)
    pe._baseline_run_cached(PROGRAM, STATE, YEAR + 1)

    files = set(_cache_dir(pe).glob("*.pkl"))
    assert files == {
        pe._cache_path(PROGRAM, "US", YEAR),
        pe._cache_path(PROGRAM, "CO", YEAR),
        pe._cache_path("federal-ctc", "US", YEAR),
        pe._cache_path(PROGRAM, "US", YEAR + 1),
    }
    assert len(files) == 4
    assert len(pe.built_sims) == 4


def test_corrupt_cache_file_is_recomputed_not_raised(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    cached = pe._cache_path(PROGRAM, STATE, YEAR)
    cached.write_bytes(b"not a pickle, just noise")

    result = pe._baseline_run_cached(PROGRAM, STATE, YEAR)

    assert result["marker"] == 2, "corrupt cache did not trigger a recompute"
    assert len(pe.built_sims) == 2
    # The recompute must also repair the file rather than leave it corrupt.
    with cached.open("rb") as f:
        assert pickle.load(f)["marker"] == 2


def test_truncated_cache_file_is_recomputed(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    cached = pe._cache_path(PROGRAM, STATE, YEAR)
    cached.write_bytes(cached.read_bytes()[:20])

    assert pe._baseline_run_cached(PROGRAM, STATE, YEAR)["marker"] == 2


def test_empty_cache_file_is_recomputed(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    pe._cache_path(PROGRAM, STATE, YEAR).write_bytes(b"")

    assert pe._baseline_run_cached(PROGRAM, STATE, YEAR)["marker"] == 2


def test_write_is_atomic_and_leaves_no_temp_files(pe, monkeypatch):
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        src_path, dst_path = Path(src), Path(dst)
        # A partially-written pickle must never be visible under the real
        # name, so the temp file has to be a *different* path in the *same*
        # directory (os.replace is only atomic within one filesystem).
        assert src_path != dst_path
        assert src_path.parent == dst_path.parent
        assert src_path.exists()
        assert not dst_path.exists()
        replacements.append((src_path, dst_path))
        real_replace(src, dst)

    monkeypatch.setattr(pe.os, "replace", spy_replace)

    pe._baseline_run_cached(PROGRAM, STATE, YEAR)

    assert len(replacements) == 1
    cached = pe._cache_path(PROGRAM, STATE, YEAR)
    assert replacements[0][1] == cached
    assert list(_cache_dir(pe).glob("*.tmp")) == []
    assert list(_cache_dir(pe).iterdir()) == [cached]


def test_cache_dir_is_created_on_demand(pe):
    assert not _cache_dir(pe).exists()

    pe._baseline_run_cached(PROGRAM, STATE, YEAR)

    assert _cache_dir(pe).is_dir()


def test_run_reuses_the_cached_baseline_and_builds_only_the_reform_sim(pe):
    """The point of the disk cache: a reform compare pays for one sim, not two."""
    pe.run(PROGRAM, STATE, YEAR, None)
    assert pe.built_sims == [(YEAR, None)]

    overrides = [{"path": "gov.irs.income.bracket.rates.1", "value": 0.095}]
    result = pe.run(PROGRAM, STATE, YEAR, overrides)

    assert len(pe.built_sims) == 2, "reform run rebuilt the baseline sim"
    assert pe.built_sims[1] == (YEAR, overrides)
    assert result["pe_reform"] is not None
    assert result["scope"] == STATE


def test_run_without_overrides_reports_a_baseline_only_result(pe):
    result = pe.run(PROGRAM, STATE, YEAR, None)

    assert result["pe_reform"] is None
    assert result["pe_baseline"]["annual_cost"] == pytest.approx(45_000.0)
    assert len(result["pe_baseline"]["decile_distribution"]) == 10


def _fresh_baseline(monkeypatch, cache_root, marker, **import_options):
    """Model a fresh PE subprocess sharing only its on-disk cache."""
    module = _load_pe_script_with_cache_dir(monkeypatch, cache_root, **import_options)
    built = []

    def build(year, overrides):
        built.append((year, overrides))
        return object()

    monkeypatch.setattr(module, "_build_sim", build)
    monkeypatch.setattr(module, "_run_program", lambda *args: _baseline(marker))
    return module._baseline_run_cached(PROGRAM, STATE, YEAR), built


@pytest.mark.parametrize("changed_part", ["path", "size", "mtime_ns"])
def test_population_identity_change_recomputes_across_fresh_imports(
    monkeypatch, tmp_path, changed_part
):
    cache_root = tmp_path / "shared-cache"
    population = tmp_path / "population-first.h5"
    population.write_bytes(b"first population")
    original_stat = population.stat()
    first, built = _fresh_baseline(monkeypatch, cache_root, 1, population=population)
    assert first["marker"] == 1 and len(built) == 1

    if changed_part == "path":
        # Equal size and timestamps isolate the resolved path component.
        replacement = tmp_path / "population-second.h5"
        replacement.write_bytes(population.read_bytes())
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        population = replacement
    elif changed_part == "size":
        population.write_bytes(b"larger replacement population")
        os.utime(population, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    else:
        os.utime(
            population,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
        )

    second, built = _fresh_baseline(monkeypatch, cache_root, 2, population=population)

    assert second["marker"] == 2, f"changed population {changed_part} reused stale baseline"
    assert len(built) == 1


def test_identical_inputs_hit_across_fresh_imports(monkeypatch, tmp_path):
    cache_root = tmp_path / "shared-cache"
    first, built = _fresh_baseline(monkeypatch, cache_root, 1)
    assert len(built) == 1

    second, built = _fresh_baseline(monkeypatch, cache_root, 2)

    assert second["marker"] == first["marker"] == 1
    assert built == []


def test_population_alias_uses_resolved_identity(monkeypatch, tmp_path):
    cache_root = tmp_path / "shared-cache"
    population = tmp_path / "population.h5"
    population.write_bytes(b"same population")
    alias = tmp_path / "alias.h5"
    alias.symlink_to(population)
    _fresh_baseline(monkeypatch, cache_root, 1, population=population)

    result, built = _fresh_baseline(monkeypatch, cache_root, 2, population=alias)

    assert result["marker"] == 1
    assert built == []


def test_installed_pe_version_change_recomputes_across_fresh_imports(monkeypatch, tmp_path):
    cache_root = tmp_path / "shared-cache"
    _fresh_baseline(monkeypatch, cache_root, 1, pe_version="1.0.0")

    result, built = _fresh_baseline(monkeypatch, cache_root, 2, pe_version="1.0.1")

    assert result["marker"] == 2, "changed PE installation reused stale baseline"
    assert len(built) == 1


def test_script_content_change_recomputes_across_fresh_imports(monkeypatch, tmp_path):
    cache_root = tmp_path / "shared-cache"
    script = tmp_path / "compute_pe_one.py"
    script.write_bytes(PE_SCRIPT.read_bytes())
    _fresh_baseline(monkeypatch, cache_root, 1, script_path=script)
    original_stat = script.stat()
    # Keep path, length and timestamp unchanged: the script needs a content digest.
    source = script.read_bytes()
    assert b"# Population source" in source
    script.write_bytes(source.replace(b"# Population source", b"# Population SOURCE", 1))
    os.utime(script, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    result, built = _fresh_baseline(monkeypatch, cache_root, 2, script_path=script)

    assert result["marker"] == 2, "changed script contents reused stale baseline"
    assert len(built) == 1


def test_fingerprint_parts_are_hashed_into_a_portable_filename(monkeypatch, tmp_path):
    population = tmp_path / "population with unicode \u2603.h5"
    population.write_bytes(b"same population")
    module = _load_pe_script_with_cache_dir(
        monkeypatch,
        tmp_path / "shared-cache",
        population=population,
        pe_version="1.2.3+arbitrary/version:metadata",
    )

    path = module._cache_path(PROGRAM, STATE, YEAR)

    assert path.parent == module._CACHE_DIR
    assert re.fullmatch(r"federal-income-tax-US-2026-[0-9a-f]{64}\.pkl", path.name)


@settings(max_examples=12, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    program=st.sampled_from(["federal-income-tax", "federal-ctc", "co-snap"]),
    state=st.sampled_from(["US", "CO", "DC", "CA"]),
    year=st.integers(min_value=2020, max_value=2035),
)
def test_repeated_baseline_inputs_never_build_a_second_sim(pe, program, state, year):
    first = pe._baseline_run_cached(program, state, year)
    built_after_first = len(pe.built_sims)

    second = pe._baseline_run_cached(program, state, year)

    assert len(pe.built_sims) == built_after_first
    assert second["marker"] == first["marker"]
