"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DecileChart } from "@/components/DecileChart";
import { DecileImpactChart } from "@/components/DecileImpactChart";
import { StatCard } from "@/components/StatCard";
import { WinnersLosers } from "@/components/WinnersLosers";
import { PROGRAMS, programById, type Lever, type ProgramId } from "@/lib/levers";
import { fmtCount, fmtCurrency } from "@/lib/format";
import type { MicrosimRequest, MicrosimResponse } from "@/lib/types";

const YEAR = 2026;

const cacheKey = (programId: string, state: string, year: number) =>
  `${programId}|${state}|${year}`;

const initialDraft = (programId: ProgramId): Record<string, number> =>
  Object.fromEntries(programById(programId).levers.map((l) => [l.id, l.baseline]));

interface RunState {
  data: MicrosimResponse | null;
  startedAt: number | null;
  error: string | null;
}
const initial: RunState = { data: null, startedAt: null, error: null };

interface PeResult {
  total: number;
  baselineTotal: number | null;
  filers: number;
  avg: number;
}
interface PeState {
  result: PeResult | null;
  startedAt: number | null;
  error: string | null;
}
const peInitial: PeState = { result: null, startedAt: null, error: null };

export default function Page() {
  const [programId, setProgramId] = useState<ProgramId>("federal-ctc");
  const program = useMemo(() => programById(programId), [programId]);
  const [state, setState] = useState<string>(program.default_state);
  const [draft, setDraft] = useState<Record<string, number>>(() => initialDraft("federal-ctc"));
  const [applied, setApplied] = useState<Record<string, number>>(() => initialDraft("federal-ctc"));
  const [baseline, setBaseline] = useState<RunState>(initial);
  const [reform, setReform] = useState<RunState>(initial);
  const [pe, setPe] = useState<PeState>(peInitial);
  const [peReform, setPeReform] = useState<PeState>(peInitial);
  const [peEnabled, setPeEnabled] = useState(false);
  const [now, setNow] = useState(Date.now());

  // Session caches keyed by `program|state|year`; refs so writes don't render.
  const baselineCache = useRef<Map<string, MicrosimResponse>>(new Map());
  const peCache = useRef<Map<string, PeResult>>(new Map());
  const peReformCache = useRef<Map<string, PeResult>>(new Map());
  // Guards the auto-run against StrictMode double-fire and repeat renders.
  const autoRunKey = useRef<string | null>(null);

  const runMicrosim = useCallback(
    async (kind: "baseline" | "reform", values: Record<string, number>) => {
      const setter = kind === "baseline" ? setBaseline : setReform;
      const overrides =
        kind === "reform"
          ? program.levers.flatMap((l) =>
              values[l.id] === l.baseline ? [] : l.build(values[l.id]),
            )
          : [];
      const startedAt = Date.now();
      setter((prev) => ({ ...prev, startedAt, error: null }));
      const body: MicrosimRequest = { program: programId, state, year: YEAR, overrides };
      try {
        const r = await fetch("/microsim/api/microsim", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
        const data = (await r.json()) as MicrosimResponse;
        setter({ data, startedAt: null, error: null });
        if (kind === "baseline") baselineCache.current.set(cacheKey(programId, state, YEAR), data);
        if (kind === "reform") setApplied(values);
      } catch (e) {
        setter({ data: null, startedAt: null, error: String((e as Error).message ?? e) });
      }
    },
    [programId, state, program.levers],
  );

  const buildPeOverrides = useCallback(
    (values: Record<string, number>) => {
      const out = program.levers.flatMap((l) => {
        const v = values[l.id];
        if (v === undefined || v === l.baseline || !l.peBuild) return [];
        return l.peBuild(v);
      });
      return out.length ? out : null;
    },
    [program.levers],
  );

  const runPe = useCallback(
    async (values: Record<string, number> | null) => {
      // values === null → PE baseline; otherwise PE with the reform applied.
      const isReform = values !== null;
      const setter = isReform ? setPeReform : setPe;
      const overrides = isReform ? buildPeOverrides(values) : null;
      if (isReform && !overrides) {
        setPeReform({
          result: null, startedAt: null,
          error: "No PolicyEngine mapping is defined for the moved sliders.",
        });
        return;
      }
      const key =
        cacheKey(programId, state, YEAR) + (isReform ? "|" + JSON.stringify(overrides) : "");
      const cache = isReform ? peReformCache : peCache;
      const cached = cache.current.get(key);
      if (cached) {
        setter({ result: cached, startedAt: null, error: null });
        return;
      }
      const startedAt = Date.now();
      setter({ result: null, startedAt, error: null });
      try {
        const r = await fetch("/microsim/api/compare", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            program: programId, state, year: YEAR,
            ...(overrides ? { overrides } : {}),
          }),
        });
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
        const data = await r.json();
        const result: PeResult = {
          total: data.pe_total as number,
          baselineTotal: isReform
            ? ((data.pe_reform?.baseline_annual_cost ?? null) as number | null)
            : ((data.pe_baseline?.annual_cost ?? data.pe_total ?? null) as number | null),
          filers: data.pe_weighted_filers as number,
          avg: data.pe_avg_per_filer as number,
        };
        cache.current.set(key, result);
        setter({ result, startedAt: null, error: null });
      } catch (e) {
        setter({ result: null, startedAt: null, error: String((e as Error).message ?? e) });
      }
    },
    [buildPeOverrides, programId, state],
  );

  // Keep `state` valid when the program changes.
  useEffect(() => {
    if (!program.state_choices.includes(state)) setState(program.default_state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programId]);

  // On program/scope change: reset sliders, hydrate from caches, and
  // auto-run the baseline if we don't have it yet.
  useEffect(() => {
    if (!program.state_choices.includes(state)) return;
    setDraft(initialDraft(programId));
    setApplied(initialDraft(programId));
    setReform(initial);
    setPeReform(peInitial);
    const k = cacheKey(programId, state, YEAR);
    const cb = baselineCache.current.get(k);
    setBaseline(cb ? { data: cb, startedAt: null, error: null } : initial);
    const cp = peCache.current.get(k);
    setPe(cp ? { result: cp, startedAt: null, error: null } : peInitial);
    if (!cb && autoRunKey.current !== k) {
      autoRunKey.current = k;
      void runMicrosim("baseline", initialDraft(programId));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programId, state]);

  // Checking the PE box fetches the PE baseline for the current combo.
  useEffect(() => {
    if (peEnabled && pe.result === null && pe.startedAt === null && !pe.error) void runPe(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peEnabled, programId, state]);

  const draftReforming = useMemo(
    () => program.levers.some((l) => draft[l.id] !== l.baseline),
    [draft, program.levers],
  );
  const dirty = useMemo(
    () => program.levers.some((l) => draft[l.id] !== applied[l.id]),
    [draft, applied, program.levers],
  );

  const anyRunning =
    baseline.startedAt !== null || reform.startedAt !== null ||
    pe.startedAt !== null || peReform.startedAt !== null;

  useEffect(() => {
    if (!anyRunning) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [anyRunning]);

  const onRunReform = () => {
    if (!draftReforming || !dirty) return;
    void runMicrosim("reform", { ...draft });
    if (peEnabled) void runPe({ ...draft });
  };

  const elapsed = (startedAt: number | null) =>
    startedAt === null ? null : ((now - startedAt) / 1000).toFixed(1);

  const reformDelta = reform.data?.reform
    ? reform.data.reform.delta_annual_cost
    : null;
  const peBaselineTotal = peReform.result?.baselineTotal ?? pe.result?.total ?? null;
  const peDelta =
    peReform.result && peBaselineTotal != null ? peReform.result.total - peBaselineTotal : null;

  const ratio = (a?: number | null, p?: number | null) =>
    a != null && p != null && p !== 0 ? `${((a / p) * 100).toFixed(0)}%` : undefined;

  const unitLabel = programId === "co-snap" ? "households" : "tax units";
  const errors = [baseline.error, reform.error, pe.error, peReform.error].filter(Boolean);

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
      {/* ---- Header: program, scope, PE toggle ---- */}
      <header className="mb-6">
        <h1 className="font-serif text-2xl leading-tight tracking-tight text-ink sm:text-3xl">
          {program.name}
        </h1>
        {program.blurb && (
          <p className="mt-2 max-w-2xl text-sm text-ink-secondary">{program.blurb}</p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <div className="inline-flex overflow-hidden rounded-sm border border-rule">
            {PROGRAMS.map((p) => (
              <button
                key={p.id}
                onClick={() => setProgramId(p.id)}
                className={`px-3 py-1.5 text-sm transition ${
                  p.id === programId
                    ? "bg-accent text-white"
                    : "bg-paper-elev text-ink-secondary hover:bg-rule-subtle"
                }`}
              >
                {p.short}
              </button>
            ))}
          </div>

          {program.state_choices.length > 1 && (
            <select
              value={state}
              onChange={(e) => setState(e.target.value)}
              className="rounded-sm border border-rule bg-paper-elev px-2 py-1.5 font-mono text-xs"
              aria-label="Scope"
            >
              {program.state_choices.map((s) => (
                <option key={s} value={s}>
                  {s === "US" ? "Nationwide" : s}
                </option>
              ))}
            </select>
          )}

          <label className="ml-auto inline-flex items-center gap-2 text-xs text-ink-secondary">
            <input
              type="checkbox"
              checked={peEnabled}
              onChange={(e) => setPeEnabled(e.target.checked)}
              className="h-3.5 w-3.5 accent-accent"
            />
            Compare with PolicyEngine
          </label>
        </div>
      </header>

      {errors.length > 0 && (
        <div className="mb-4 space-y-2">
          {errors.map((e, i) => (
            <div key={i} className="rounded-sm border border-error bg-paper-elev p-3 text-sm text-error">
              {e}
            </div>
          ))}
        </div>
      )}

      {/* ---- Headline numbers ---- */}
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <StatCard
          label={`${program.headline_label} · baseline`}
          value={
            baseline.startedAt !== null
              ? `running… ${elapsed(baseline.startedAt)}s`
              : baseline.data
                ? fmtCurrency(baseline.data.baseline.annual_cost)
                : "—"
          }
          hint={
            baseline.data
              ? `${fmtCount(baseline.data.baseline.households_with_benefit)} ${unitLabel} · avg ${fmtCurrency(baseline.data.baseline.average_monthly_benefit)}`
              : undefined
          }
          peValue={
            pe.startedAt !== null
              ? `running… ${elapsed(pe.startedAt)}s`
              : pe.result
                ? fmtCurrency(pe.result.total)
                : undefined
          }
          peRatio={ratio(baseline.data?.baseline.annual_cost, pe.result?.total)}
        />
        <StatCard
          label={`${program.headline_label} · reform`}
          value={
            reform.startedAt !== null
              ? `running… ${elapsed(reform.startedAt)}s`
              : reform.data?.reform
                ? fmtCurrency(reform.data.reform.reform_annual_cost)
                : "—"
          }
          hint={reform.data?.reform ? undefined : "move a slider, then run"}
          peValue={
            peReform.startedAt !== null
              ? `running… ${elapsed(peReform.startedAt)}s`
              : peReform.result
                ? fmtCurrency(peReform.result.total)
                : undefined
          }
          peRatio={ratio(reform.data?.reform?.reform_annual_cost, peReform.result?.total)}
        />
        <StatCard
          label="Change vs baseline"
          value={reformDelta != null ? fmtSignedCurrency(reformDelta) : "—"}
          hint={
            reformDelta != null && reform.data?.reform && reform.data.reform.baseline_annual_cost !== 0
              ? `${((reformDelta / reform.data.reform.baseline_annual_cost) * 100).toFixed(1)}% of baseline`
              : undefined
          }
          peValue={peDelta != null ? fmtSignedCurrency(peDelta) : undefined}
          peRatio={ratio(reformDelta, peDelta)}
        />
      </div>

      {/* ---- Reform levers ---- */}
      <section className="mb-6 rounded-md border border-rule bg-paper-elev p-5">
        <div className="grid gap-x-8 gap-y-5 md:grid-cols-2 lg:grid-cols-3">
          {program.levers.map((l) => (
            <LeverControl
              key={l.id}
              lever={l}
              value={draft[l.id] ?? l.baseline}
              onChange={(v) => setDraft((prev) => ({ ...prev, [l.id]: v }))}
            />
          ))}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-rule pt-4">
          <button
            onClick={onRunReform}
            disabled={!draftReforming || !dirty || reform.startedAt !== null}
            className={`rounded-sm px-5 py-2 text-sm font-semibold transition ${
              !draftReforming || !dirty
                ? "cursor-not-allowed bg-rule text-ink-muted"
                : reform.startedAt !== null
                  ? "cursor-wait bg-accent-hover text-white"
                  : "bg-accent text-white hover:bg-accent-hover"
            }`}
          >
            {reform.startedAt !== null
              ? `Running… ${elapsed(reform.startedAt)}s`
              : "Run reform"}
          </button>
          <button
            onClick={() => setDraft(initialDraft(programId))}
            disabled={!draftReforming}
            className="rounded-sm border border-rule px-3 py-2 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted"
          >
            Reset
          </button>
          {dirty && draftReforming && reform.startedAt === null && (
            <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
              sliders changed — run to update
            </span>
          )}
        </div>
      </section>

      {/* ---- Charts ---- */}
      <div className="space-y-4">
        {reform.data?.reform ? (
          <>
            {reform.data.reform.decile_impact.length > 0 && (
              <ChartCard
                title="Mean change by income decile"
                subtitle={`${unitLabel === "households" ? "Households by gross income decile" : "Tax units by AGI decile"} · mean ${programId === "co-snap" ? "monthly" : "annual"} change`}
              >
                <DecileImpactChart
                  bins={reform.data.reform.decile_impact}
                  metricLabel={programId === "co-snap" ? "Mean monthly Δ" : "Mean annual Δ"}
                  metricSuffix={programId === "co-snap" ? "/mo" : "/yr"}
                />
              </ChartCard>
            )}
            <WinnersLosers
              reform={reform.data.reform}
              winnersLabel={program.winners_label}
              losersLabel={program.losers_label}
              unitLabel={unitLabel}
            />
          </>
        ) : (
          <ChartCard
            title="Distribution by income decile"
            subtitle={`${unitLabel === "households" ? "Households by gross income decile" : "Tax units by AGI decile"} · D1 lowest, D10 highest`}
          >
            {baseline.data ? (
              <div className="h-72 w-full">
                <DecileChart
                  bins={baseline.data.baseline.decile_distribution}
                  metricLabel={decileMetricLabel(programId)}
                  metricSuffix={programId === "co-snap" ? "/mo" : "/yr"}
                />
              </div>
            ) : (
              <div className="py-12 text-center text-sm text-ink-muted">
                {baseline.startedAt !== null ? `Computing baseline… ${elapsed(baseline.startedAt)}s` : "Baseline loads automatically."}
              </div>
            )}
          </ChartCard>
        )}

        {/* PE detail metrics, collapsed by default */}
        {peEnabled && (pe.result || peReform.result) && (
          <details className="rounded-md border border-rule bg-paper-elev p-4 text-sm">
            <summary className="cursor-pointer font-mono text-[0.7rem] uppercase tracking-eyebrow text-ink-secondary">
              PolicyEngine comparison details
            </summary>
            <table className="mt-3 w-full border-collapse font-mono text-xs">
              <thead className="text-left text-ink-muted">
                <tr>
                  <th className="py-1.5 pr-3 font-normal">Metric</th>
                  <th className="py-1.5 pr-3 text-right font-normal">Axiom</th>
                  <th className="py-1.5 text-right font-normal">PolicyEngine</th>
                </tr>
              </thead>
              <tbody className="text-ink">
                <tr className="border-t border-rule">
                  <td className="py-1.5 pr-3 font-sans">Weighted {unitLabel} affected</td>
                  <td className="py-1.5 pr-3 text-right">
                    {baseline.data ? fmtCount(baseline.data.baseline.households_with_benefit) : "—"}
                  </td>
                  <td className="py-1.5 text-right">
                    {pe.result ? fmtCount(pe.result.filers) : "—"}
                  </td>
                </tr>
                <tr className="border-t border-rule">
                  <td className="py-1.5 pr-3 font-sans">Average per unit</td>
                  <td className="py-1.5 pr-3 text-right">
                    {baseline.data ? fmtCurrency(baseline.data.baseline.average_monthly_benefit) : "—"}
                  </td>
                  <td className="py-1.5 text-right">
                    {pe.result ? fmtCurrency(pe.result.avg) : "—"}
                  </td>
                </tr>
                {peReform.result && (
                  <tr className="border-t border-rule">
                    <td className="py-1.5 pr-3 font-sans">Reform · weighted units (PE)</td>
                    <td className="py-1.5 pr-3 text-right">—</td>
                    <td className="py-1.5 text-right">{fmtCount(peReform.result.filers)}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </details>
        )}
      </div>

      <footer className="mt-8 border-t border-rule pt-4 text-xs text-ink-muted">
        {baseline.data
          ? `ECPS sample: ${baseline.data.n_households_sampled.toLocaleString()} ${unitLabel} · ${baseline.data.n_persons_sampled.toLocaleString()} persons · `
          : ""}
        <code className="font-mono">enhanced_cps_2024.h5</code> ·{" "}
        <code className="font-mono">axiom-rules-engine</code> ·{" "}
        <a href="/microsim/methodology" className="text-accent underline">
          methodology
        </a>
      </footer>
    </main>
  );
}

// --- pieces -----------------------------------------------------------------

function LeverControl({
  lever,
  value,
  onChange,
}: {
  lever: Lever;
  value: number;
  onChange: (v: number) => void;
}) {
  const changed = value !== lever.baseline;
  const display =
    lever.kind === "amount" ? fmtCurrency(value) : `${(value * 100).toFixed(0)}%`;
  return (
    <div className="space-y-1.5" title={lever.description}>
      <div className="flex items-baseline justify-between gap-3">
        <label className="text-sm font-medium text-ink">{lever.label}</label>
        <span
          className={`font-mono text-xs tabular-nums ${
            changed ? "font-semibold text-accent" : "text-ink-secondary"
          }`}
        >
          {display}
        </span>
      </div>
      <input
        type="range"
        min={lever.min}
        max={lever.max}
        step={lever.step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
      <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
        baseline · {lever.baseline_label}
      </div>
    </div>
  );
}

function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-5">
      <h3 className="font-serif text-lg text-ink">{title}</h3>
      {subtitle && <p className="mb-3 text-xs text-ink-muted">{subtitle}</p>}
      {children}
    </div>
  );
}

function decileMetricLabel(programId: ProgramId): string {
  if (programId === "co-snap") return "Mean monthly SNAP";
  if (programId === "federal-income-tax") return "Mean income tax per tax unit";
  return "Mean CTC per tax unit";
}

function fmtSignedCurrency(n: number): string {
  if (n === 0) return "$0";
  const sign = n > 0 ? "+" : "−";
  return `${sign}${fmtCurrency(Math.abs(n))}`;
}
