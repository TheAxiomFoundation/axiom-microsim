"""The ``state`` scope is an allowlist, not free-form text.

``state`` is forwarded to the PE subprocess as ``--state`` and
``scripts/compute_pe_one.py`` interpolates it into a cache filename it then
``pickle.load``s. A scope carrying ``/`` or ``..`` would escape the cache
directory and let the caller choose which pickle the server deserialises,
on an endpoint that is public and CORS ``*``. These tests pin both the API
boundary and the script's own defence.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from axiom_microsim import server


ROOT = Path(__file__).resolve().parents[1]
PE_SCRIPT = ROOT / "scripts" / "compute_pe_one.py"

# Path traversal, absolute paths, NUL/newline injection, and plain
# nonsense — none of these are a state.
REJECTED_SCOPES = [
    "x/../../../tmp/evil",
    "../../../etc/passwd",
    "US/../../evil",
    "..",
    "/etc/passwd",
    "CO/",
    "C:\\evil",
    "US\x00",
    "US\nCO",
    "ZZ",
    "",
    "C",
    "COO",
    "co-snap",
    "*",
]

PE_PAYLOAD = {
    "pe_total": 1.0,
    "pe_n_units": 1,
    "pe_weighted_filers": 1.0,
    "pe_weighted_total": 1.0,
    "pe_avg_per_filer": 1.0,
}


@pytest.fixture
def pe_python(monkeypatch, tmp_path):
    python_path = tmp_path / "python"
    python_path.write_text("")
    monkeypatch.setattr(server, "_PE_PYTHON", python_path)
    return python_path


@pytest.fixture
def forbid_subprocess(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("PE subprocess ran for a rejected state scope")

    monkeypatch.setattr(server._subprocess, "run", fail)


# --- API boundary -----------------------------------------------------------


@pytest.mark.parametrize("scope", REJECTED_SCOPES)
def test_compare_rejects_scope_with_422(scope, pe_python, forbid_subprocess):
    response = TestClient(server.app).post(
        "/compare", json={"program": "federal-income-tax", "state": scope, "year": 2026}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("scope", REJECTED_SCOPES)
def test_microsim_rejects_scope_with_422(scope, monkeypatch):
    def fail(state):
        raise AssertionError("loader reached for a rejected state scope")

    monkeypatch.setattr(server, "load_state", fail)
    monkeypatch.setattr(server, "load_state_tax_units", fail)

    response = TestClient(server.app).post(
        "/microsim", json={"program": "co-snap", "state": scope, "year": 2026}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("scope", ["x/../../../tmp/evil", "ZZ", ""])
def test_ecps_stats_rejects_scope_with_422(scope, monkeypatch):
    def fail(state):
        raise AssertionError("loader reached for a rejected state scope")

    monkeypatch.setattr(server, "load_state", fail)
    monkeypatch.setattr(server, "load_state_tax_units", fail)

    response = TestClient(server.app).get(
        "/ecps-stats", params={"program": "co-snap", "state": scope}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("sent", "normalised"),
    [("US", "US"), ("us", "US"), ("CO", "CO"), ("co", "CO"), (" ca ", "CA"), ("DC", "DC")],
)
def test_compare_accepts_and_normalises_valid_scopes(sent, normalised, monkeypatch, pe_python):
    seen: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout=json.dumps(PE_PAYLOAD), stderr="")

    monkeypatch.setattr(server._subprocess, "run", fake_run)

    response = TestClient(server.app).post(
        "/compare", json={"program": "federal-income-tax", "state": sent, "year": 2026}
    )

    assert response.status_code == 200
    assert response.json()["state"] == normalised
    argv = seen[0]
    assert argv[argv.index("--state") + 1] == normalised


def test_allowlist_is_the_50_states_plus_dc_plus_nationwide():
    assert len(server.VALID_STATE_SCOPES) == 52
    assert {"US", "CA", "CO", "DC", "WY"} <= server.VALID_STATE_SCOPES
    assert "PR" not in server.VALID_STATE_SCOPES


def test_validator_message_names_the_offending_value():
    with pytest.raises(ValueError, match="unknown state scope"):
        server._validate_state_scope("x/../../../tmp/evil")


# --- compute_pe_one.py standalone defence -----------------------------------


def _load_pe_script():
    spec = importlib.util.spec_from_file_location("compute_pe_one", PE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The script cannot import the server's `STATE_FIPS` allowlist — it runs in
# PolicyEngine's own venv — so its check is a path-*shape* guard
# (`US` or two uppercase letters). That is enough to keep the value inside
# the cache directory; the semantic allowlist (is `ZZ` a real state?) is the
# API's job, which is why `ZZ` is absent below but rejected above.
SHAPE_REJECTED_SCOPES = [s for s in REJECTED_SCOPES if s != "ZZ"]


@pytest.mark.parametrize("scope", SHAPE_REJECTED_SCOPES)
def test_compute_pe_one_cache_path_rejects_scope(scope, monkeypatch, tmp_path):
    pe = _load_pe_script()
    monkeypatch.setattr(pe, "_CACHE_DIR", tmp_path)

    with pytest.raises(ValueError, match="invalid state scope"):
        pe._cache_path("federal-income-tax", scope, 2026)


# The subset of the corpus that genuinely walks out of the cache directory
# once interpolated into the filename. Pinned separately so the corpus can't
# quietly decay into a list of merely-malformed strings that prove nothing.
ESCAPING_SCOPES = [
    "x/../../../tmp/evil",
    "../../../etc/passwd",
    "US/../../evil",
]


@pytest.mark.parametrize("scope", ESCAPING_SCOPES)
def test_escaping_scopes_would_leave_the_cache_dir_unguarded(scope, tmp_path):
    """The vulnerability, stated directly: without the guard, where does it write?"""
    cache_dir = tmp_path / "cache"

    unguarded = (cache_dir / f"federal-income-tax-{scope}-2026.pkl").resolve()

    assert not unguarded.is_relative_to(cache_dir.resolve())


def test_compute_pe_one_rejects_scope_before_touching_the_filesystem(monkeypatch, tmp_path):
    pe = _load_pe_script()
    monkeypatch.setattr(pe, "_CACHE_DIR", tmp_path / "cache")

    def boom(*args, **kwargs):
        raise AssertionError("built a sim for a rejected state scope")

    monkeypatch.setattr(pe, "_build_sim", boom)
    monkeypatch.setattr(pe, "_run_program", boom)

    with pytest.raises(ValueError, match="invalid state scope"):
        pe._baseline_run_cached("federal-income-tax", "x/../../../tmp/evil", 2026)

    assert not (tmp_path / "cache").exists()


def test_compute_pe_one_rejects_unknown_program(monkeypatch, tmp_path):
    pe = _load_pe_script()
    monkeypatch.setattr(pe, "_CACHE_DIR", tmp_path)

    with pytest.raises(ValueError, match="unknown program"):
        pe._cache_path("../../evil", "US", 2026)


@pytest.mark.parametrize("scope", ["US", "CO", "DC"])
def test_compute_pe_one_cache_path_stays_inside_the_cache_dir(scope, monkeypatch, tmp_path):
    pe = _load_pe_script()
    monkeypatch.setattr(pe, "_CACHE_DIR", tmp_path)

    path = pe._cache_path("federal-income-tax", scope, 2026)

    assert path.parent == tmp_path
    assert path.name == f"federal-income-tax-{scope}-2026.pkl"


def test_compute_pe_one_year_cannot_inject_a_path(monkeypatch, tmp_path):
    """``year`` reaches the same filename; it is coerced to an int, not formatted raw."""
    pe = _load_pe_script()
    monkeypatch.setattr(pe, "_CACHE_DIR", tmp_path)

    with pytest.raises(ValueError):
        pe._cache_path("federal-income-tax", "US", "../../evil")
