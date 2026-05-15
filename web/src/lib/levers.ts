/**
 * Reform levers exposed in the v1 UI.
 *
 * Mirrors a subset of axiom-co-snap's `LEVERS` list — same RuleSpec paths,
 * same patch semantics — so a reform expressed in either app evaluates to
 * identical numbers.
 */
import type { Override } from "./types";

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

const US_MAX_ALLOTMENT =
  "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml";
const US_DEDUCTIONS = "policies/usda/snap/fy-2026-cola/deductions.yaml";

export const LEVERS: Lever[] = [
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
        file_relative: US_MAX_ALLOTMENT,
        parameter: "snap_maximum_allotment_table",
        patch_kind: "scale_values",
        multiplier: m,
      },
      {
        repo: "rules-us",
        file_relative: US_MAX_ALLOTMENT,
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
        file_relative: US_DEDUCTIONS,
        parameter: "snap_standard_deduction_48_states_dc_table",
        patch_kind: "scale_values",
        multiplier: m,
      },
    ],
  },
];
