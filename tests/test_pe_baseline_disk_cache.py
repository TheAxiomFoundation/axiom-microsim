"""The on-disk PE baseline cache in ``scripts/compute_pe_one.py``.

Baseline PE runs are deterministic in (program, state, year), so the script
pickles them under ``$AXIOM_PE_CACHE_DIR`` and reuses them across subprocess
invocations — that is what turns a reform ``/compare`` from two full
PolicyEngine sims into one. No real sim runs here: ``_build_sim`` and
``_run_program`` are stubbed and the cache dir is a tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import pickle
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
PE_SCRIPT = ROOT / "scripts" / "compute_pe_one.py"

PROGRAM = "federal-income-tax"
STATE = "US"
YEAR = 2026
CACHE_FILENAME = f"{PROGRAM}-{STATE}-{YEAR}.pkl"


def _load_pe_script_with_cache_dir(monkeypatch, cache_root: Path):
    """Import the script fresh with ``AXIOM_PE_CACHE_DIR`` pointed at a tmp dir.

    ``_CACHE_DIR`` is resolved at import time, so the env var has to be set
    before the module body runs — this also covers the env-var wiring itself.
    """
    monkeypatch.setenv("AXIOM_PE_CACHE_DIR", str(cache_root))
    spec = importlib.util.spec_from_file_location("compute_pe_one_cachetest", PE_SCRIPT)
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

    written = _cache_dir(pe) / CACHE_FILENAME
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

    names = sorted(p.name for p in _cache_dir(pe).glob("*.pkl"))
    assert names == sorted(
        [
            f"{PROGRAM}-US-2026.pkl",
            f"{PROGRAM}-CO-2026.pkl",
            "federal-ctc-US-2026.pkl",
            f"{PROGRAM}-US-2027.pkl",
        ]
    )
    assert len(pe.built_sims) == 4


def test_corrupt_cache_file_is_recomputed_not_raised(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    cached = _cache_dir(pe) / CACHE_FILENAME
    cached.write_bytes(b"not a pickle, just noise")

    result = pe._baseline_run_cached(PROGRAM, STATE, YEAR)

    assert result["marker"] == 2, "corrupt cache did not trigger a recompute"
    assert len(pe.built_sims) == 2
    # The recompute must also repair the file rather than leave it corrupt.
    with cached.open("rb") as f:
        assert pickle.load(f)["marker"] == 2


def test_truncated_cache_file_is_recomputed(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    cached = _cache_dir(pe) / CACHE_FILENAME
    cached.write_bytes(cached.read_bytes()[:20])

    assert pe._baseline_run_cached(PROGRAM, STATE, YEAR)["marker"] == 2


def test_empty_cache_file_is_recomputed(pe):
    pe._baseline_run_cached(PROGRAM, STATE, YEAR)
    (_cache_dir(pe) / CACHE_FILENAME).write_bytes(b"")

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
    assert replacements[0][1] == _cache_dir(pe) / CACHE_FILENAME
    assert list(_cache_dir(pe).glob("*.tmp")) == []
    assert sorted(p.name for p in _cache_dir(pe).iterdir()) == [CACHE_FILENAME]


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
