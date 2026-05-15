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
  winnersLabel = "Winners",
  losersLabel = "Losers",
  unitLabel = "tax units",
}: Props) {
  const total = reform.households_total_weighted || 1;
  const winners = reform.households_winners / total;
  const losers = reform.households_losers / total;
  const unchanged = reform.households_unchanged / total;

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

      {/* Stacked bar — visual share guide */}
      <div className="space-y-1">
        <div className="flex h-3 w-full overflow-hidden rounded-sm border border-rule bg-rule-subtle">
          <Segment width={winners} color="bg-success" />
          <Segment width={unchanged} color="bg-rule" />
          <Segment width={losers} color="bg-error" />
        </div>
        <div className="flex justify-between font-mono text-[0.6rem] uppercase tracking-eyebrow text-ink-muted">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      </div>

      {/* Unchanged — de-emphasised single line */}
      <div className="flex items-center justify-between rounded-sm border border-rule bg-rule-subtle px-3 py-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-rule-strong" />
          <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
            Unchanged
          </span>
        </div>
        <div className="font-mono text-ink-secondary">
          {fmtPct(unchanged)} · {fmtCount(reform.households_unchanged)} {unitLabel}
        </div>
      </div>
    </div>
  );
}

function Segment({ width, color }: { width: number; color: string }) {
  return <div className={color} style={{ width: `${width * 100}%` }} />;
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
