"use client";

import { useEffect, useMemo, useState } from "react";

import { DecileChart } from "@/components/DecileChart";
import { StatCard } from "@/components/StatCard";
import { WinnersLosers } from "@/components/WinnersLosers";
import { LEVERS } from "@/lib/levers";
import { fmtCount, fmtCurrency } from "@/lib/format";
import type { MicrosimRequest, MicrosimResponse } from "@/lib/types";

const DEFAULT_STATE = "CO";
const DEFAULT_YEAR = 2026;

export default function Page() {
  const [multipliers, setMultipliers] = useState<Record<string, number>>(() =>
    Object.fromEntries(LEVERS.map((l) => [l.id, 1])),
  );
  const [data, setData] = useState<MicrosimResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  const overrides = useMemo(
    () =>
      LEVERS.flatMap((l) =>
        multipliers[l.id] === 1 ? [] : l.build(multipliers[l.id]),
      ),
    [multipliers],
  );
  const reforming = overrides.length > 0;

  useEffect(() => {
    const controller = new AbortController();
    const body: MicrosimRequest = {
      program: "co-snap",
      state: DEFAULT_STATE,
      year: DEFAULT_YEAR,
      overrides,
    };
    setLoading(true);
    setError(null);
    const t0 = performance.now();
    fetch("/api/microsim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
        return (await r.json()) as MicrosimResponse;
      })
      .then((d) => {
        setData(d);
        setLatencyMs(Math.round(performance.now() - t0));
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(String(e.message ?? e));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [overrides]);

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <div className="text-xs uppercase tracking-wider text-teal-700">
          axiom-microsim · CO SNAP · FY 2026
        </div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">
          Colorado SNAP, on Axiom rules, on Enhanced CPS
        </h1>
        <p className="mt-2 max-w-3xl text-stone-600">
          Adjust SNAP parameters and see weighted population impact for
          Colorado households. Runs entirely on the{" "}
          <code className="rounded bg-stone-100 px-1 py-0.5 text-sm">
            axiom-rules-engine
          </code>{" "}
          dense executor — no PolicyEngine code in the runtime path.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[320px,1fr]">
        <aside className="space-y-5 rounded-xl border border-stone-200 bg-white p-5">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
            Reform parameters
          </h2>
          {LEVERS.map((l) => {
            const m = multipliers[l.id];
            return (
              <div key={l.id}>
                <div className="flex items-baseline justify-between">
                  <label className="text-sm font-medium text-stone-800">
                    {l.label}
                  </label>
                  <span className="text-xs tabular-nums text-stone-600">
                    {(m * 100).toFixed(0)}%
                  </span>
                </div>
                <input
                  type="range"
                  min={l.min_multiplier}
                  max={l.max_multiplier}
                  step={l.step}
                  value={m}
                  onChange={(e) =>
                    setMultipliers((prev) => ({
                      ...prev,
                      [l.id]: Number(e.target.value),
                    }))
                  }
                  className="w-full accent-teal-600"
                />
                <div className="mt-1 text-xs text-stone-500">
                  {l.description}
                </div>
                <div className="mt-1 text-xs text-stone-400">
                  Baseline: {l.baseline_label}
                </div>
              </div>
            );
          })}
          <button
            onClick={() =>
              setMultipliers(Object.fromEntries(LEVERS.map((l) => [l.id, 1])))
            }
            className="w-full rounded-md border border-stone-300 px-3 py-2 text-sm hover:bg-stone-50"
            disabled={!reforming}
          >
            Reset to baseline
          </button>
          <div className="rounded-md bg-stone-50 p-3 text-xs text-stone-500">
            {loading
              ? "Running…"
              : latencyMs !== null && (
                  <>
                    <div>Last run: {latencyMs} ms</div>
                    {data && (
                      <div className="mt-1">
                        Sample: {data.n_households_sampled} CO households · weighted to{" "}
                        {fmtCount(data.households_total_weighted)}
                      </div>
                    )}
                  </>
                )}
          </div>
        </aside>

        <section className="space-y-6">
          {error && (
            <div className="rounded-md border border-rose-300 bg-rose-50 p-3 text-sm text-rose-700">
              {error}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard
              label="Annual cost"
              value={data ? fmtCurrency(data.baseline.annual_cost) : "—"}
              hint={reforming ? "baseline" : undefined}
            />
            <StatCard
              label="Reform cost"
              value={
                data?.reform
                  ? fmtCurrency(data.reform.reform_annual_cost)
                  : "—"
              }
              delta={
                data?.reform
                  ? {
                      value: `${data.reform.delta_annual_cost >= 0 ? "+" : ""}${fmtCurrency(
                        data.reform.delta_annual_cost,
                      )}`,
                      positive:
                        data.reform.delta_annual_cost === 0
                          ? null
                          : data.reform.delta_annual_cost > 0,
                    }
                  : undefined
              }
            />
            <StatCard
              label="Households w/ benefit"
              value={
                data ? fmtCount(data.baseline.households_with_benefit) : "—"
              }
            />
            <StatCard
              label="Avg monthly benefit"
              value={
                data
                  ? fmtCurrency(data.baseline.average_monthly_benefit)
                  : "—"
              }
            />
          </div>

          <div className="rounded-xl border border-stone-200 bg-white p-5">
            <h3 className="text-sm font-semibold text-stone-700">
              Mean SNAP allotment by household income decile
            </h3>
            <p className="mb-3 text-xs text-stone-500">
              Households grouped by weighted decile of their gross annual
              income (employment + self-employment + investment + pensions).
            </p>
            {data && <DecileChart bins={data.baseline.decile_distribution} />}
          </div>

          <div className="rounded-xl border border-stone-200 bg-white p-5">
            <h3 className="text-sm font-semibold text-stone-700">
              Reform impact on households
            </h3>
            <p className="mb-3 text-xs text-stone-500">
              Compared to the baseline. Adjust a slider to populate.
            </p>
            {data?.reform ? (
              <WinnersLosers reform={data.reform} />
            ) : (
              <div className="py-4 text-sm text-stone-400">
                No reform applied.
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
