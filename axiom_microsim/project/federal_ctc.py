"""Project an :class:`TaxUnitBatch` into the inputs §24(h) CTC expects.

§24(h) needs to know, per person, whether they are:
  - a dependent under §152, AND
  - a qualifying child under §24(c) (age < 17 typically), AND
  - have a valid SSN included on the return.

ECPS doesn't carry these as stored variables. We synthesize them with a
simple within-tax-unit role classification:

  - oldest person in the tax unit → head
  - if filing_status == joint AND second-oldest person is an adult → spouse
  - everyone else → dependent
  - dependent + age < 17 → qualifying child
  - dependent + 17 ≤ age < 24 → other dependent (treated as §152 dependent)

SSN-related slots default to True (we assume valid US SSN unless ECPS
gives us reason to think otherwise; documented as a v2 gap).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.ecps_loader import (
    TaxUnitBatch,
    count_persons_per_tax_unit,
    sum_person_to_tax_unit,
)


ADULT_AGE = 18
QUALIFYING_CHILD_AGE = 17     # under age 17 → §24(c) qualifying child
OTHER_DEPENDENT_MAX_AGE = 24  # under 24 → other dependent (§152, child of taxpayer)


@dataclass
class FedCtcProjection:
    n_tax_units: int
    n_persons: int
    period_year: int
    tax_unit_weight: np.ndarray

    # Per-tax-unit + per-person engine inputs, keyed by full RuleSpec id.
    tax_unit_inputs: dict[str, np.ndarray]
    person_inputs: dict[str, np.ndarray]

    # Sort order of persons (head first, spouse, then dependents) so the
    # offsets array we hand the engine matches the per-person inputs.
    person_sort: np.ndarray
    relation_offsets: np.ndarray

    # Diagnostics
    qualifying_children_per_tu: np.ndarray
    other_dependents_per_tu: np.ndarray
    filing_status: np.ndarray


def project(batch: TaxUnitBatch, *, period_year: int = 2026) -> FedCtcProjection:
    n_tu = batch.n_tax_units
    age = batch.person_columns["age"].astype(np.int64)

    # --- Classify persons within each tax unit ------------------------------
    # We need, for each person:
    #   - their rank within their tax unit by age (descending; oldest = 0)
    # Compute via lexsort: primary key = tax_unit_index, secondary = -age.
    primary = batch.person_tax_unit_index
    secondary = -age
    order = np.lexsort((secondary, primary))    # sort persons by (tu, -age)
    sorted_tu = primary[order]
    # rank within tax unit: count of preceding persons in the same tu
    same_tu = sorted_tu[1:] == sorted_tu[:-1]
    rank_in_tu_sorted = np.zeros(batch.n_persons, dtype=np.int64)
    for i in range(1, batch.n_persons):
        rank_in_tu_sorted[i] = rank_in_tu_sorted[i - 1] + 1 if same_tu[i - 1] else 0
    # un-sort the rank back to original person order
    rank_in_tu = np.empty(batch.n_persons, dtype=np.int64)
    rank_in_tu[order] = rank_in_tu_sorted

    # Filing-status heuristic (same as the §1(j) projection): 2+ adults → joint.
    is_adult = age >= ADULT_AGE
    adults_per_tu = sum_person_to_tax_unit(
        is_adult.astype(np.float64), batch.person_tax_unit_index, n_tu
    ).astype(np.int64)
    persons_per_tu = count_persons_per_tax_unit(batch.person_tax_unit_index).astype(np.int64)
    dependents_per_tu_raw = persons_per_tu - adults_per_tu
    is_joint = adults_per_tu >= 2
    filing_status = np.where(
        is_joint, 1, np.where(dependents_per_tu_raw > 0, 3, 0)
    ).astype(np.int64)

    # Per-person role flags
    is_joint_per_person = is_joint[batch.person_tax_unit_index]
    is_head = rank_in_tu == 0
    is_spouse = is_joint_per_person & (rank_in_tu == 1) & is_adult
    is_dependent = ~is_head & ~is_spouse
    is_qualifying_child = is_dependent & (age < QUALIFYING_CHILD_AGE)
    is_other_dependent = (
        is_dependent
        & (age >= QUALIFYING_CHILD_AGE)
        & (age < OTHER_DEPENDENT_MAX_AGE)
    )

    # Engine wants `dependent_under_section_152` to be true for both
    # qualifying children AND other dependents — anyone the taxpayer can
    # claim. Treat as is_qualifying_child OR is_other_dependent.
    dependent_152 = is_qualifying_child | is_other_dependent

    # --- Build offset / sort arrays for the engine relation ------------------
    # The engine expects person inputs sorted by tax unit. We use the same
    # `order` we computed above, then build offsets from contiguous-run
    # boundaries.
    person_sort = order
    sorted_tu_idx = primary[order]
    counts = np.bincount(sorted_tu_idx, minlength=n_tu).astype(np.int64)
    offsets = np.empty(n_tu + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(counts, out=offsets[1:])

    # --- Build engine inputs --------------------------------------------------
    person_inputs = {
        "us:statutes/26/24/h#input.dependent_under_section_152":
            dependent_152[person_sort].astype(bool),
        "us:statutes/26/24/h#input.qualifying_child_described_in_subsection_c":
            is_qualifying_child[person_sort].astype(bool),
        "us:statutes/26/24/h#input.qualifying_child_ssn_included_on_return":
            np.ones(batch.n_persons, dtype=bool),
        "us:statutes/26/24/h#input.qualifying_child_ssn_is_valid_for_subsection_h":
            np.ones(batch.n_persons, dtype=bool),
        "us:statutes/26/24/h#input.noncitizen_exception_to_other_dependent_credit_under_subsection_h":
            np.zeros(batch.n_persons, dtype=bool),
    }

    tax_unit_inputs = {
        "us:statutes/26/24/h#input.filing_status_is_joint_return": (filing_status == 1),
        "us:statutes/26/24/h#input.taxpayer_or_spouse_ssn_included_on_return":
            np.ones(n_tu, dtype=bool),
        "us:statutes/26/24/h#input.taxpayer_or_spouse_ssn_is_valid_for_subsection_h":
            np.ones(n_tu, dtype=bool),
    }

    qualifying_children_per_tu = sum_person_to_tax_unit(
        is_qualifying_child.astype(np.float64), batch.person_tax_unit_index, n_tu
    ).astype(np.int64)
    other_dependents_per_tu = sum_person_to_tax_unit(
        is_other_dependent.astype(np.float64), batch.person_tax_unit_index, n_tu
    ).astype(np.int64)

    return FedCtcProjection(
        n_tax_units=n_tu,
        n_persons=batch.n_persons,
        period_year=period_year,
        tax_unit_weight=batch.tax_unit_weight,
        tax_unit_inputs=tax_unit_inputs,
        person_inputs=person_inputs,
        person_sort=person_sort,
        relation_offsets=offsets,
        qualifying_children_per_tu=qualifying_children_per_tu,
        other_dependents_per_tu=other_dependents_per_tu,
        filing_status=filing_status,
    )
