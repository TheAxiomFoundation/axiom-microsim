"""Shared fixtures.

``axiom_microsim.server`` keeps module-level caches — PE ``/compare``
results and engine baselines — that deliberately outlive a request so a
warm Modal container doesn't recompute. In a test process they also
outlive a *test*: posting to ``/microsim`` leaves a fake baseline in
``_BASELINE_RESULT_CACHE`` under ``("federal-income-tax", "US", 2026)``,
and posting to ``/compare`` leaves a fake response in ``_COMPARE_CACHE``,
so a later test asserting on a real computation would silently read the
earlier test's fixture instead. Reset them around every test so the suite
can't depend on ordering.
"""

from __future__ import annotations

import pytest

from axiom_microsim import server


def _reset_server_caches() -> None:
    with server._COMPARE_CACHE_LOCK:
        server._COMPARE_CACHE.clear()
        server._COMPARE_INFLIGHT.clear()
    with server._BASELINE_RESULT_LOCK:
        server._BASELINE_RESULT_CACHE.clear()


@pytest.fixture(autouse=True)
def reset_server_caches():
    """Clear the server's process-wide caches before and after every test."""
    _reset_server_caches()
    yield
    _reset_server_caches()
