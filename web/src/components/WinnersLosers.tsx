"use client";

import type { Reform } from "@/lib/types";
import { fmtCount, fmtCurrency, fmtPct } from "@/lib/format";

interface Props {
  reform: Reform;
  winnersLabel?: string;
  losersLabel?: string;
  unitLabel?: string;     // "households" | "tax units"
}

export function WinnersLosers({
  reform,
  winnersLabel = "Receive more",
  losersLabel = "Receive less",
  unitLabel = "tax units",
}: Props) {
  const total = reform.households_total_weighted || 1;
  const winners = reform.households_winners / total;
  const losers = reform.households_losers / total;
  const unchanged = reform.households_unchanged / total;
  return (
    <div className="space-y-4">
      {/* Big stacked bar */}
      <div className="space-y-1">
        <div className="flex h-8 w-full overflow-hidden rounded-sm border border-rule bg-rule-subtle">
          <Segment width={winners} color="bg-success" />
          <Segment width={unchanged} color="bg-rule" />
          <Segment width={losers} color="bg-error" />
        </div>
        <div className="flex justify-between font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      </div>

      {/* Three columns of stats */}
      <div className="grid grid-cols-3 gap-4">
        <Block
          color="text-success"
          dot="bg-success"
          label={winnersLabel}
          headline={fmtPct(winners)}
          sub={`${fmtCount(reform.households_winners)} ${unitLabel}`}
          delta={
            reform.households_winners > 0
              ? `avg +${fmtCurrency(reform.average_winner_gain_monthly)}/yr`
              : undefined
          }
        />
        <Block
          color="text-ink-secondary"
          dot="bg-rule-strong"
          label="Unchanged"
          headline={fmtPct(unchanged)}
          sub={`${fmtCount(reform.households_unchanged)} ${unitLabel}`}
        />
        <Block
          color="text-error"
          dot="bg-error"
          label={losersLabel}
          headline={fmtPct(losers)}
          sub={`${fmtCount(reform.households_losers)} ${unitLabel}`}
          delta={
            reform.households_losers > 0
              ? `avg −${fmtCurrency(reform.average_loser_loss_monthly)}/yr`
              : undefined
          }
        />
      </div>
    </div>
  );
}

function Segment({ width, color }: { width: number; color: string }) {
  return <div className={color} style={{ width: `${width * 100}%` }} />;
}

function Block({
  color, dot, label, headline, sub, delta,
}: {
  color: string;
  dot: string;
  label: string;
  headline: string;
  sub: string;
  delta?: string;
}) {
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
        <div className={`font-mono text-[0.65rem] uppercase tracking-eyebrow ${color}`}>
          {label}
        </div>
      </div>
      <div className={`mt-1.5 font-serif text-2xl tracking-tight ${color}`}>{headline}</div>
      <div className="mt-1 text-sm text-ink-secondary">{sub}</div>
      {delta && <div className="mt-0.5 font-mono text-xs text-ink-muted">{delta}</div>}
    </div>
  );
}
