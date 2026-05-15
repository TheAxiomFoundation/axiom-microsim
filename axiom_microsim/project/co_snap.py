"""Project an :class:`EcpsBatch` into the input shape CO SNAP expects.

CO SNAP's compiled program has 100+ household inputs and 97 person inputs
(see ``axiom-co-snap/engine/artifacts/co-snap-base.json``). Most are policy
edge-case flags (alien status sub-categories, ABAWD work-program sub-flags,
self-employment sub-divisions) that ECPS does not measure and that default
to false / 0 in the compiled artifact. This module sets only the inputs
that ECPS *can* populate; everything else inherits the compiled defaults.

That's intentional: an ECPS microsim is a stylised population sim, not a
caseworker eligibility determination. The right granularity here is "what
ECPS measures, projected honestly into the engine's contract" — not
"every flag the compiled program could theoretically read."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from ..data.ecps_loader import EcpsBatch, sum_person_to_household


# Default application date for v1 — matches the artifact's compiled default.
DEFAULT_APPLICATION_DATE = date(2026, 1, 1)


@dataclass
class CoSnapProjection:
    """Inputs ready for ``CompiledDenseProgram.execute``.

    ``relation_offsets`` is a length-(n_households+1) int array, persons-
    sorted-by-household. ``person_inputs`` columns are length n_persons in
    the same order.
    """

    n_households: int
    n_persons: int
    period_year: int

    household_inputs: dict[str, np.ndarray]
    relation_offsets: np.ndarray
    person_inputs: dict[str, np.ndarray]

    # Carried through for aggregation: weights and the household ordering
    # the engine outputs will use.
    household_weight: np.ndarray
    household_order: np.ndarray  # original positions in EcpsBatch


def project(batch: EcpsBatch, *, period_year: int = 2026) -> CoSnapProjection:
    """Build CO SNAP dense inputs from an ECPS batch."""
    # Sort persons by household so the dense engine's per-household slice
    # `[offsets[i]:offsets[i+1]]` lands the right people.
    sort = np.argsort(batch.person_household_index, kind="stable")
    sorted_hh_index = batch.person_household_index[sort]

    # Offsets: cumulative count per household. We need it dense over
    # [0, n_households], so households with zero persons (which shouldn't
    # happen in ECPS but be defensive) get an empty slice.
    counts = np.bincount(sorted_hh_index, minlength=batch.n_households).astype(np.int64)
    offsets = np.empty(batch.n_households + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(counts, out=offsets[1:])

    # --- Person-level projection ---
    age = batch.person_columns["age"][sort].astype(np.int64)
    is_disabled = batch.person_columns["is_disabled"][sort].astype(bool)
    employment = batch.person_columns["employment_income_before_lsr"][sort].astype(np.float64)
    self_emp = batch.person_columns["self_employment_income_before_lsr"][sort].astype(np.float64)

    # `member_weekly_wages` is a per-person work-requirement input (used to
    # judge whether someone meets the 30-hr work threshold), NOT the income
    # the gross-income test reads. The household-level monthly earnings
    # slot is `employee_wages_received`; we populate that below from the
    # per-person annual earnings.
    weekly_wages = (employment + self_emp) / 52.0

    elderly_or_disabled = (age >= 60) | is_disabled
    under_18 = age < 18

    # We honour the compiled default that everyone is a US citizen. ECPS
    # has no field that flips this honestly. Documented in DECISIONS.md
    # if/when we revisit immigration eligibility.
    is_citizen = np.ones(batch.n_persons, dtype=bool)

    person_inputs: dict[str, np.ndarray] = {
        "member_age": age,
        "member_weekly_wages": weekly_wages,
        "member_is_us_citizen": is_citizen,
        "member_is_under_age_eighteen": under_18,
        "snap_member_is_elderly_or_disabled": elderly_or_disabled,
    }

    # --- Household-level projection ---
    household_size = counts.astype(np.int64)

    # ECPS stores `rent` per person, semantically a household quantity.
    # Folding by sum across people in the household preserves the
    # convention PE itself uses when this column is treated as a
    # household total. PE's `rent` is annual; SNAP wants monthly shelter.
    annual_rent_sum = sum_person_to_household(
        batch.person_columns["rent"], batch.person_household_index, batch.n_households
    )
    monthly_shelter = (annual_rent_sum / 12.0).round().astype(np.int64)

    # Household monthly earnings: sum person-level annual earnings / 12.
    # `employee_wages_received` is the slot CO SNAP's gross-income test
    # reads. PE earnings columns are annual; SNAP is monthly.
    annual_earnings = sum_person_to_household(
        batch.person_columns["employment_income_before_lsr"],
        batch.person_household_index,
        batch.n_households,
    ) + sum_person_to_household(
        batch.person_columns["self_employment_income_before_lsr"],
        batch.person_household_index,
        batch.n_households,
    )
    monthly_earnings = (annual_earnings / 12.0).round().astype(np.int64)

    # Household monthly unearned income → `assistance_payments` slot.
    # Pensions, interest, dividends, rental, alimony — all PE annual.
    unearned_columns = (
        "taxable_pension_income",
        "tax_exempt_pension_income",
        "taxable_interest_income",
        "tax_exempt_interest_income",
        "qualified_dividend_income",
        "non_qualified_dividend_income",
        "rental_income",
        "alimony_income",
        "miscellaneous_income",
    )
    annual_unearned = np.zeros(batch.n_households, dtype=np.float64)
    for col in unearned_columns:
        if col in batch.person_columns:
            annual_unearned += sum_person_to_household(
                batch.person_columns[col],
                batch.person_household_index,
                batch.n_households,
            )
    monthly_unearned = (annual_unearned / 12.0).round().astype(np.int64)

    # Utility flags: until we encode utility-cost data per household we
    # take the conservative side that lets the standard utility allowance
    # (SUA) apply. Flagged in DECISIONS.md as a v2 refinement.
    has_heating_cooling = np.ones(batch.n_households, dtype=bool)
    pays_electricity = np.ones(batch.n_households, dtype=bool)

    application_date = np.array(
        [DEFAULT_APPLICATION_DATE] * batch.n_households, dtype="datetime64[D]"
    )

    household_inputs: dict[str, np.ndarray] = {
        "household_size": household_size,
        "household_shelter_costs_incurred": monthly_shelter,
        "household_incurred_or_anticipated_heating_or_cooling_costs_separate_from_rent_or_mortgage": has_heating_cooling,
        "household_pays_electricity_utility_cost": pays_electricity,
        "application_date": application_date,
        "employee_wages_received": monthly_earnings,
        "assistance_payments": monthly_unearned,
    }

    return CoSnapProjection(
        n_households=batch.n_households,
        n_persons=batch.n_persons,
        period_year=period_year,
        household_inputs=household_inputs,
        relation_offsets=offsets,
        person_inputs=person_inputs,
        household_weight=batch.household_weight,
        household_order=np.arange(batch.n_households),
    )
