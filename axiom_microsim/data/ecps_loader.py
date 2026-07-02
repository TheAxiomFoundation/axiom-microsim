"""Strictly PE-free population loader.

The **default** population source is PolicyEngine's *populace* project —
the pinned dense ``populace_us_2024.h5`` artifact resolved and verified by
:mod:`axiom_microsim.data.populace_loader`. The legacy Enhanced CPS
(``policyengine-us-data``) remains a per-run **escape hatch** via
``$AXIOM_ECPS_PATH`` (deprecated; emits a ``DeprecationWarning``).

The two sources store their columns differently, and this loader hides the
difference behind a small column-reader abstraction so the state / tax-unit
filtering logic is identical for both:

* **populace** (default) is a pandas ``HDFStore`` / PyTables file: one
  compound-dtype ``table`` dataset per entity (``person/table``,
  ``household/table``, …). A column such as ``age`` is a field of
  ``person/table``. See :class:`populace_loader.PopulaceReader`.
* **Enhanced CPS** (legacy, as built by ``policyengine-us-data``) stores
  one group per variable with a single year-keyed dataset inside, e.g.::

      age/2024                  shape=(N_persons,)     float32
      person_household_id/2024  shape=(N_persons,)     int32
      household_id/2024         shape=(N_households,)  int32
      household_weight/2024     shape=(N_households,)  float32
      state_fips/2024           shape=(N_households,)  int32

  (The state-letter top-level groups ``AK/``, ``AL/``, … are empty
  placeholders; the real data is the flat per-variable groups.)

Both sources present the join keys the same way — ``person_household_id``
values match ``household_id`` values, ``person_tax_unit_id`` matches
``tax_unit_id`` — so the ``household_id``-based remap below is unchanged.

Some variables that are semantically household-level are stored at the
person level (e.g. ``rent``) — the projection layer folds them.
"""

from __future__ import annotations

import os
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Protocol

import h5py
import numpy as np

from .populace_loader import PopulaceReader, resolve_populace_path


DEFAULT_ECPS_PATH = Path(
    os.environ.get("AXIOM_ECPS_PATH", str(Path.home() / "Downloads" / "enhanced_cps_2024.h5"))
)

# Legacy escape hatch: when set, the microsim reads this Enhanced CPS file
# instead of the pinned populace artifact (with a DeprecationWarning).
ECPS_ENV_VAR = "AXIOM_ECPS_PATH"

DEFAULT_YEAR = "2024"


# --- Population source resolution -------------------------------------------


class _ColumnReader(Protocol):
    """Reads a named 1-D column from an open population file."""

    def read(self, var: str, year: str) -> np.ndarray: ...


class _EcpsColumnReader:
    """Flat ``variable/year`` reader for the legacy Enhanced CPS layout."""

    def __init__(self, h5: h5py.File):
        self._h5 = h5

    def read(self, var: str, year: str) -> np.ndarray:
        return _read(self._h5, var, year)


class _PopulaceColumnReader:
    """Adapter presenting populace entity tables as ``variable/year`` reads."""

    def __init__(self, reader: PopulaceReader):
        self._reader = reader

    def read(self, var: str, year: str) -> np.ndarray:
        # populace is a single-period file; ``year`` is accepted for
        # interface parity but the file itself is the period.
        return self._reader.column(var)


@contextmanager
def _open_population(path: Path | str | None) -> Iterator[_ColumnReader]:
    """Open the resolved population source and yield a column reader.

    Resolution order (matches the migration plan A2):

    1. explicit ``path`` argument (either layout, sniffed);
    2. ``$AXIOM_POPULACE_US_H5`` / pinned populace download (default);
    3. ``$AXIOM_ECPS_PATH`` legacy Enhanced CPS (DeprecationWarning).
    """
    explicit = Path(path) if path else None
    ecps_override = os.environ.get(ECPS_ENV_VAR)

    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"population file not found at {explicit}.")
        with h5py.File(explicit, "r") as f:
            yield _reader_for(f)
        return

    if ecps_override:
        # Legacy path chosen explicitly by the operator.
        legacy = Path(ecps_override).expanduser()
        if not legacy.exists():
            raise FileNotFoundError(
                f"{ECPS_ENV_VAR}={ecps_override!r} does not exist. Unset it to "
                "use the pinned populace artifact (the default), or point it at "
                "a valid Enhanced CPS file."
            )
        warnings.warn(
            f"{ECPS_ENV_VAR} is set: axiom-microsim is reading the legacy "
            "Enhanced CPS (policyengine-us-data) instead of the pinned "
            "populace artifact. This path is deprecated; migrate to populace "
            "(unset AXIOM_ECPS_PATH, or set AXIOM_POPULACE_US_H5 to a local "
            "pinned copy). See axiom-rebuild-plan A2.",
            DeprecationWarning,
            stacklevel=3,
        )
        with h5py.File(legacy, "r") as f:
            yield _EcpsColumnReader(f)
        return

    # Default: pinned + sha256-verified populace artifact.
    populace_path = resolve_populace_path()
    with h5py.File(populace_path, "r") as f:
        yield _PopulaceColumnReader(PopulaceReader(f))


def _reader_for(f: h5py.File) -> _ColumnReader:
    """Sniff an explicitly-passed file's layout and return the right reader.

    populace files have entity groups (``person``, ``household``) each
    containing a ``table`` dataset; Enhanced CPS files have flat
    per-variable groups (``age``, ``household_weight``, …).
    """
    person = f.get("person")
    if isinstance(person, h5py.Group) and "table" in person:
        return _PopulaceColumnReader(PopulaceReader(f))
    return _EcpsColumnReader(f)


# Census FIPS codes for the 50 states + DC. Hand-encoded; no PE dependency.
STATE_FIPS = {
    "AL": 1,
    "AK": 2,
    "AZ": 4,
    "AR": 5,
    "CA": 6,
    "CO": 8,
    "CT": 9,
    "DE": 10,
    "DC": 11,
    "FL": 12,
    "GA": 13,
    "HI": 15,
    "ID": 16,
    "IL": 17,
    "IN": 18,
    "IA": 19,
    "KS": 20,
    "KY": 21,
    "LA": 22,
    "ME": 23,
    "MD": 24,
    "MA": 25,
    "MI": 26,
    "MN": 27,
    "MS": 28,
    "MO": 29,
    "MT": 30,
    "NE": 31,
    "NV": 32,
    "NH": 33,
    "NJ": 34,
    "NM": 35,
    "NY": 36,
    "NC": 37,
    "ND": 38,
    "OH": 39,
    "OK": 40,
    "OR": 41,
    "PA": 42,
    "RI": 44,
    "SC": 45,
    "SD": 46,
    "TN": 47,
    "TX": 48,
    "UT": 49,
    "VT": 50,
    "VA": 51,
    "WA": 53,
    "WV": 54,
    "WI": 55,
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
    person_household_index: np.ndarray  # int64 (n_persons,)
    household_weight: np.ndarray  # float64 (n_households,)
    person_columns: dict[str, np.ndarray] = field(default_factory=dict)
    household_columns: dict[str, np.ndarray] = field(default_factory=dict)


# Default columns read for CO SNAP. Programs may extend this set via
# ``load_state(person_columns=…)``. Any unknown variable raises rather
# than defaulting silently — we want loud failures over silent zeros.
DEFAULT_PERSON_COLUMNS: tuple[str, ...] = (
    "age",
    "is_disabled",
    "is_blind",
    # Earned income
    "employment_income_before_lsr",
    "self_employment_income_before_lsr",
    "tip_income",
    # Investment income
    "taxable_interest_income",
    "tax_exempt_interest_income",
    "qualified_dividend_income",
    "non_qualified_dividend_income",
    # Capital gains (long-term gets preferential §1(h) rates; short-term at ordinary)
    "long_term_capital_gains_before_response",
    "short_term_capital_gains",
    "long_term_capital_gains_on_collectibles",
    "non_sch_d_capital_gains",
    # Retirement
    "taxable_pension_income",
    "tax_exempt_pension_income",
    "taxable_ira_distributions",
    "taxable_401k_distributions",
    "social_security",
    # Other
    "rental_income",
    "farm_income",
    "estate_income",
    "alimony_income",
    # Raw concept; the taxable amount (26 USC 85) is a rule output, not
    # a population input.
    "unemployment_compensation",
    "miscellaneous_income",
    # Logically household-level but stored per-person. Folded by the
    # projection layer. Gross rent — subsidy netting is rules-layer.
    "pre_subsidy_rent",
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
    person_tax_unit_index: np.ndarray  # int64 (n_persons,)
    tax_unit_weight: np.ndarray  # float64 (n_tax_units,)
    person_columns: dict[str, np.ndarray] = field(default_factory=dict)


def load_state(
    state: str,
    *,
    path: Path | str | None = None,
    year: str = DEFAULT_YEAR,
    person_columns: Iterable[str] = DEFAULT_PERSON_COLUMNS,
    household_columns: Iterable[str] = DEFAULT_HOUSEHOLD_COLUMNS,
) -> EcpsBatch:
    """Read all households in ``state`` from the population source.

    Reads the pinned populace artifact by default; ``$AXIOM_ECPS_PATH``
    selects the legacy Enhanced CPS instead (deprecated). Pass ``path`` to
    read a specific file of either layout.
    """
    state = state.upper()
    if state not in STATE_FIPS:
        raise ValueError(f"unknown state code: {state!r}")
    fips = STATE_FIPS[state]

    with _open_population(path) as src:
        household_ids = src.read("household_id", year)  # (H,)
        household_state_fips = src.read("state_fips", year)  # (H,)
        household_weight_all = src.read("household_weight", year)  # (H,)
        person_hh_id = src.read("person_household_id", year)  # (P,)

        if not (household_ids.shape == household_state_fips.shape == household_weight_all.shape):
            raise RuntimeError(
                "shape mismatch across household-level variables — population file is malformed."
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
            person_data[name] = src.read(name, year)[person_pos]

        household_data: dict[str, np.ndarray] = {}
        for name in household_columns:
            household_data[name] = src.read(name, year)[hh_mask]

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

    Pass ``state="US"`` to skip state filtering and load every tax unit
    in the file — useful for federal-tax microsimulation.
    """
    state = state.upper()
    nationwide = state in {"US", "ALL", "NATIONAL"}
    if not nationwide and state not in STATE_FIPS:
        raise ValueError(f"unknown state code: {state!r}")
    fips = None if nationwide else STATE_FIPS[state]

    with _open_population(path) as src:
        household_ids = src.read("household_id", year)
        household_state_fips = src.read("state_fips", year)
        household_weight_all = src.read("household_weight", year)
        person_household_id = src.read("person_household_id", year)
        person_tax_unit_id = src.read("person_tax_unit_id", year)

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
            person_data[name] = src.read(name, year)[person_pos]

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
