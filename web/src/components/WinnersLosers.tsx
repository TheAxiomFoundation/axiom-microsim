"use client";

import type { Reform } from "@/lib/types";
import { fmtCount, fmtPct } from "@/lib/format";

interface Props {
  reform: Reform;
}

export function WinnersLosers({ reform }: Props) {
  const total = reform.households_total_weighted || 1;
  const winners = reform.households_winners / total;
  const losers = reform.households_losers / total;
  const unchanged = reform.households_unchanged / total;
  return (
    <div className="space-y-3">
      <div className="flex h-6 w-full overflow-hidden rounded-md border border-stone-200 bg-stone-100">
        <div
          className="bg-emerald-500"
          style={{ width: `${winners * 100}%` }}
          title={`Gain: ${fmtPct(winners)}`}
        />
        <div
          className="bg-stone-300"
          style={{ width: `${unchanged * 100}%` }}
          title={`Unchanged: ${fmtPct(unchanged)}`}
        />
        <div
          className="bg-rose-500"
          style={{ width: `${losers * 100}%` }}
          title={`Loss: ${fmtPct(losers)}`}
        />
      </div>
      <div className="grid grid-cols-3 gap-2 text-sm">
        <div>
          <div className="font-medium text-emerald-700">Gain</div>
          <div>{fmtCount(reform.households_winners)} hh ({fmtPct(winners)})</div>
          <div className="text-xs text-stone-500">
            avg +${reform.average_winner_gain_monthly.toFixed(0)}/mo
          </div>
        </div>
        <div>
          <div className="font-medium text-stone-700">Unchanged</div>
          <div>{fmtCount(reform.households_unchanged)} hh ({fmtPct(unchanged)})</div>
        </div>
        <div>
          <div className="font-medium text-rose-700">Loss</div>
          <div>{fmtCount(reform.households_losers)} hh ({fmtPct(losers)})</div>
          <div className="text-xs text-stone-500">
            avg ${reform.average_loser_loss_monthly.toFixed(0)}/mo
          </div>
        </div>
      </div>
    </div>
  );
}
