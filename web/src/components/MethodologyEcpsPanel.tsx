"use client";

import { useEffect, useState } from "react";

import { fmtCurrency } from "@/lib/format";

interface EcpsColumnStat {
  name: string;
  level: string;
  weighted_total: number;
  weighted_mean: number;
  nonzero_share: number;
  sample_size: number;
}

interface EcpsStatsResponse {
  program: string;
  state: string;
  n_persons_sample: number;
  n_units_sample: number;
  units_label: string;
  weighted_units: number;
  columns: EcpsColumnStat[];
}

const SCOPES: { id: string; label: string; state: string }[] = [
  { id: "federal-ctc", label: "Federal CTC · nationwide", state: "US" },
  { id: "federal-income-tax", label: "Federal income tax · nationwide", state: "US" },
  { id: "co-snap", label: "Colorado SNAP", state: "CO" },
];

export function MethodologyEcpsPanel() {
  return (
    <div className="space-y-6">
      <p className="editorial">
        These are the actual weighted aggregates of every ECPS column the
        loader reads, for the scope shown. If a number here looks wrong,
        the projection downstream will be wrong too — they're the
        upstream truth.
      </p>
      {SCOPES.map((s) => (
        <ScopePanel key={s.id} program={s.id} state={s.state} label={s.label} />
      ))}
    </div>
  );
}

function ScopePanel({
  program, state, label,
}: { program: string; state: string; label: string }) {
  const [data, setData] = useState<EcpsStatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    fetch(`/api/ecps-stats?program=${program}&state=${state}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
        return r.json();
      })
      .then((d) => setData(d as EcpsStatsResponse))
      .catch((e) => setError(String(e.message ?? e)));
  }, [program, state]);

  if (error) {
    return (
      <div className="rounded-md border border-error bg-paper-elev p-4 text-sm text-error">
        {label}: {error}
      </div>
    );
  }
  if (!data) {
    return (
      <div className="rounded-md border border-rule bg-paper-elev p-4 text-xs text-ink-muted">
        Loading {label}…
      </div>
    );
  }

  const top = data.columns.filter((c) => c.weighted_total !== 0);

  return (
    <div className="rounded-md border border-rule bg-paper-elev">
      <div className="border-b border-rule bg-rule-subtle px-4 py-2">
        <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          {label}
        </div>
        <div className="mt-0.5 text-xs text-ink-secondary">
          {data.n_units_sample.toLocaleString()} {data.units_label} sampled ·
          weighted to {(data.weighted_units / 1e6).toFixed(1)}M ·
          {data.n_persons_sample.toLocaleString()} persons
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
            <tr className="border-b border-rule">
              <th className="px-3 py-2 text-left">ECPS column</th>
              <th className="px-3 py-2 text-right">Weighted total</th>
              <th className="px-3 py-2 text-right">Mean per {data.units_label.slice(0, -1)}</th>
              <th className="px-3 py-2 text-right">% of {data.units_label} {">"} 0</th>
            </tr>
          </thead>
          <tbody className="font-mono text-sm">
            {top.map((c) => (
              <tr key={c.name} className="border-t border-rule">
                <td className="px-3 py-2 text-accent">{c.name}</td>
                <td className="px-3 py-2 text-right text-ink">{fmtCurrency(c.weighted_total)}</td>
                <td className="px-3 py-2 text-right text-ink-secondary">
                  {fmtCurrency(c.weighted_mean)}
                </td>
                <td className="px-3 py-2 text-right text-ink-secondary">
                  {(c.nonzero_share * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
