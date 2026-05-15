"""Project an :class:`TaxUnitBatch` into the inputs §1(j) expects.

§1(j) is small: it needs ``taxable_income``, ``filing_status``, and a
handful of capital-gains slots from §1(h). For v1 we zero out the
capital-gains inputs (most ECPS tax units have negligible cap gains
exposure; the §1(h) preferential-rate machinery is correctness-critical
for high-net-worth tax units but not for a first reform demo).

``taxable_income`` is computed in Python here as a defensible proxy:
sum of person-level income components within the tax unit, minus the
2026 standard deduction for the tax unit's filing status. We do NOT yet
apply above-the-line deductions or itemized deductions — both are
encoded in rules-us but require many more inputs we don't yet pipe.
That gap is documented in DECISIONS.md (see D7 once added).

``filing_status`` follows §1401's convention: 0 single/other, 1 joint,
2 married-filing-separately, 3 head-of-household, 4 surviving spouse.
We use a simple heuristic: 2+ adults in the tax unit → joint;
1 adult + dependents → HoH; otherwise single. ECPS doesn't carry the
PE-derived filing status as a stored variable, so this is the honest
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.ecps_loader import (
    TaxUnitBatch,
    count_persons_per_tax_unit,
    sum_person_to_tax_unit,
)


# Rev Proc 2025-32 standard deduction amounts for tax year 2026.
# Source of truth lives in rules-us; we duplicate here only to compute
# the taxable_income proxy. If/when the deduction chain is wired the
# proxy goes away and we feed AGI instead.
STD_DEDUCTION_2026 = {
    0: 16_100,   # single
    1: 32_200,   # joint
    2: 16_100,   # MFS
    3: 24_150,   # HoH
    4: 32_200,   # surviving spouse
}

ADULT_AGE = 18

INCOME_COLUMNS: tuple[str, ...] = (
    "employment_income_before_lsr",
    "self_employment_income_before_lsr",
    "taxable_interest_income",
    "qualified_dividend_income",
    "non_qualified_dividend_income",
    "taxable_pension_income",
    "rental_income",
    "alimony_income",
    "tip_income",
    "miscellaneous_income",
)


@dataclass
class FedIncomeTaxProjection:
    n_tax_units: int
    period_year: int
    tax_unit_weight: np.ndarray

    # Per-tax-unit inputs. Names are bare slot names; the runner adds the
    # absolute RuleSpec-id prefix.
    inputs: dict[str, np.ndarray]

    # Diagnostics carried for the aggregator:
    agi_proxy: np.ndarray
    standard_deduction: np.ndarray


def project(batch: TaxUnitBatch, *, period_year: int = 2026) -> FedIncomeTaxProjection:
    n_tu = batch.n_tax_units

    # AGI proxy: sum income components per tax unit.
    agi = np.zeros(n_tu, dtype=np.float64)
    for col in INCOME_COLUMNS:
        if col in batch.person_columns:
            agi += sum_person_to_tax_unit(
                batch.person_columns[col], batch.person_tax_unit_index, n_tu
            )

    # Filing status heuristic: count adults vs total persons per TU.
    age = batch.person_columns["age"]
    is_adult = (age >= ADULT_AGE).astype(np.float64)
    adults_per_tu = sum_person_to_tax_unit(is_adult, batch.person_tax_unit_index, n_tu).astype(np.int64)
    persons_per_tu = count_persons_per_tax_unit(batch.person_tax_unit_index).astype(np.int64)
    dependents_per_tu = persons_per_tu - adults_per_tu

    filing_status = np.where(
        adults_per_tu >= 2, 1,                         # joint
        np.where(dependents_per_tu > 0, 3, 0),         # HoH else single
    ).astype(np.int64)

    # Standard deduction by filing status.
    std_ded = np.zeros(n_tu, dtype=np.float64)
    for code, amount in STD_DEDUCTION_2026.items():
        std_ded[filing_status == code] = amount

    taxable_income = np.maximum(0.0, agi - std_ded)

    inputs: dict[str, np.ndarray] = {
        # §1(j) input
        "us:statutes/26/1/j#input.taxable_income": taxable_income.round().astype(np.int64),
        # Brackets policy input
        "us:policies/irs/rev-proc-2025-32/income-tax-brackets#input.filing_status": filing_status,
        # §1(h) inputs — zeroed for v1 (capital-gains slice not yet projected)
        "us:statutes/26/1/h#input.long_term_capital_gains": np.zeros(n_tu, dtype=np.int64),
        "us:statutes/26/1/h#input.short_term_capital_gains": np.zeros(n_tu, dtype=np.int64),
        "us:statutes/26/1/h#input.qualified_dividend_income": np.zeros(n_tu, dtype=np.int64),
        "us:statutes/26/1/h#input.unrecaptured_section_1250_gain": np.zeros(n_tu, dtype=np.int64),
        "us:statutes/26/1/h#input.capital_gains_28_percent_rate_gain": np.zeros(n_tu, dtype=np.int64),
    }

    return FedIncomeTaxProjection(
        n_tax_units=n_tu,
        period_year=period_year,
        tax_unit_weight=batch.tax_unit_weight,
        inputs=inputs,
        agi_proxy=agi,
        standard_deduction=std_ded,
    )
