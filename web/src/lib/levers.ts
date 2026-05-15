/**
 * Reform levers, organised by program.
 *
 * Each program declares its own slider catalogue. The page swaps the
 * lever list when the user toggles the program, and each lever's
 * `build` returns the engine `Override` for the patched RuleSpec.
 */
import type { Override } from "./types";

export type ProgramId = "co-snap" | "federal-income-tax";

export interface Lever {
  id: string;
  label: string;
  description: string;
  baseline_label: string;
  min_multiplier: number;
  max_multiplier: number;
  step: number;
  build: (multiplier: number) => Override[];
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

// --- Program: Colorado SNAP --------------------------------------------------

const SNAP_MAX_ALLOTMENT =
  "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml";
const SNAP_DEDUCTIONS = "policies/usda/snap/fy-2026-cola/deductions.yaml";

const COLORADO_SNAP: Program = {
  id: "co-snap",
  name: "Colorado SNAP",
  short: "CO SNAP",
  blurb:
    "USDA Thrifty Food Plan + Colorado eligibility, monthly benefit. " +
    "Runs on every CO household drawn from Enhanced CPS.",
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
      min_multiplier: 0.5,
      max_multiplier: 2,
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
      min_multiplier: 0,
      max_multiplier: 2,
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
    "Ordinary individual income tax under IRC §1(j) — the 7-bracket schedule " +
    "applied to taxable income (AGI minus standard deduction in our v1 proxy). " +
    "Excludes capital gains preferential rates, AMT, and credits — those are " +
    "encoded but not yet wired into one program.",
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
        "Scale every rate in the 7-bracket schedule (10% / 12% / 22% / 24% / 32% / 35% / 37%) by the same multiplier.",
      baseline_label: "10 → 12 → 22 → 24 → 32 → 35 → 37 %",
      min_multiplier: 0.5,
      max_multiplier: 1.5,
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
      min_multiplier: 0.5,
      max_multiplier: 2,
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
      description:
        "Scale every bracket threshold for single filers.",
      baseline_label: "Single D1 → 10% up to $12,400",
      min_multiplier: 0.5,
      max_multiplier: 2,
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

export const PROGRAMS: Program[] = [FEDERAL_INCOME_TAX, COLORADO_SNAP];

export function programById(id: ProgramId): Program {
  const p = PROGRAMS.find((p) => p.id === id);
  if (!p) throw new Error(`unknown program ${id}`);
  return p;
}
