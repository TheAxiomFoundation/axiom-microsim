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
  program: "co-snap";
  state: string;
  year: number;
  overrides: Override[];
}
