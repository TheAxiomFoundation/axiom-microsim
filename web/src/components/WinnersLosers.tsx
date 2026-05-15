"use client";

import type { Reform } from "@/lib/types";
import { fmtCount, fmtCurrency, fmtPct } from "@/lib/format";

// "Unchanged" intentionally not surfaced; for sparse programs (CTC) it's
// dominated by ineligible units (no qualifying children, etc.) and
// drowns the meaningful winners/losers signal.

interface Props {
  reform: Reform;
  winnersLabel?: string;
  losersLabel?: string;
  unitLabel?: string;     // "households" | "tax units"
}

export function WinnersLosers({
  reform,
  winnersLabel = "Winners",
  losersLabel = "Losers",
  unitLabel = "tax units",
}: Props) {
  const total = reform.households_total_weighted || 1;
  const winners = reform.households_winners / total;
  const losers = reform.households_losers / total;

  return (
    <div className="space-y-5">
      {/* Two big columns: winners on the left, losers on the right */}
      <div className="grid grid-cols-2 gap-3">
        <SideCard
          tone="success"
          eyebrow={`▲ ${winnersLabel}`}
          headline={fmtPct(winners)}
          count={fmtCount(reform.households_winners)}
          unitLabel={unitLabel}
          delta={
            reform.households_winners > 0
              ? `avg +${fmtCurrency(reform.average_winner_gain_monthly)}/yr`
              : null
          }
        />
        <SideCard
          tone="error"
          eyebrow={`▼ ${losersLabel}`}
          headline={fmtPct(losers)}
          count={fmtCount(reform.households_losers)}
          unitLabel={unitLabel}
          delta={
            reform.households_losers > 0
              ? `avg −${fmtCurrency(reform.average_loser_loss_monthly)}/yr`
              : null
          }
        />
      </div>

    </div>
  );
}

function SideCard({
  tone, eyebrow, headline, count, unitLabel, delta,
}: {
  tone: "success" | "error";
  eyebrow: string;
  headline: string;
  count: string;
  unitLabel: string;
  delta: string | null;
}) {
  const color = tone === "success" ? "text-success" : "text-error";
  const border = tone === "success" ? "border-success/30" : "border-error/30";
  const tint = tone === "success" ? "bg-success/[0.04]" : "bg-error/[0.04]";
  return (
    <div className={`rounded-md border ${border} ${tint} p-4`}>
      <div className={`font-mono text-[0.7rem] uppercase tracking-eyebrow ${color}`}>
        {eyebrow}
      </div>
      <div className={`mt-2 font-serif text-[2.2rem] leading-none tracking-tight ${color}`}>
        {headline}
      </div>
      <div className="mt-2 text-sm text-ink-secondary">
        {count} {unitLabel}
      </div>
      {delta && <div className="mt-1 font-mono text-xs text-ink-muted">{delta}</div>}
    </div>
  );
}
