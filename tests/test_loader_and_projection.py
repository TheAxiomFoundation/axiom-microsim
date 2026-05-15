"""Smoke tests for the ECPS loader and CO SNAP projection.

These don't require the dense engine — they validate the data path
the engine consumes. End-to-end engine tests live in
``test_engine_smoke.py`` and are gated on the native extension being
built.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from axiom_microsim.data.ecps_loader import load_state, sum_person_to_household
from axiom_microsim.project.co_snap import project


ECPS_PATH = Path(os.environ.get("AXIOM_ECPS_PATH", str(Path.home() / "Downloads" / "enhanced_cps_2024.h5")))
requires_ecps = pytest.mark.skipif(
    not ECPS_PATH.exists(),
    reason=f"Enhanced CPS file not present at {ECPS_PATH}",
)


@requires_ecps
def test_loader_co_subset_has_realistic_population() -> None:
    batch = load_state("CO")
    assert batch.state == "CO"
    assert batch.n_households > 100
    assert batch.n_persons > batch.n_households
    weighted_population_persons = batch.n_persons / batch.n_households * batch.household_weight.sum()
    # CO 2024 population is ~5.9M. Tolerate 50% slack since this is a tiny sample.
    assert 3_000_000 < weighted_population_persons < 9_000_000


@requires_ecps
def test_loader_offsets_partition_persons() -> None:
    batch = load_state("CO")
    proj = project(batch)
    assert proj.relation_offsets[0] == 0
    assert proj.relation_offsets[-1] == batch.n_persons
    sizes = np.diff(proj.relation_offsets)
    assert sizes.sum() == batch.n_persons
    assert (sizes > 0).all(), "every household should have at least one person"


@requires_ecps
def test_projection_household_size_matches_offset_widths() -> None:
    batch = load_state("CO")
    proj = project(batch)
    sizes_from_offsets = np.diff(proj.relation_offsets)
    np.testing.assert_array_equal(proj.household_inputs["household_size"], sizes_from_offsets)


@requires_ecps
def test_projection_inputs_have_expected_dtypes() -> None:
    batch = load_state("CO")
    proj = project(batch)
    assert proj.person_inputs["member_age"].dtype == np.int64
    assert proj.person_inputs["member_is_us_citizen"].dtype == bool
    assert proj.person_inputs["snap_member_is_elderly_or_disabled"].dtype == bool
    assert proj.household_inputs["household_size"].dtype == np.int64


def test_sum_person_to_household_basic() -> None:
    person_values = np.array([10, 20, 30, 40, 50], dtype=np.float64)
    person_household_index = np.array([0, 0, 1, 1, 2], dtype=np.int64)
    out = sum_person_to_household(person_values, person_household_index, 3)
    np.testing.assert_array_equal(out, [30, 70, 50])
