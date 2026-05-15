"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { DecileBin } from "@/lib/types";
import { fmtCurrency } from "@/lib/format";

interface Props {
  bins: DecileBin[];
}

export function DecileChart({ bins }: Props) {
  const data = bins.map((b) => ({
    decile: `D${b.decile}`,
    mean: b.mean_monthly_benefit,
  }));
  return (
    <div className="h-64 w-full">
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
            tickFormatter={(v) => fmtCurrency(v as number)}
            stroke="#78716c"
          />
          <Tooltip
            cursor={{ fill: "rgba(146, 64, 14, 0.06)" }}
            contentStyle={{
              backgroundColor: "#ffffff",
              border: "1px solid #e7e5e4",
              borderRadius: 4,
              fontSize: 12,
              fontFamily: "var(--f-sans)",
            }}
            formatter={(v: number) => [fmtCurrency(v) + "/mo", "Mean monthly SNAP"]}
            labelFormatter={(d) => `Income ${d}`}
          />
          <Bar dataKey="mean" fill="#92400e" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
