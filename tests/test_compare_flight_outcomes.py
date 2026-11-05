"""One flight shares its outcome, with a total monotonic budget for waiters."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, given, settings, strategies as st

from axiom_microsim import server


PAYLOAD = dict(
    pe_total=100, pe_n_units=2, pe_weighted_filers=1, pe_weighted_total=2, pe_avg_per_filer=100
)


@pytest.fixture
def pe_python(monkeypatch, tmp_path):
    path = tmp_path / "python"
    path.touch()
    monkeypatch.setattr(server, "_PE_PYTHON", path)


def _ok():
    return SimpleNamespace(returncode=0, stdout=json.dumps(PAYLOAD), stderr="")


def _flight(monkeypatch, first_compute, *, after_wait=lambda: None):
    entered, release, waiting = (threading.Event() for _ in range(3))
    calls, outcomes = [], {}

    class ObservedEvent:
        def __init__(self):
            self.event = threading.Event()

        def wait(self, timeout):
            waiting.set()
            result = self.event.wait(timeout)
            after_wait()
            return result

        def set(self):
            self.event.set()

    monkeypatch.setattr(server, "_threading", SimpleNamespace(Event=ObservedEvent))

    def run(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5), "leader was never released"
            return first_compute()
        return _ok()

    monkeypatch.setattr(server._subprocess, "run", run)
    req = server.CompareRequest(program="federal-ctc")

    def request(name):
        try:
            outcomes[name] = server.compare(req)
        except Exception as exc:
            outcomes[name] = exc

    leader = threading.Thread(target=request, args=("leader",))
    waiter = threading.Thread(target=request, args=("waiter",))
    leader.start()
    try:
        assert entered.wait(5)
        waiter.start()
        assert waiting.wait(5), "waiter never joined the flight"
    finally:
        release.set()
        leader.join(5)
        if waiter.ident is not None:
            waiter.join(5)
    assert not leader.is_alive() and not waiter.is_alive()
    return calls, outcomes, req


@pytest.mark.parametrize("failure", ["timeout", "exit", "non_json", "unexpected", "http"])
def test_waiters_share_leader_failure_and_fresh_request_retries(monkeypatch, pe_python, failure):
    def fail():
        if failure == "timeout":
            raise server._subprocess.TimeoutExpired("pe", 780)
        if failure == "exit":
            return SimpleNamespace(returncode=1, stdout="", stderr="broken compute")
        if failure == "non_json":
            return SimpleNamespace(returncode=0, stdout="broken JSON", stderr="")
        if failure == "http":
            raise HTTPException(429, {"reason": "busy"})
        raise RuntimeError("unexpected compute failure")

    calls, outcomes, req = _flight(monkeypatch, fail)
    assert len(calls) == 1, "a waiter retried its leader's failed computation"
    leader, waiter = outcomes["leader"], outcomes["waiter"]
    assert isinstance(leader, HTTPException)
    assert isinstance(waiter, HTTPException)
    assert (waiter.status_code, waiter.detail) == (leader.status_code, leader.detail)
    if failure == "unexpected":
        assert (leader.status_code, leader.detail) == (500, "unexpected compute failure")
    assert not server._COMPARE_CACHE, "a failed outcome entered the result cache"
    assert not server._COMPARE_INFLIGHT, "a failed flight leaked its slot"
    assert server.compare(req).pe_total == 100
    assert len(calls) == 2, "a fresh request must retry after a failure"
    assert not server._COMPARE_INFLIGHT


def test_waiter_keeps_leader_success_even_if_result_is_evicted(monkeypatch, pe_python):
    def evict():
        with server._COMPARE_CACHE_LOCK:
            server._COMPARE_CACHE.clear()

    calls, outcomes, _ = _flight(monkeypatch, _ok, after_wait=evict)
    assert len(calls) == 1, "a waiter lost the leader's success after cache eviction"
    assert outcomes["leader"].pe_total == outcomes["waiter"].pe_total == 100
    assert not server._COMPARE_INFLIGHT


@pytest.mark.parametrize("signalled", [False, True])
@pytest.mark.parametrize("request_overhead", [20, 800])
def test_waiter_deadline_is_total_and_monotonic(
    monkeypatch, pe_python, signalled, request_overhead
):
    """Injected time advances past budget even when wait returns an event."""
    budget = getattr(server, "_COMPARE_WAITER_TIMEOUT_SECONDS", 780)
    assert 0 < budget <= 780
    calls = []
    clock = SimpleNamespace(now=10.0)
    entered, release = threading.Event(), threading.Event()
    outcomes = {}
    main_thread = threading.current_thread()

    class ExpiringEvent:
        def wait(self, timeout):
            calls.append(timeout)
            assert timeout <= 780, "wait exceeds the subprocess budget"
            assert timeout == budget - request_overhead
            clock.now += timeout + 0.01
            # The old loop otherwise waits forever; stop with a visible failure.
            assert len(calls) == 1, "waiter restarted its budget after an event wait"
            return signalled

        def set(self):
            pass

    def wall_time():
        # Work before joining the flight consumes this request's budget too.
        if threading.current_thread() is main_thread:
            clock.now += request_overhead
        return -clock.now * 100

    monkeypatch.setattr(server, "_threading", SimpleNamespace(Event=ExpiringEvent))
    monkeypatch.setattr(
        server, "_time", SimpleNamespace(monotonic=lambda: clock.now, time=wall_time)
    )

    def compute(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _ok()

    monkeypatch.setattr(server._subprocess, "run", compute)
    req = server.CompareRequest(program="federal-ctc")

    def leader():
        try:
            outcomes["leader"] = server.compare(req)
        except Exception as exc:
            outcomes["leader"] = exc

    thread = threading.Thread(target=leader)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(HTTPException) as exc:
            server.compare(req)
        assert exc.value.status_code == 504
        assert "retry shortly" in exc.value.detail
        assert len(calls) == (1 if request_overhead < budget else 0)
        assert server._COMPARE_INFLIGHT, "waiter cleared the active leader's slot"
        assert not server._COMPARE_CACHE
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not server._COMPARE_INFLIGHT


def test_old_waiter_keeps_failure_while_fresh_flight_succeeds(monkeypatch, pe_python):
    """A new flight's cache entry cannot replace an old waiter's outcome."""
    entered, release_leader = threading.Event(), threading.Event()
    waiting, signalled, release_waiter = (threading.Event() for _ in range(3))
    calls, outcomes = [], {}

    class HeldWaiterEvent:
        def __init__(self):
            self.event = threading.Event()

        def wait(self, timeout):
            waiting.set()
            result = self.event.wait(timeout)
            if result:
                signalled.set()
                assert release_waiter.wait(5), "old waiter was never released"
            return result

        def set(self):
            self.event.set()

    monkeypatch.setattr(server, "_threading", SimpleNamespace(Event=HeldWaiterEvent))

    def compute(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release_leader.wait(5), "first leader was never released"
            raise HTTPException(503, "original flight failed")
        return _ok()

    monkeypatch.setattr(server._subprocess, "run", compute)
    req = server.CompareRequest(program="federal-ctc")

    def request(name):
        try:
            outcomes[name] = server.compare(req)
        except Exception as exc:
            outcomes[name] = exc

    leader = threading.Thread(target=request, args=("leader",))
    waiter = threading.Thread(target=request, args=("waiter",))
    leader.start()
    try:
        assert entered.wait(5)
        waiter.start()
        assert waiting.wait(5), "waiter never joined the first flight"
        release_leader.set()
        assert signalled.wait(5), "first flight never signalled its waiter"
        leader.join(5)
        assert not leader.is_alive()
        assert not server._COMPARE_INFLIGHT
        assert not server._COMPARE_CACHE, "the first flight cached its failure"

        fresh = server.compare(req)
        assert fresh.pe_total == 100
        assert len(calls) == 2
        assert not server._COMPARE_INFLIGHT
        assert waiter.is_alive(), "old waiter was released before the fresh flight finished"
    finally:
        release_leader.set()
        release_waiter.set()
        leader.join(5)
        if waiter.ident is not None:
            waiter.join(5)

    assert not leader.is_alive() and not waiter.is_alive()
    for name in ("leader", "waiter"):
        outcome = outcomes[name]
        assert isinstance(outcome, HTTPException)
        assert (outcome.status_code, outcome.detail) == (503, "original flight failed")
    assert len(calls) == 2, "the old waiter started a third computation"
    assert not server._COMPARE_INFLIGHT


@settings(
    max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(status=st.integers(400, 599), detail=st.text(max_size=40))
def test_any_http_failure_is_shared_without_caching(monkeypatch, pe_python, status, detail):
    # Each generated example starts with a cold result cache, including when
    # probing an implementation that incorrectly retries and caches success.
    with server._COMPARE_CACHE_LOCK:
        server._COMPARE_CACHE.clear()
    assert not server._COMPARE_INFLIGHT

    def fail():
        raise HTTPException(status, detail)

    calls, outcomes, _ = _flight(monkeypatch, fail)
    assert len(calls) == 1
    assert all(isinstance(outcome, HTTPException) for outcome in outcomes.values())
    assert {(outcome.status_code, outcome.detail) for outcome in outcomes.values()} == {
        (status, detail)
    }
    assert not server._COMPARE_CACHE
    assert not server._COMPARE_INFLIGHT
