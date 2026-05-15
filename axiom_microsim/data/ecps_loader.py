"""Strictly PE-free loader for the Enhanced CPS HDF5 files.

The h5 layout (as built by `policyengine-us-data`) stores one group per
variable with a single year-keyed dataset inside, e.g.

    age/2024                  shape=(N_persons,)     float32
    person_household_id/2024  shape=(N_persons,)     int32   (each person's hh)
    household_id/2024         shape=(N_households,)  int32   (canonical hh ids)
    household_weight/2024     shape=(N_households,)  float32
    state_fips/2024           shape=(N_households,)  int32

Some variables that are semantically household-level are nevertheless
stored at the person level (e.g. ``rent``) — caller decides how to fold
them. The loader leaves those as person columns and the projection layer
aggregates.

The state-letter top-level groups in the h5 (`AK/`, `AL/`, …) are empty
placeholders; the real data is the flat per-variable groups.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np


DEFAULT_ECPS_PATH = Path(
    os.environ.get("AXIOM_ECPS_PATH", str(Path.home() / "Downloads" / "enhanced_cps_2024.h5"))
)

DEFAULT_YEAR = "2024"

# Census FIPS codes for the 50 states + DC. Hand-encoded; no PE dependency.
STATE_FIPS = {
    "AL": 1, "AK": 2, "AZ": 4, "AR": 5, "CA": 6, "CO": 8, "CT": 9, "DE": 10,
    "DC": 11, "FL": 12, "GA": 13, "HI": 15, "ID": 16, "IL": 17, "IN": 18,
    "IA": 19, "KS": 20, "KY": 21, "LA": 22, "ME": 23, "MD": 24, "MA": 25,
    "MI": 26, "MN": 27, "MS": 28, "MO": 29, "MT": 30, "NE": 31, "NV": 32,
    "NH": 33, "NJ": 34, "NM": 35, "NY": 36, "NC": 37, "ND": 38, "OH": 39,
    "OK": 40, "OR": 41, "PA": 42, "RI": 44, "SC": 45, "SD": 46, "TN": 47,
    "TX": 48, "UT": 49, "VT": 50, "VA": 51, "WA": 53, "WV": 54, "WI": 55,
    "WY": 56,
}


@dataclass
class EcpsBatch:
    """A state-filtered slice of the Enhanced CPS, ready for projection.

    Person-level arrays are length ``n_persons``; household-level arrays
    are length ``n_households``. ``person_household_index[i]`` gives the
    *position* (0..n_households-1) of the household that person i belongs
    to — already remapped from raw household_id, ready to use as the
    relation offset for the dense engine.
    """

    state: str
    year: str
    n_persons: int
    n_households: int
    person_household_index: np.ndarray         # int64 (n_persons,)
    household_weight: np.ndarray               # float64 (n_households,)
    person_columns: dict[str, np.ndarray] = field(default_factory=dict)
    household_columns: dict[str, np.ndarray] = field(default_factory=dict)


# Default columns read for CO SNAP. Programs may extend this set via
# ``load_state(person_columns=…)``. Any unknown variable raises rather
# than defaulting silently — we want loud failures over silent zeros.
DEFAULT_PERSON_COLUMNS: tuple[str, ...] = (
    "age",
    "is_disabled",
    "is_blind",
    "employment_income_before_lsr",
    "self_employment_income_before_lsr",
    "taxable_interest_income",
    "tax_exempt_interest_income",
    "qualified_dividend_income",
    "non_qualified_dividend_income",
    "taxable_pension_income",
    "tax_exempt_pension_income",
    "rental_income",
    "alimony_income",
    "tip_income",
    "miscellaneous_income",
    # Logically household-level but stored per-person in ECPS. Folded by
    # the projection layer.
    "rent",
)

DEFAULT_HOUSEHOLD_COLUMNS: tuple[str, ...] = ()


@dataclass
class TaxUnitBatch:
    """ECPS slice grouped by tax unit instead of household.

    Tax-unit weight is inherited from the household the tax unit belongs
    to (PE convention: every tax unit in a household has the household's
    weight).
    """

    state: str
    year: str
    n_persons: int
    n_tax_units: int
    person_tax_unit_index: np.ndarray   # int64 (n_persons,)
    tax_unit_weight: np.ndarray         # float64 (n_tax_units,)
    person_columns: dict[str, np.ndarray] = field(default_factory=dict)


def load_state(
    state: str,
    *,
    path: Path | str | None = None,
    year: str = DEFAULT_YEAR,
    person_columns: Iterable[str] = DEFAULT_PERSON_COLUMNS,
    household_columns: Iterable[str] = DEFAULT_HOUSEHOLD_COLUMNS,
) -> EcpsBatch:
    """Read all households in ``state`` from the Enhanced CPS file."""
    state = state.upper()
    if state not in STATE_FIPS:
        raise ValueError(f"unknown state code: {state!r}")
    fips = STATE_FIPS[state]

    h5_path = Path(path) if path else DEFAULT_ECPS_PATH
    if not h5_path.exists():
        raise FileNotFoundError(
            f"Enhanced CPS file not found at {h5_path}. Set AXIOM_ECPS_PATH or "
            f"download enhanced_cps_2024.h5 from "
            f"huggingface.co/policyengine/policyengine-us-data."
        )

    with h5py.File(h5_path, "r") as f:
        household_ids = _read(f, "household_id", year)              # (H,)
        household_state_fips = _read(f, "state_fips", year)         # (H,)
        household_weight_all = _read(f, "household_weight", year)   # (H,)
        person_hh_id = _read(f, "person_household_id", year)        # (P,)

        if not (household_ids.shape == household_state_fips.shape == household_weight_all.shape):
            raise RuntimeError(
                "ECPS shape mismatch across household-level variables — "
                "h5 file is malformed."
            )

        hh_mask = household_state_fips == fips
        n_households = int(hh_mask.sum())
        if n_households == 0:
            raise RuntimeError(f"no households in state {state} (fips {fips})")

        kept_hh_ids = household_ids[hh_mask]
        # Map raw household_id → contiguous 0..n_households-1 position.
        # Using a sentinel-filled lookup table is O(P) and avoids np.isin's hash.
        max_id = int(max(kept_hh_ids.max(), person_hh_id.max())) + 1
        hh_id_to_pos = np.full(max_id, -1, dtype=np.int64)
        hh_id_to_pos[kept_hh_ids] = np.arange(n_households)

        person_pos = np.where(hh_id_to_pos[person_hh_id] >= 0)[0]
        n_persons = person_pos.size

        person_household_index = hh_id_to_pos[person_hh_id[person_pos]]
        household_weight = household_weight_all[hh_mask].astype(np.float64)

        person_data: dict[str, np.ndarray] = {}
        for name in person_columns:
            person_data[name] = _read(f, name, year)[person_pos]

        household_data: dict[str, np.ndarray] = {}
        for name in household_columns:
            household_data[name] = _read(f, name, year)[hh_mask]

    return EcpsBatch(
        state=state,
        year=year,
        n_persons=n_persons,
        n_households=n_households,
        person_household_index=person_household_index.astype(np.int64),
        household_weight=household_weight,
        person_columns=person_data,
        household_columns=household_data,
    )


def load_state_tax_units(
    state: str,
    *,
    path: Path | str | None = None,
    year: str = DEFAULT_YEAR,
    person_columns: Iterable[str] = DEFAULT_PERSON_COLUMNS,
) -> TaxUnitBatch:
    """Read tax units for ``state`` (or ``"US"`` for nationwide).

    A tax unit is a tax-filing entity (single filer, MFJ couple, MFS
    spouse, HoH parent, etc.). One household can contain several tax
    units (e.g. unmarried partners filing separately, an adult child).
    Tax-unit weight inherits from the parent household.

    Pass ``state="US"`` to skip state filtering and load all 30k tax
    units in the file — useful for federal-tax microsimulation.
    """
    state = state.upper()
    nationwide = state in {"US", "ALL", "NATIONAL"}
    if not nationwide and state not in STATE_FIPS:
        raise ValueError(f"unknown state code: {state!r}")
    fips = None if nationwide else STATE_FIPS[state]

    h5_path = Path(path) if path else DEFAULT_ECPS_PATH
    if not h5_path.exists():
        raise FileNotFoundError(f"Enhanced CPS file not found at {h5_path}")

    with h5py.File(h5_path, "r") as f:
        household_ids = _read(f, "household_id", year)
        household_state_fips = _read(f, "state_fips", year)
        household_weight_all = _read(f, "household_weight", year)
        person_household_id = _read(f, "person_household_id", year)
        tax_unit_ids = _read(f, "tax_unit_id", year)
        person_tax_unit_id = _read(f, "person_tax_unit_id", year)

        # Filter households by state, then keep only those persons
        # (and the tax units they belong to). For nationwide, keep all.
        if fips is None:
            hh_mask = np.ones(household_ids.shape, dtype=bool)
        else:
            hh_mask = household_state_fips == fips
        kept_hh_ids = household_ids[hh_mask]
        max_hh_id = int(max(kept_hh_ids.max(), person_household_id.max())) + 1
        hh_id_to_pos = np.full(max_hh_id, -1, dtype=np.int64)
        hh_id_to_pos[kept_hh_ids] = np.arange(int(hh_mask.sum()))

        person_pos = np.where(hh_id_to_pos[person_household_id] >= 0)[0]
        n_persons = person_pos.size

        # Per-person household weight, then per-person tax-unit id.
        weights_by_household = household_weight_all[hh_mask].astype(np.float64)
        person_hh_pos = hh_id_to_pos[person_household_id[person_pos]]
        person_weight = weights_by_household[person_hh_pos]
        kept_person_tu_ids = person_tax_unit_id[person_pos]

        # Build tax-unit index over kept persons.
        unique_tu_ids = np.unique(kept_person_tu_ids)
        n_tax_units = unique_tu_ids.size
        max_tu_id = int(unique_tu_ids.max()) + 1
        tu_id_to_pos = np.full(max_tu_id, -1, dtype=np.int64)
        tu_id_to_pos[unique_tu_ids] = np.arange(n_tax_units)
        person_tu_index = tu_id_to_pos[kept_person_tu_ids].astype(np.int64)

        # Tax-unit weight: every person in a tax unit has the same hh
        # weight (PE invariant). Take the first person's weight per TU
        # via index_of_first.
        order = np.argsort(person_tu_index, kind="stable")
        sorted_tu = person_tu_index[order]
        first_in_tu = np.concatenate(([True], sorted_tu[1:] != sorted_tu[:-1]))
        tu_first_person = order[first_in_tu]
        tax_unit_weight = person_weight[tu_first_person]

        person_data: dict[str, np.ndarray] = {}
        for name in person_columns:
            person_data[name] = _read(f, name, year)[person_pos]

    return TaxUnitBatch(
        state=state,
        year=year,
        n_persons=n_persons,
        n_tax_units=n_tax_units,
        person_tax_unit_index=person_tu_index,
        tax_unit_weight=tax_unit_weight,
        person_columns=person_data,
    )


def sum_person_to_tax_unit(
    person_values: np.ndarray, person_tax_unit_index: np.ndarray, n_tax_units: int
) -> np.ndarray:
    return np.bincount(person_tax_unit_index, weights=person_values, minlength=n_tax_units)


def count_persons_per_tax_unit(person_tax_unit_index: np.ndarray) -> np.ndarray:
    return np.bincount(person_tax_unit_index)


def _read(f: h5py.File, var: str, year: str) -> np.ndarray:
    if var not in f:
        raise KeyError(f"variable {var!r} not in ECPS file")
    group = f[var]
    if year not in group:
        raise KeyError(f"variable {var!r} has no year {year!r} (have: {list(group.keys())})")
    return group[year][...]


def households_per_person(person_household_index: np.ndarray) -> np.ndarray:
    """Number of persons in each household, indexed by household position."""
    return np.bincount(person_household_index)


def sum_person_to_household(
    person_values: np.ndarray, person_household_index: np.ndarray, n_households: int
) -> np.ndarray:
    """Σ person_values within each household. Always returns float64."""
    return np.bincount(person_household_index, weights=person_values, minlength=n_households)
