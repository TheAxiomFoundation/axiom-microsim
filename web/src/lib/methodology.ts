/**
 * Static metadata that drives the /methodology page.
 *
 * Mirrors what the Python projection layer actually does — kept in sync
 * by hand. If you change a slot mapping in
 * `axiom_microsim/project/<program>.py`, update the matching row here.
 */

export interface SlotMapping {
  axiomSlot: string;
  axiomEntity: "Person" | "TaxUnit" | "Household";
  ecpsColumn: string | null;       // null if synthesized / heuristic
  derivation: string;              // human-readable formula
}

export interface ProgramMethodology {
  id: "co-snap" | "federal-income-tax";
  name: string;
  rootEntity: "Household" | "TaxUnit";
  period: "month" | "tax_year";
  programYaml: string;
  outputs: { axiomId: string; description: string }[];
  inputs: SlotMapping[];
  calculations: { name: string; formula: string; note?: string }[];
  limitations: { item: string; impact: string; rulesusStatus: string }[];
}


export const FEDERAL_INCOME_TAX: ProgramMethodology = {
  id: "federal-income-tax",
  name: "Federal income tax — IRC §1(j) ordinary brackets",
  rootEntity: "TaxUnit",
  period: "tax_year",
  programYaml: "rules-us/statutes/26/1/j.yaml",
  outputs: [
    {
      axiomId: "us:statutes/26/1/j#income_tax_main_rates",
      description:
        "Tax on ordinary taxable income via the 7-bracket schedule (10/12/22/24/32/35/37%). Excludes the §1(h) capital-gains preferential-rate adjustment.",
    },
    {
      axiomId: "us:statutes/26/1/j#regular_tax_before_credits",
      description:
        "Sum of ordinary-rate tax (§1(j)) and capital-gains tax (§1(h)). With our v1 zeroing of capital gains inputs, equals income_tax_main_rates.",
    },
    {
      axiomId: "us:statutes/26/1/j#ordinary_taxable_income",
      description:
        "max(0, taxable_income − capital_gains_excluded_from_taxable_income).",
    },
  ],
  inputs: [
    {
      axiomSlot: "us:statutes/26/1/j#input.taxable_income",
      axiomEntity: "TaxUnit",
      ecpsColumn: "(computed)",
      derivation:
        "max(0, AGI_proxy − standard_deduction_for_filing_status) where AGI_proxy = sum of person-level income components per tax unit.",
    },
    {
      axiomSlot:
        "us:policies/irs/rev-proc-2025-32/income-tax-brackets#input.filing_status",
      axiomEntity: "TaxUnit",
      ecpsColumn: "(heuristic)",
      derivation:
        "0 single / 1 joint / 3 HoH. Heuristic: 2+ adults in tax unit → joint; 1 adult + dependent(s) → HoH; otherwise single.",
    },
    {
      axiomSlot: "us:statutes/26/1/h#input.long_term_capital_gains",
      axiomEntity: "TaxUnit",
      ecpsColumn: null,
      derivation: "Zeroed in v1.",
    },
    {
      axiomSlot: "us:statutes/26/1/h#input.short_term_capital_gains",
      axiomEntity: "TaxUnit",
      ecpsColumn: null,
      derivation: "Zeroed in v1.",
    },
    {
      axiomSlot: "us:statutes/26/1/h#input.qualified_dividend_income",
      axiomEntity: "TaxUnit",
      ecpsColumn: null,
      derivation:
        "Zeroed in §1(h) v1 (we still include it in the AGI proxy at ordinary rates above).",
    },
    {
      axiomSlot: "us:statutes/26/1/h#input.unrecaptured_section_1250_gain",
      axiomEntity: "TaxUnit",
      ecpsColumn: null,
      derivation: "Zeroed in v1.",
    },
    {
      axiomSlot: "us:statutes/26/1/h#input.capital_gains_28_percent_rate_gain",
      axiomEntity: "TaxUnit",
      ecpsColumn: null,
      derivation: "Zeroed in v1.",
    },
  ],
  calculations: [
    {
      name: "AGI_proxy",
      formula:
        "Σ over persons in tax unit of: employment_income_before_lsr + self_employment_income_before_lsr + taxable_interest_income + qualified_dividend_income + non_qualified_dividend_income + taxable_pension_income + rental_income + alimony_income + tip_income + miscellaneous_income",
      note:
        "ECPS reports these per person. We sum to TaxUnit. Above-the-line deductions (§62 — half-SECA, QBI, tips, overtime, etc.) are NOT subtracted in v1.",
    },
    {
      name: "standard_deduction (Rev Proc 2025-32, tax year 2026)",
      formula:
        "16100 if single | 32200 if joint | 16100 if MFS | 24150 if HoH | 32200 if surviving spouse",
      note: "Hardcoded for 2026; rules-us has the parameters but we don't read them yet for the proxy.",
    },
    {
      name: "filing_status (heuristic)",
      formula:
        "1 if adults_in_tax_unit ≥ 2; else 3 if dependents > 0; else 0",
      note:
        "ECPS does not store PE-derived filing status. PE infers it from marital_unit composition + dependency rules. Our heuristic is intentionally simple.",
    },
    {
      name: "tax_unit_weight",
      formula: "household_weight of the parent household",
      note:
        "PE convention: every tax unit in a household carries that household's weight. Several tax units in one household are NOT down-weighted.",
    },
  ],
  limitations: [
    {
      item: "Capital gains (§1(h) preferential rates)",
      impact:
        "Top deciles understated because long-term gains are taxed at ordinary rates above (since we feed them into AGI) but the §1(h) preferential schedule is bypassed.",
      rulesusStatus: "§1(h) encoded; we just don't pipe inputs.",
    },
    {
      item: "Above-the-line deductions (§62, §164(f), §199A, §170(p), §213, §224, §225)",
      impact:
        "AGI overstated, taxable_income overstated, tax overstated. Likely the largest single source of upward bias.",
      rulesusStatus: "All encoded; not yet wired into a single program with §1(j).",
    },
    {
      item: "Itemized deductions (§163 mortgage interest, §170(p) charity, §213 medical, etc.)",
      impact:
        "We use the standard deduction for everyone. ~10% of filers itemize; their tax overstated.",
      rulesusStatus: "§163, §163/h/4/A, §170(p), §213 encoded.",
    },
    {
      item: "Refundable credits (§32 EITC, §24(d) refundable CTC)",
      impact:
        "Bottom deciles overstate tax — without EITC/CTC, low-income households appear to owe positive tax that they actually receive as a refund.",
      rulesusStatus: "§32 + §24/d + §24/h all encoded.",
    },
    {
      item: "Non-refundable credits (§21, §22, §25A, §25B, §26)",
      impact: "Tax mid-deciles overstated by the credit amount.",
      rulesusStatus: "All encoded.",
    },
    {
      item: "AMT (§55) and NIIT (§1411)",
      impact:
        "Top tax units understated for AMT-exposed; understated for NIIT-exposed (>$200k single / $250k joint investment income).",
      rulesusStatus: "Both encoded.",
    },
    {
      item: "Filing status accuracy",
      impact:
        "Our heuristic miscategorises some MFS filers (2/all flagged as joint), some surviving spouses (treated as joint), some HoH (1 adult + dependent rule misses qualifying-relative cases).",
      rulesusStatus: "Filing status is computed by PE; rules-us has §2 partially.",
    },
  ],
};


export const CO_SNAP: ProgramMethodology = {
  id: "co-snap",
  name: "Colorado SNAP — FY 2026",
  rootEntity: "Household",
  period: "month",
  programYaml: "rules-us-co/policies/cdhs/snap/fy-2026-benefit-calculation.yaml",
  outputs: [
    {
      axiomId: "us-co:regulations/10-ccr-2506-1/4.207.2#snap_allotment",
      description:
        "Final monthly SNAP allotment for the household, after CO-specific minimum-allotment and prorating rules.",
    },
    {
      axiomId: "us:statutes/7/2017/a#snap_regular_month_allotment",
      description: "Regular-month allotment: max(0, max_allotment − 0.30 × net_income).",
    },
    {
      axiomId:
        "us:policies/usda/snap/fy-2026-cola/maximum-allotments#snap_maximum_allotment",
      description: "Per-household-size USDA max benefit ($298 / 1-person, $546 / 2-person, …).",
    },
  ],
  inputs: [
    {
      axiomSlot: "household_size",
      axiomEntity: "Household",
      ecpsColumn: "person_household_id",
      derivation: "Count of persons per household_id, as a numpy bincount.",
    },
    {
      axiomSlot: "employee_wages_received",
      axiomEntity: "Household",
      ecpsColumn: "employment_income_before_lsr",
      derivation:
        "Σ person-level annual earnings ÷ 12 → monthly. SNAP gross-income test reads this slot.",
    },
    {
      axiomSlot: "assistance_payments",
      axiomEntity: "Household",
      ecpsColumn:
        "taxable_pension_income + qualified_dividend_income + … + alimony_income",
      derivation: "Σ unearned-income components ÷ 12 → monthly.",
    },
    {
      axiomSlot: "household_shelter_costs_incurred",
      axiomEntity: "Household",
      ecpsColumn: "rent",
      derivation: "Σ person-level rent / 12 → monthly. ECPS stores rent at person level.",
    },
    {
      axiomSlot: "member_age",
      axiomEntity: "Person",
      ecpsColumn: "age",
      derivation: "Cast to int64.",
    },
    {
      axiomSlot: "member_weekly_wages",
      axiomEntity: "Person",
      ecpsColumn:
        "(employment_income_before_lsr + self_employment_income_before_lsr) / 52",
      derivation:
        "Used for ABAWD work-requirement assessment, not for the gross-income test.",
    },
    {
      axiomSlot: "snap_member_is_elderly_or_disabled",
      axiomEntity: "Person",
      ecpsColumn: "age, is_disabled",
      derivation: "(age ≥ 60) OR is_disabled.",
    },
    {
      axiomSlot: "member_is_us_citizen",
      axiomEntity: "Person",
      ecpsColumn: null,
      derivation: "Hardcoded True. ECPS doesn't carry granular citizenship; documented v2 gap.",
    },
    {
      axiomSlot: "household_pays_electricity_utility_cost (and heat/cool)",
      axiomEntity: "Household",
      ecpsColumn: null,
      derivation: "Hardcoded True (lets the standard utility allowance apply).",
    },
    {
      axiomSlot: "(other ~190 input slots)",
      axiomEntity: "Household",
      ecpsColumn: null,
      derivation:
        "Compiled defaults from co-snap-base.json (most are 0/false — alien-status sub-flags, ABAWD sub-flags, niche resource/income categories).",
    },
  ],
  calculations: [
    {
      name: "Household monthly earnings",
      formula:
        "Σ_person ((employment_income_before_lsr + self_employment_income_before_lsr) / 12)",
    },
    {
      name: "Household monthly unearned income",
      formula:
        "Σ_person ((taxable_pension + qualified_dividend + non_qualified_dividend + taxable_interest + tax_exempt_interest + rental + alimony + miscellaneous) / 12)",
    },
    {
      name: "Annual cost",
      formula: "monthly_allotment × 12 × household_weight, summed over CO households",
      note: "Same convention USDA uses for state SNAP outlays.",
    },
  ],
  limitations: [
    {
      item: "Citizenship",
      impact:
        "Everyone defaults to US citizen → eligibility overstated for non-citizen-heavy households.",
      rulesusStatus: "Encoded (member_is_us_citizen is a slot); ECPS gap.",
    },
    {
      item: "Utility costs",
      impact:
        "All households assumed to incur heating/cooling and electricity costs → standard utility allowance applies broadly, inflating excess shelter deduction → larger allotments.",
      rulesusStatus: "Slots encoded; need real utility data.",
    },
    {
      item: "Elderly/disabled medical-deduction details",
      impact:
        "Out-of-pocket medical not pulled in → moderate underestimate of net allotment for elderly/disabled hh.",
      rulesusStatus: "Slots encoded; ECPS lacks granular medical expenses.",
    },
    {
      item: "Resource (asset) test",
      impact:
        "Resource-test inputs at default → resource-eligible households over-counted; matters where Colorado's BBCE doesn't cover them.",
      rulesusStatus: "Encoded; no asset data in ECPS.",
    },
    {
      item: "ABAWD work requirements",
      impact: "ABAWD time-limit flags default → no households marked time-limited.",
      rulesusStatus: "Encoded; ECPS lacks the right per-person flags.",
    },
  ],
};


export const ALL_PROGRAMS: ProgramMethodology[] = [
  FEDERAL_INCOME_TAX,
  CO_SNAP,
];
