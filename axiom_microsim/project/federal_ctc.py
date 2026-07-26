"""Project a :class:`TaxUnitBatch` into the inputs §24 CTC expects.

We run the **parent** §24 module, not §24(h) alone. §24(h) states only
the post-2017 substitutions — the $2,200 per-child amount, the $500
other-dependent amount, the $400k/$200k thresholds — and its outputs stop
at the maximum *before* phase-out. §24(b)(1) does the reduction, and it
lives in the parent module, which imports the (h) amounts. Running the
parent is what makes the phase-out threshold lever move the number.

Per person the statute needs to know whether they are:
  - a dependent under §152, AND
  - a qualifying child under §152(c) / §24(c) (age < 17 typically), AND
  - have a valid SSN/TIN included on the return.

Per tax unit it needs modified AGI (§24(b)(1)), filing status, and the
disallowance/short-year facts that gate the credit entirely.

ECPS doesn't carry these as stored variables. We synthesize them with a
simple within-tax-unit role classification:

  - oldest person in the tax unit → head
  - if filing_status == joint AND second-oldest person is an adult → spouse
  - everyone else → dependent
  - dependent + age < 17 → qualifying child
  - dependent + 17 ≤ age < 24 → other dependent (treated as §152 dependent)

SSN/TIN-related slots default to True (we assume valid US SSN unless ECPS
gives us reason to think otherwise; documented as a v2 gap). So do the
disallowance slots — no ECPS household is in a fraud-disallowance period,
files a short year, or received §7527A advance payments.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.ecps_loader import (
    TaxUnitBatch,
    count_persons_per_tax_unit,
    sum_person_to_tax_unit,
    tax_unit_agi,
)


ADULT_AGE = 18
QUALIFYING_CHILD_AGE = 17  # under age 17 → §24(c) qualifying child
OTHER_DEPENDENT_MAX_AGE = 24  # under 24 → other dependent (§152, child of taxpayer)

# Module id prefixes for the two RuleSpec modules in the §24 program.
CTC_PARENT = "us:statutes/26/24"
CTC_SUBSECTION_H = "us:statutes/26/24/h"

# Tax-unit facts that *person*-scoped rules read: the (h)(7) taxpayer-SSN
# conditions (evaluated inside a Person judgment) and the identification
# flag inside §24's `count_where` over qualifying children. Only these get
# replicated onto person rows — see `run.microsim._build_ctc_request_bytes`.
TAX_UNIT_INPUTS_ON_PERSONS: frozenset[str] = frozenset(
    {
        f"{CTC_SUBSECTION_H}#input.taxpayer_or_spouse_ssn_included_on_return",
        f"{CTC_SUBSECTION_H}#input.taxpayer_or_spouse_ssn_is_valid_for_subsection_h",
        f"{CTC_PARENT}#input.ctc_child_missing_identification",
    }
)


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

    # Persons the §24 rules can read, in `person_sort` order. Every §24
    # person rule is about a claimed dependent; heads and spouses carry
    # all-false dependency flags, contribute nothing to either
    # `count_where`, and cost ~15 input records each. See
    # `run.microsim._build_ctc_request_bytes`.
    person_in_scope: np.ndarray

    # Diagnostics
    qualifying_children_per_tu: np.ndarray
    other_dependents_per_tu: np.ndarray
    filing_status: np.ndarray

    # Subset of `tax_unit_inputs` that must also be asserted on each person
    # row (person-scoped rules read them). Everything else stays TaxUnit-only.
    tax_unit_inputs_on_persons: frozenset[str] = TAX_UNIT_INPUTS_ON_PERSONS


def project(batch: TaxUnitBatch, *, period_year: int = 2026) -> FedCtcProjection:
    n_tu = batch.n_tax_units
    age = batch.person_columns["age"].astype(np.int64)

    # --- Classify persons within each tax unit ------------------------------
    # We need, for each person:
    #   - their rank within their tax unit by age (descending; oldest = 0)
    # Compute via lexsort: primary key = tax_unit_index, secondary = -age.
    primary = batch.person_tax_unit_index
    secondary = -age
    order = np.lexsort((secondary, primary))  # sort persons by (tu, -age)
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
    filing_status = np.where(is_joint, 1, np.where(dependents_per_tu_raw > 0, 3, 0)).astype(
        np.int64
    )

    # Per-person role flags
    is_joint_per_person = is_joint[batch.person_tax_unit_index]
    is_head = rank_in_tu == 0
    is_spouse = is_joint_per_person & (rank_in_tu == 1) & is_adult
    is_dependent = ~is_head & ~is_spouse
    is_qualifying_child = is_dependent & (age < QUALIFYING_CHILD_AGE)
    is_other_dependent = (
        is_dependent & (age >= QUALIFYING_CHILD_AGE) & (age < OTHER_DEPENDENT_MAX_AGE)
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
    all_true_persons = np.ones(batch.n_persons, dtype=bool)
    all_false_persons = np.zeros(batch.n_persons, dtype=bool)
    all_true_tax_units = np.ones(n_tu, dtype=bool)
    all_false_tax_units = np.zeros(n_tu, dtype=bool)
    zero_tax_units = np.zeros(n_tu, dtype=np.float64)

    person_inputs = {
        # §24(h) — post-2017 amounts and the SSN requirement.
        f"{CTC_SUBSECTION_H}#input.dependent_under_section_152": dependent_152[person_sort].astype(
            bool
        ),
        f"{CTC_SUBSECTION_H}#input.qualifying_child_described_in_subsection_c": is_qualifying_child[
            person_sort
        ].astype(bool),
        f"{CTC_SUBSECTION_H}#input.qualifying_child_ssn_included_on_return": all_true_persons,
        f"{CTC_SUBSECTION_H}#input.qualifying_child_ssn_is_valid_for_subsection_h": all_true_persons,
        f"{CTC_SUBSECTION_H}#input.noncitizen_exception_to_other_dependent_credit_under_subsection_h": all_false_persons,
        # §24(c)/§152(c) — the parent module applies the age-17 ceiling itself,
        # so this is "dependent child of the taxpayer", not "under 17".
        f"{CTC_PARENT}#input.age": age[person_sort],
        f"{CTC_PARENT}#input.qualifying_child_under_section_152_c": dependent_152[
            person_sort
        ].astype(bool),
        f"{CTC_PARENT}#input.allowed_deduction_under_section_151_for_child": dependent_152[
            person_sort
        ].astype(bool),
        f"{CTC_PARENT}#input.certain_noncitizen_exception_applies": all_false_persons,
        # §24(e) identification — same assumption as the (h)(7) SSN slots.
        f"{CTC_PARENT}#input.qualifying_child_name_included_on_return": all_true_persons,
        f"{CTC_PARENT}#input.qualifying_child_tin_included_on_return": all_true_persons,
        f"{CTC_PARENT}#input.qualifying_child_tin_issued_on_or_before_return_due_date": all_true_persons,
    }

    tax_unit_inputs = {
        f"{CTC_SUBSECTION_H}#input.filing_status_is_joint_return": (filing_status == 1),
        f"{CTC_SUBSECTION_H}#input.taxpayer_or_spouse_ssn_included_on_return": all_true_tax_units,
        f"{CTC_SUBSECTION_H}#input.taxpayer_or_spouse_ssn_is_valid_for_subsection_h": all_true_tax_units,
        # §24(b)(1) modified AGI — the phase-out base. Same AGI proxy the
        # decile axis uses; §911/§931/§933 exclusions aren't in ECPS, so the
        # modification adds nothing.
        f"{CTC_PARENT}#input.adjusted_gross_income": tax_unit_agi(batch),
        f"{CTC_PARENT}#input.amount_excluded_from_gross_income_under_section_911": zero_tax_units,
        f"{CTC_PARENT}#input.amount_excluded_from_gross_income_under_section_931": zero_tax_units,
        f"{CTC_PARENT}#input.amount_excluded_from_gross_income_under_section_933": zero_tax_units,
        f"{CTC_PARENT}#input.filing_status": filing_status,
        # §24(h)(1) — every year this microsim runs begins after 2017, so the
        # (h) substitutions apply. Derived from the period, not hardcoded.
        f"{CTC_PARENT}#input.taxable_year_begins_after_2017": np.full(
            n_tu, period_year > 2017, dtype=bool
        ),
        # §24(e)/(f)/(g)/(j) gates: full 12-month year, no disallowance
        # period, no §7527A advance payments. ECPS carries none of these.
        f"{CTC_PARENT}#input.taxable_year_months": np.full(n_tu, 12, dtype=np.int64),
        f"{CTC_PARENT}#input.taxable_year_closed_by_reason_of_taxpayer_death": all_false_tax_units,
        f"{CTC_PARENT}#input.taxpayer_identification_number_issued_after_return_due_date": all_false_tax_units,
        f"{CTC_PARENT}#input.ctc_child_missing_identification": all_false_tax_units,
        f"{CTC_PARENT}#input.ctc_fraud_disallowance_period_applies": all_false_tax_units,
        f"{CTC_PARENT}#input.ctc_reckless_or_intentional_disregard_disallowance_period_applies": all_false_tax_units,
        f"{CTC_PARENT}#input.prior_deficiency_denial_without_required_eligibility_information": all_false_tax_units,
        f"{CTC_PARENT}#input.aggregate_advance_payments_under_section_7527A": zero_tax_units,
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
        person_in_scope=dependent_152[person_sort],
        qualifying_children_per_tu=qualifying_children_per_tu,
        other_dependents_per_tu=other_dependents_per_tu,
        filing_status=filing_status,
    )
