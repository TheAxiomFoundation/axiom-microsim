export interface DecileBin {
  decile: number;
  income_floor: number;
  income_ceiling: number;
  households_weighted: number;
  mean_monthly_benefit: number;
  share_receiving: number;
}

export interface Baseline {
  annual_cost: number;
  monthly_cost: number;
  households_with_benefit: number;
  average_monthly_benefit: number;
  decile_distribution: DecileBin[];
}

export interface DecileImpactBin {
  decile: number;
  income_floor: number;
  income_ceiling: number;
  households_weighted: number;
  mean_delta: number;
  share_winners: number;
  share_losers: number;
}

export interface Reform {
  baseline_annual_cost: number;
  reform_annual_cost: number;
  delta_annual_cost: number;
  households_winners: number;
  households_losers: number;
  households_unchanged: number;
  households_total_weighted: number;
  average_winner_gain_monthly: number;
  average_loser_loss_monthly: number;
  decile_impact: DecileImpactBin[];
}

export interface MicrosimResponse {
  program: string;
  state: string;
  period_year: number;
  n_households_sampled: number;
  n_persons_sampled: number;
  households_total_weighted: number;
  baseline: Baseline;
  reform?: Reform;
}

export interface Override {
  repo: "rules-us" | "rules-us-co";
  file_relative: string;
  parameter: string;
  patch_kind: "scale_values" | "set_values" | "scale_formula" | "set_formula";
  multiplier?: number;
  values?: Record<number, number>;
  formula?: string;
}

export interface MicrosimRequest {
  program: "co-snap" | "federal-income-tax" | "federal-ctc";
  state: string;
  year: number;
  overrides: Override[];
}

export interface PeComparison {
  computed_at: string;
  year: number;
  dataset: string;
  federal_income_tax: PeProgramNumbers | null;
  co_snap: PeProgramNumbers | null;
  federal_ctc: PeProgramNumbers | null;
  errors: string[];
}

export interface PeProgramNumbers {
  scope: string;
  axiom_output: string;
  pe_variable: string;
  // Federal income tax fields
  pe_total_revenue?: number;
  pe_n_tax_units?: number;
  pe_weighted_filers?: number;
  pe_weighted_total?: number;
  pe_avg_per_filer?: number;
  // SNAP fields
  pe_total_annual_cost?: number;
  pe_weighted_co_households?: number;
  pe_weighted_recipients?: number;
}
