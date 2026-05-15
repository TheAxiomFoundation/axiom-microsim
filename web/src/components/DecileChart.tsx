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
          <XAxis dataKey="decile" tick={{ fontSize: 11, fill: "#57534e" }} />
          <YAxis
            tick={{ fontSize: 11, fill: "#57534e" }}
            tickFormatter={(v) => fmtCurrency(v as number)}
          />
          <Tooltip
            formatter={(v: number) => [fmtCurrency(v) + "/mo", "Mean monthly SNAP"]}
            labelFormatter={(d) => `Income ${d}`}
          />
          <Bar dataKey="mean" fill="#0d9488" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
