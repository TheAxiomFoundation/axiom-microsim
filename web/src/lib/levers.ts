/**
 * Reform levers, organised by program.
 *
 * Each program declares its own slider catalogue. The page swaps the
 * lever list when the user toggles the program, and each lever's
 * `build` returns the engine `Override` for the patched RuleSpec.
 */
import type { Override } from "./types";

export type ProgramId = "co-snap" | "federal-income-tax" | "federal-ctc";

/** Sliders come in two flavours.
 *
 * `kind: "scale"` — a multiplier, displayed as a percent. Native shape
 *   for tables (scale every bracket rate, scale every shelter-cost row).
 *
 * `kind: "amount"` — an absolute dollar value, displayed as currency.
 *   Native shape for single parameters where humans think in dollars
 *   ("change CTC from $2,200 to $3,000 per child").
 */
export interface PeOverride {
  /** Dotted PE parameter path, e.g. "gov.irs.credits.ctc.amount.base". */
  path: string;
  /** Value to set; PE accepts numbers for scalar params. */
  value: number;
}

export interface Lever {
  id: string;
  label: string;
  description: string;
  baseline_label: string;
  kind: "scale" | "amount";
  /** Baseline value — for `scale` always 1; for `amount` the current dollar amount. */
  baseline: number;
  /** Min slider value in its native units (multiplier or dollars). */
  min: number;
  /** Max slider value in its native units. */
  max: number;
  step: number;
  /** Currency symbol prefix for amount levers (default "$"). */
  unit?: string;
  build: (value: number) => Override[];
  /** Optional translation to a PolicyEngine parameter override; lets the
   *  same reform run on the PE side for side-by-side comparison. */
  peBuild?: (value: number) => PeOverride[];
}

export interface Program {
  id: ProgramId;
  name: string;
  short: string;
  blurb: string;
  default_state: string;
  state_choices: string[];
  levers: Lever[];
  /** Friendly name for the headline output (column 1 of the stat row). */
  headline_label: string;
  /** "winners under reform" semantics — for SNAP this is "got more benefit",
   *  for tax this is "paid less tax". */
  winners_label: string;
  losers_label: string;
}

// --- Program: Federal CTC §24(h) ---------------------------------------------

const CTC_FILE = "statutes/26/24/h.yaml";

const FEDERAL_CTC: Program = {
  id: "federal-ctc",
  name: "Federal Child Tax Credit — IRC §24(h)",
  short: "Federal CTC",
  blurb:
    "Child Tax Credit before phase-out under §24(h): qualifying children × $2,200 + other dependents × $500. Computed for every tax unit in the Enhanced CPS.",
  default_state: "US",
  state_choices: ["US", "CA", "TX", "NY", "FL", "CO", "WA", "MA", "IL"],
  headline_label: "Annual CTC cost",
  winners_label: "Receive more",
  losers_label: "Receive less",
  levers: [
    {
      id: "ctc_child_amount",
      label: "Per qualifying-child amount",
      description: "Dollar credit per qualifying child under §24(h)(2).",
      baseline_label: "$2,200 (current law)",
      kind: "amount",
      baseline: 2200,
      min: 0,
      max: 6000,
      step: 100,
      build: (v) => [
        {
          repo: "rules-us",
          file_relative: CTC_FILE,
          parameter: "ctc_child_amount_under_subsection_h",
          patch_kind: "set_formula",
          formula: String(Math.round(v)),
        },
      ],
      peBuild: (v) => [
        { path: "gov.irs.credits.ctc.amount.base[0].amount", value: Math.round(v) },
      ],
    },
    {
      id: "ctc_other_dependent_amount",
      label: "Per other-dependent amount",
      description: "Dollar credit per other dependent under §24(h)(4)(A).",
      baseline_label: "$500 (current law)",
      kind: "amount",
      baseline: 500,
      min: 0,
      max: 2500,
      step: 50,
      build: (v) => [
        {
          repo: "rules-us",
          file_relative: CTC_FILE,
          parameter: "ctc_other_dependent_amount_under_subsection_h",
          patch_kind: "set_formula",
          formula: String(Math.round(v)),
        },
      ],
      peBuild: (v) => [{ path: "gov.irs.credits.ctc.amount.adult_dependent", value: Math.round(v) }],
    },
    {
      id: "ctc_joint_phase_out",
      label: "Joint phase-out threshold",
      description:
        "Where the per-credit phase-out begins for joint filers under §24(h)(3).",
      baseline_label: "$400,000 (current law)",
      kind: "amount",
      baseline: 400_000,
      min: 50_000,
      max: 1_000_000,
      step: 25_000,
      build: (v) => [
        {
          repo: "rules-us",
          file_relative: CTC_FILE,
          parameter: "ctc_joint_phase_out_threshold_under_subsection_h",
          patch_kind: "set_formula",
          formula: String(Math.round(v)),
        },
      ],
      peBuild: (v) => [
        { path: "gov.irs.credits.ctc.phase_out.threshold.JOINT", value: Math.round(v) },
      ],
    },
  ],
};

// --- Program: Colorado SNAP --------------------------------------------------

const SNAP_MAX_ALLOTMENT =
  "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml";
const SNAP_DEDUCTIONS = "policies/usda/snap/fy-2026-cola/deductions.yaml";

const COLORADO_SNAP: Program = {
  id: "co-snap",
  name: "Colorado SNAP",
  short: "CO SNAP",
  blurb:
    "USDA Thrifty Food Plan + Colorado eligibility, monthly benefit. Runs on every CO household drawn from Enhanced CPS.",
  default_state: "CO",
  state_choices: ["CO"],
  headline_label: "Annual cost",
  winners_label: "Gain",
  losers_label: "Loss",
  levers: [
    {
      id: "max_allotment_scale",
      label: "SNAP maximum allotment",
      description:
        "Scale every row of the USDA maximum-allotment-by-household-size table.",
      baseline_label: "1-person: $298 / mo",
      kind: "scale", baseline: 1,
      min: 0.5,
      max: 2,
      step: 0.05,
      build: (m) => [
        {
          repo: "rules-us",
          file_relative: SNAP_MAX_ALLOTMENT,
          parameter: "snap_maximum_allotment_table",
          patch_kind: "scale_values",
          multiplier: m,
        },
        {
          repo: "rules-us",
          file_relative: SNAP_MAX_ALLOTMENT,
          parameter: "snap_maximum_allotment_additional_member",
          patch_kind: "scale_formula",
          multiplier: m,
        },
      ],
    },
    {
      id: "standard_deduction_scale",
      label: "SNAP standard deduction",
      description:
        "Scale the SNAP standard deduction (48 states / DC) by household size.",
      baseline_label: "1–3 person: $209 / mo",
      kind: "scale", baseline: 1,
      min: 0,
      max: 2,
      step: 0.05,
      build: (m) => [
        {
          repo: "rules-us",
          file_relative: SNAP_DEDUCTIONS,
          parameter: "snap_standard_deduction_48_states_dc_table",
          patch_kind: "scale_values",
          multiplier: m,
        },
      ],
    },
  ],
};

// --- Program: Federal income tax §1(j) brackets ------------------------------

const FED_BRACKETS =
  "policies/irs/rev-proc-2025-32/income-tax-brackets.yaml";

const FEDERAL_INCOME_TAX: Program = {
  id: "federal-income-tax",
  name: "Federal income tax (§1(j) brackets)",
  short: "Federal income tax",
  blurb:
    "Ordinary individual income tax under IRC §1(j) — the 7-bracket schedule applied to taxable income (AGI minus standard deduction in our v1 proxy). Excludes capital gains preferential rates, AMT, and credits.",
  default_state: "US",
  state_choices: ["US", "CA", "TX", "NY", "FL", "CO", "WA", "MA", "IL"],
  headline_label: "Annual revenue",
  winners_label: "Pay less",
  losers_label: "Pay more",
  levers: [
    {
      id: "all_rates_scale",
      label: "All bracket rates (scale)",
      description:
        "Scale every rate in the 7-bracket schedule (10/12/22/24/32/35/37%) by the same multiplier.",
      baseline_label: "10 → 12 → 22 → 24 → 32 → 35 → 37 %",
      kind: "scale", baseline: 1,
      min: 0.5,
      max: 1.5,
      step: 0.01,
      build: (m) => [
        {
          repo: "rules-us",
          file_relative: FED_BRACKETS,
          parameter: "income_tax_bracket_rates",
          patch_kind: "scale_values",
          multiplier: m,
        },
      ],
    },
    {
      id: "joint_thresholds_scale",
      label: "Joint bracket thresholds (scale)",
      description:
        "Scale every bracket threshold for joint filers. >1.0 = wider brackets (lower tax); <1.0 = narrower.",
      baseline_label: "Joint D1 → 10% up to $24,800",
      kind: "scale", baseline: 1,
      min: 0.5,
      max: 2,
      step: 0.05,
      build: (m) => [
        {
          repo: "rules-us",
          file_relative: FED_BRACKETS,
          parameter: "income_tax_bracket_thresholds_joint",
          patch_kind: "scale_values",
          multiplier: m,
        },
      ],
    },
    {
      id: "single_thresholds_scale",
      label: "Single bracket thresholds (scale)",
      description: "Scale every bracket threshold for single filers.",
      baseline_label: "Single D1 → 10% up to $12,400",
      kind: "scale", baseline: 1,
      min: 0.5,
      max: 2,
      step: 0.05,
      build: (m) => [
        {
          repo: "rules-us",
          file_relative: FED_BRACKETS,
          parameter: "income_tax_bracket_thresholds_single",
          patch_kind: "scale_values",
          multiplier: m,
        },
      ],
    },
  ],
};

export const PROGRAMS: Program[] = [FEDERAL_CTC, FEDERAL_INCOME_TAX, COLORADO_SNAP];

export function programById(id: ProgramId): Program {
  const p = PROGRAMS.find((p) => p.id === id);
  if (!p) throw new Error(`unknown program ${id}`);
  return p;
}
