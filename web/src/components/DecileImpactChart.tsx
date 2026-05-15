"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { DecileImpactBin } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";

interface Props {
  bins: DecileImpactBin[];
  /** Sign convention: when positive, the program transfers MORE to the
   *  unit (CTC, SNAP). When negative, the unit pays more (income tax). */
  metricLabel?: string;
  metricSuffix?: string;
}

export function DecileImpactChart({
  bins,
  metricLabel = "Mean change",
  metricSuffix = "/yr",
}: Props) {
  const data = bins.map((b) => ({
    decile: `D${b.decile}`,
    range: `$${(b.income_floor / 1000).toFixed(0)}K – $${(b.income_ceiling / 1000).toFixed(0)}K`,
    delta: b.mean_delta,
    winnersShare: b.share_winners,
  }));
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 10, right: 10, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e7e5e4" />
          <XAxis
            dataKey="decile"
            tick={{ fontSize: 11, fill: "#57534e", fontFamily: "var(--f-mono)" }}
            stroke="#78716c"
          />
          <YAxis
            tick={{ fontSize: 11, fill: "#57534e", fontFamily: "var(--f-mono)" }}
            tickFormatter={(v) =>
              v === 0 ? "0" : (v > 0 ? "+" : "−") + fmtCurrency(Math.abs(v as number))
            }
            stroke="#78716c"
          />
          <ReferenceLine y={0} stroke="#1c1917" strokeWidth={1} />
          <Tooltip
            cursor={{ fill: "rgba(146, 64, 14, 0.06)" }}
            contentStyle={{
              backgroundColor: "#ffffff",
              border: "1px solid #e7e5e4",
              borderRadius: 4,
              fontSize: 12,
              fontFamily: "var(--f-sans)",
            }}
            formatter={(v: number, _name, props) => {
              const d = props.payload;
              const sign = v > 0 ? "+" : v < 0 ? "−" : "";
              return [
                `${sign}${fmtCurrency(Math.abs(v))}${metricSuffix} · ${(d.winnersShare * 100).toFixed(0)}% receive more`,
                metricLabel,
              ];
            }}
            labelFormatter={(d, payload) => {
              const item = payload?.[0]?.payload as { range?: string } | undefined;
              return `Income decile ${d}${item?.range ? ` · ${item.range}` : ""}`;
            }}
          />
          <Bar dataKey="delta" radius={[2, 2, 2, 2]}>
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={d.delta > 0 ? "#166534" : d.delta < 0 ? "#991b1b" : "#78716c"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
