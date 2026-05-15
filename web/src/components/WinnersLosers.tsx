"use client";

import type { Reform } from "@/lib/types";
import { fmtCount, fmtPct } from "@/lib/format";

interface Props {
  reform: Reform;
  winnersLabel?: string;
  losersLabel?: string;
}

export function WinnersLosers({
  reform,
  winnersLabel = "Gain",
  losersLabel = "Loss",
}: Props) {
  const total = reform.households_total_weighted || 1;
  const winners = reform.households_winners / total;
  const losers = reform.households_losers / total;
  const unchanged = reform.households_unchanged / total;
  return (
    <div className="space-y-4">
      <div className="flex h-6 w-full overflow-hidden rounded-sm border border-rule bg-rule-subtle">
        <div
          className="bg-success"
          style={{ width: `${winners * 100}%` }}
          title={`Gain: ${fmtPct(winners)}`}
        />
        <div
          className="bg-rule"
          style={{ width: `${unchanged * 100}%` }}
          title={`Unchanged: ${fmtPct(unchanged)}`}
        />
        <div
          className="bg-error"
          style={{ width: `${losers * 100}%` }}
          title={`Loss: ${fmtPct(losers)}`}
        />
      </div>
      <div className="grid grid-cols-3 gap-3 text-sm">
        <Block
          label={winnersLabel}
          color="text-success"
          count={reform.households_winners}
          share={winners}
          delta={`avg $${reform.average_winner_gain_monthly.toFixed(0)}`}
        />
        <Block
          label="Unchanged"
          color="text-ink-secondary"
          count={reform.households_unchanged}
          share={unchanged}
        />
        <Block
          label={losersLabel}
          color="text-error"
          count={reform.households_losers}
          share={losers}
          delta={`avg $${reform.average_loser_loss_monthly.toFixed(0)}`}
        />
      </div>
    </div>
  );
}

function Block({
  label,
  color,
  count,
  share,
  delta,
}: {
  label: string;
  color: string;
  count: number;
  share: number;
  delta?: string;
}) {
  return (
    <div>
      <div
        className={`font-mono text-[0.65rem] uppercase tracking-eyebrow ${color}`}
      >
        {label}
      </div>
      <div className="mt-1 text-ink">
        {fmtCount(count)} hh ({fmtPct(share)})
      </div>
      {delta && <div className="text-xs text-ink-muted">{delta}</div>}
    </div>
  );
}
