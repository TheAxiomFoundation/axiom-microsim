"""Caching, single-flight and timeout behaviour of ``POST /compare``.

The PE oracle behind ``/compare`` costs minutes per run, so the endpoint
memoises results, collapses concurrent identical requests onto one
compute, and turns a subprocess timeout into a clean 504. None of that is
exercised by running PolicyEngine — every test here stubs the subprocess.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from axiom_microsim import server


PE_PAYLOAD = {
    "pe_total": 100.0,
    "pe_n_units": 2,
    "pe_weighted_filers": 1.0,
    "pe_weighted_total": 2.0,
    "pe_avg_per_filer": 100.0,
}

REQUEST = {"program": "federal-income-tax", "state": "US", "year": 2026}


@pytest.fixture
def pe_python(monkeypatch, tmp_path):
    """Point the server at a stand-in PE interpreter that merely exists."""
    python_path = tmp_path / "python"
    python_path.write_text("")
    monkeypatch.setattr(server, "_PE_PYTHON", python_path)
    return python_path


def _stub_subprocess(monkeypatch, handler):
    """Replace the PE subprocess with ``handler``; return the recorded argv list."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return handler(cmd)

    monkeypatch.setattr(server._subprocess, "run", fake_run)
    return calls


def _ok(_cmd):
    return SimpleNamespace(returncode=0, stdout=json.dumps(PE_PAYLOAD), stderr="")


# --- result cache -----------------------------------------------------------


def test_second_identical_compare_is_served_from_cache(monkeypatch, pe_python):
    def slow_ok(cmd):
        time.sleep(0.05)
        return _ok(cmd)

    calls = _stub_subprocess(monkeypatch, slow_ok)
    client = TestClient(server.app)

    first = client.post("/compare", json=REQUEST)
    second = client.post("/compare", json=REQUEST)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1, "identical request re-ran the PE subprocess"
    assert second.json()["pe_total"] == first.json()["pe_total"] == 100.0


def test_cache_hit_reports_its_own_elapsed_not_the_original_run(monkeypatch, pe_python):
    def slow_ok(cmd):
        time.sleep(0.2)
        return _ok(cmd)

    _stub_subprocess(monkeypatch, slow_ok)
    client = TestClient(server.app)

    cold = client.post("/compare", json=REQUEST).json()["elapsed_seconds"]
    hit = client.post("/compare", json=REQUEST).json()["elapsed_seconds"]

    assert cold >= 0.2
    assert hit < cold, "cache hit replayed the cold run's elapsed_seconds"
    assert hit < 0.1


def test_compare_cache_key_separates_distinct_overrides(monkeypatch, pe_python):
    calls = _stub_subprocess(monkeypatch, _ok)
    client = TestClient(server.app)

    for value in (0.1, 0.2, 0.1):
        body = {**REQUEST, "overrides": [{"path": "gov.irs.rate", "value": value}]}
        assert client.post("/compare", json=body).status_code == 200

    assert len(calls) == 2, "distinct override values must not share a cache entry"


def test_compare_cache_is_bounded(monkeypatch, pe_python):
    _stub_subprocess(monkeypatch, _ok)
    client = TestClient(server.app)

    overflow = server._CACHE_MAX_ENTRIES + 5
    for i in range(overflow):
        body = {**REQUEST, "overrides": [{"path": "gov.irs.rate", "value": float(i)}]}
        assert client.post("/compare", json=body).status_code == 200

    assert len(server._COMPARE_CACHE) == server._CACHE_MAX_ENTRIES


# --- single flight ----------------------------------------------------------


def test_concurrent_identical_compares_compute_once(monkeypatch, pe_python):
    """A second identical request in flight must wait, not start its own PE run."""
    entered = threading.Event()
    release = threading.Event()

    def blocking_ok(cmd):
        entered.set()
        assert release.wait(timeout=10), "test never released the stub subprocess"
        return _ok(cmd)

    calls = _stub_subprocess(monkeypatch, blocking_ok)
    results: dict[int, object] = {}

    def post(tag: int) -> None:
        results[tag] = TestClient(server.app).post("/compare", json=REQUEST)

    leader = threading.Thread(target=post, args=(1,))
    leader.start()
    assert entered.wait(timeout=10), "leader never reached the PE subprocess"

    follower = threading.Thread(target=post, args=(2,))
    follower.start()

    # The follower cannot be served from the cache — nothing is cached yet —
    # so if it were not deduped it would start a second PE run. Give it room
    # to do so, then confirm it did not.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        assert len(calls) == 1, "concurrent identical request started a second PE run"
        assert len(server._COMPARE_CACHE) == 0
        time.sleep(0.02)

    release.set()
    leader.join(timeout=10)
    follower.join(timeout=10)
    assert not leader.is_alive() and not follower.is_alive()

    assert len(calls) == 1
    assert {r.status_code for r in results.values()} == {200}
    assert {r.json()["pe_total"] for r in results.values()} == {100.0}
    assert server._COMPARE_INFLIGHT == {}, "single-flight slot leaked"


# --- timeout ----------------------------------------------------------------


def test_subprocess_timeout_returns_504(monkeypatch, pe_python):
    def timeout(cmd):
        raise server._subprocess.TimeoutExpired(cmd=cmd, timeout=780)

    _stub_subprocess(monkeypatch, timeout)

    response = TestClient(server.app).post("/compare", json=REQUEST)

    assert response.status_code == 504
    assert "780s" in response.json()["detail"]
    assert server._COMPARE_CACHE == {}, "a timed-out run must not be cached"
    assert server._COMPARE_INFLIGHT == {}, "single-flight slot leaked on timeout"


def test_subprocess_timeout_does_not_block_a_later_success(monkeypatch, pe_python):
    """The retry the 504 message advises must actually be able to succeed."""
    attempts = {"n": 0}

    def flaky(cmd):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise server._subprocess.TimeoutExpired(cmd=cmd, timeout=780)
        return _ok(cmd)

    _stub_subprocess(monkeypatch, flaky)
    client = TestClient(server.app)

    assert client.post("/compare", json=REQUEST).status_code == 504
    assert client.post("/compare", json=REQUEST).status_code == 200


def test_compare_timeout_matches_the_vercel_cap():
    """The inner timeout must stay under the 800s Vercel maxDuration."""
    import inspect

    source = inspect.getsource(server._run_pe_subprocess)
    assert "timeout=780" in source


# --- baseline cache ---------------------------------------------------------


def test_cached_baseline_computes_once_per_key():
    calls: list[tuple] = []

    def compute():
        calls.append(())
        return {"run": len(calls)}

    first = server._cached_baseline("federal-ctc", "US", 2026, compute)
    second = server._cached_baseline("federal-ctc", "US", 2026, compute)

    assert first is second
    assert len(calls) == 1

    other_state = server._cached_baseline("federal-ctc", "CO", 2026, compute)
    other_year = server._cached_baseline("federal-ctc", "US", 2027, compute)
    other_program = server._cached_baseline("co-snap", "US", 2026, compute)

    assert len({id(x) for x in (first, other_state, other_year, other_program)}) == 4
    assert len(calls) == 4


def test_baseline_cache_is_bounded():
    overflow = server._CACHE_MAX_ENTRIES + 10
    for year in range(overflow):
        server._cached_baseline("federal-ctc", "US", year, lambda y=year: {"year": y})

    assert len(server._BASELINE_RESULT_CACHE) == server._CACHE_MAX_ENTRIES
    assert ("federal-ctc", "US", 0) not in server._BASELINE_RESULT_CACHE
    assert ("federal-ctc", "US", overflow - 1) in server._BASELINE_RESULT_CACHE


def test_microsim_reuses_the_cached_baseline_across_requests(monkeypatch):
    """Two /microsim posts for the same scope run the engine baseline once."""
    import numpy as np

    from axiom_microsim.run.microsim import MicrosimResult

    class FakeBatch:
        state = "US"
        n_tax_units = 4
        n_persons = 4
        tax_unit_weight = np.ones(4)
        person_tax_unit_index = np.arange(4)
        person_columns = {"employment_income_before_lsr": np.linspace(1e4, 9e4, 4)}

    batch = FakeBatch()
    runs: list[object] = []

    def fake_run_federal_income_tax(loaded, *, period_year, overrides=None):
        runs.append(overrides)
        return MicrosimResult(
            program="federal-income-tax",
            state=loaded.state,
            period_year=period_year,
            n_households=loaded.n_tax_units,
            n_persons=loaded.n_persons,
            household_weight=loaded.tax_unit_weight,
            outputs={server.TAX_OUTPUT: np.linspace(0, 3_000, 4)},
        )

    monkeypatch.setattr(server, "load_state_tax_units", lambda state: batch)
    monkeypatch.setattr(server, "run_federal_income_tax", fake_run_federal_income_tax)

    client = TestClient(server.app)
    body = {"program": "federal-income-tax", "state": "US", "year": 2026}
    assert client.post("/microsim", json=body).status_code == 200
    assert client.post("/microsim", json=body).status_code == 200

    assert runs == [None], "baseline engine run was not reused across requests"


# --- LRU primitives ---------------------------------------------------------


def test_lru_put_evicts_the_least_recently_used_entry():
    cache: OrderedDict = OrderedDict()
    for i in range(server._CACHE_MAX_ENTRIES):
        server._lru_put(cache, i, i)

    # Touching the oldest key must move it out of the eviction slot.
    assert server._lru_get(cache, 0) == 0
    server._lru_put(cache, "new", "new")

    assert len(cache) == server._CACHE_MAX_ENTRIES
    assert 0 in cache, "recently-read entry was evicted"
    assert 1 not in cache, "least-recently-used entry survived"
    assert "new" in cache


def test_lru_get_returns_none_for_a_miss():
    assert server._lru_get(OrderedDict(), "absent") is None
