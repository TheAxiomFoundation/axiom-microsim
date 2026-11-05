"""Public work admission is bounded and always releases capacity."""

from threading import BoundedSemaphore

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from axiom_microsim import server


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_busy_public_worker_refuses_before_work_and_health_still_works(monkeypatch, route):
    slot = BoundedSemaphore(1)
    monkeypatch.setattr(server, "_PUBLIC_WORK", slot)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("busy public request began work")

    monkeypatch.setattr(server, "_run_co_snap", forbidden)
    monkeypatch.setattr(server, "_compare", forbidden)
    assert slot.acquire(blocking=False)
    try:
        client = TestClient(server.app)
        response = client.post(route, json={"program": "co-snap"})
        assert response.status_code == 503
        assert "capacity" in response.json()["detail"]
        assert client.get("/health").json() == {"status": "ok"}
        assert not calls
    finally:
        slot.release()


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_public_capacity_released_after_failure(monkeypatch, route):
    slot = BoundedSemaphore(1)
    monkeypatch.setattr(server, "_PUBLIC_WORK", slot)
    calls = []

    def fails(*args, **kwargs):
        calls.append(True)
        raise HTTPException(418, "controlled work failure")

    monkeypatch.setattr(server, "_run_co_snap", fails)
    monkeypatch.setattr(server, "_compare", fails)
    client = TestClient(server.app)
    for _ in range(2):
        assert client.post(route, json={"program": "co-snap"}).status_code == 418
    assert len(calls) == 2
    assert slot.acquire(blocking=False)
    slot.release()
