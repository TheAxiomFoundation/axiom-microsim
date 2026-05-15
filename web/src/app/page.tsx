"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { DecileChart } from "@/components/DecileChart";
import { WinnersLosers } from "@/components/WinnersLosers";
import { PROGRAMS, programById, type Lever, type ProgramId } from "@/lib/levers";
import { fmtCount, fmtCurrency } from "@/lib/format";
import type { MicrosimRequest, MicrosimResponse } from "@/lib/types";

const YEAR = 2026;

const initialDraft = (programId: ProgramId): Record<string, number> =>
  Object.fromEntries(programById(programId).levers.map((l) => [l.id, l.baseline]));

interface RunState {
  data: MicrosimResponse | null;
  loadingMs: number | null;
  startedAt: number | null;
  error: string | null;
}
const initial: RunState = { data: null, loadingMs: null, startedAt: null, error: null };

interface PeState {
  total: number | null;
  filers: number | null;
  avg: number | null;
  loadingMs: number | null;
  startedAt: number | null;
  error: string | null;
}
const peInitial: PeState = {
  total: null, filers: null, avg: null,
  loadingMs: null, startedAt: null, error: null,
};

export default function Page() {
  const [programId, setProgramId] = useState<ProgramId>("federal-ctc");
  const program = useMemo(() => programById(programId), [programId]);
  const [state, setState] = useState<string>(program.default_state);
  // Eagerly initialise draft / applied for the current program so the
  // first render of LeverControl always has a defined value (no
  // controlled→uncontrolled flicker when the program toggles).
  const [draft, setDraft] = useState<Record<string, number>>(() => initialDraft("federal-ctc"));
  const [applied, setApplied] = useState<Record<string, number>>(() => initialDraft("federal-ctc"));
  const [baseline, setBaseline] = useState<RunState>(initial);
  const [reform, setReform] = useState<RunState>(initial);
  const [pe, setPe] = useState<PeState>(peInitial);
  const [now, setNow] = useState(Date.now());

  // Reset everything when program / state changes. Use the functional
  // form so the new draft is computed from the new programId synchronously
  // — and combine with a useMemo guard to avoid the brief render where
  // draft is keyed for the OLD program but levers come from the NEW one.
  useEffect(() => {
    setState(program.default_state);
    setDraft(initialDraft(programId));
    setApplied(initialDraft(programId));
    setBaseline(initial);
    setReform(initial);
    setPe(peInitial);
  }, [programId, program.default_state]);

  // Re-set the slider draft whenever the state changes too.
  useEffect(() => {
    setDraft(initialDraft(programId));
    setApplied(initialDraft(programId));
    setReform(initial);
    setPe(peInitial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const dirty = useMemo(
    () => program.levers.some((l) => draft[l.id] !== applied[l.id]),
    [draft, applied, program.levers],
  );
  const draftReforming = useMemo(
    () => program.levers.some((l) => draft[l.id] !== l.baseline),
    [draft, program.levers],
  );
  const appliedReforming = useMemo(
    () => program.levers.some((l) => applied[l.id] !== l.baseline),
    [applied, program.levers],
  );

  useEffect(() => {
    if (
      baseline.startedAt === null &&
      reform.startedAt === null &&
      pe.startedAt === null
    ) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [baseline.startedAt, reform.startedAt, pe.startedAt]);

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
      setter((prev) => ({ ...prev, loadingMs: 0, startedAt, error: null }));

      const body: MicrosimRequest = { program: programId, state, year: YEAR, overrides };
      try {
        const r = await fetch("/api/microsim", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
        const data = (await r.json()) as MicrosimResponse;
        setter({ data, loadingMs: Date.now() - startedAt, startedAt: null, error: null });
        if (kind === "reform") setApplied(values);
      } catch (e) {
        setter({
          data: null,
          loadingMs: null,
          startedAt: null,
          error: String((e as Error).message ?? e),
        });
      }
    },
    [programId, state, program.levers],
  );

  const runPe = useCallback(async () => {
    const startedAt = Date.now();
    setPe((prev) => ({ ...prev, loadingMs: 0, startedAt, error: null }));
    try {
      const r = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ program: programId, state, year: YEAR }),
      });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const data = await r.json();
      setPe({
        total: data.pe_total,
        filers: data.pe_weighted_filers,
        avg: data.pe_avg_per_filer,
        loadingMs: Date.now() - startedAt,
        startedAt: null,
        error: null,
      });
    } catch (e) {
      setPe({
        total: null, filers: null, avg: null,
        loadingMs: null, startedAt: null,
        error: String((e as Error).message ?? e),
      });
    }
  }, [programId, state]);

  useEffect(() => {
    void runMicrosim("baseline", initialDraft(programId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programId, state]);

  const onRunReform = () => {
    if (!draftReforming) return;
    void runMicrosim("reform", { ...draft });
  };

  const baselineRunning = baseline.startedAt !== null;
  const reformRunning = reform.startedAt !== null;
  const peRunning = pe.startedAt !== null;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <header className="mb-8 border-b border-rule pb-6">
        <div className="flex items-center gap-2 font-mono text-[0.7rem] uppercase tracking-eyebrow text-accent">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />
          axiom-microsim · FY {YEAR}
        </div>
        <h1 className="mt-3 font-serif text-[2.4rem] leading-[1.1] tracking-tight text-ink">
          {program.name}.
        </h1>
        <p className="editorial mt-4 max-w-3xl">{program.blurb}</p>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
            Program
          </span>
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
            <>
              <span className="ml-2 font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
                Scope
              </span>
              <select
                value={state}
                onChange={(e) => setState(e.target.value)}
                className="rounded-sm border border-rule bg-paper-elev px-2 py-1.5 font-mono text-xs"
              >
                {program.state_choices.map((s) => (
                  <option key={s} value={s}>
                    {s === "US" ? "Nationwide" : s}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      </header>

      {/* Two big headline numbers */}
      <section className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Headline
          eyebrow={`Baseline · ${program.headline_label.toLowerCase()}`}
          value={baseline.data ? fmtCurrency(baseline.data.baseline.annual_cost) : "—"}
          sub={
            baseline.data
              ? `${fmtCount(baseline.data.baseline.households_with_benefit)} ${
                  programId === "co-snap" ? "households" : "tax units"
                } affected · avg ${fmtCurrency(baseline.data.baseline.average_monthly_benefit)} ${
                  programId === "co-snap" ? "/ mo" : "/ yr"
                }`
              : "—"
          }
          loading={baselineRunning}
          loadingElapsed={baselineRunning ? (now - (baseline.startedAt ?? now)) / 1000 : null}
        />
        <Headline
          eyebrow="Reform · change vs baseline"
          value={
            reform.data?.reform
              ? fmtSignedCurrency(reform.data.reform.delta_annual_cost)
              : "—"
          }
          sub={
            reform.data?.reform
              ? `Reform total ${fmtCurrency(reform.data.reform.reform_annual_cost)}`
              : "Adjust a slider, then click Run reform."
          }
          loading={reformRunning}
          loadingElapsed={reformRunning ? (now - (reform.startedAt ?? now)) / 1000 : null}
          accent={
            reform.data?.reform
              ? reform.data.reform.delta_annual_cost > 0
                ? "text-error"
                : reform.data.reform.delta_annual_cost < 0
                  ? "text-success"
                  : undefined
              : undefined
          }
        />
      </section>

      {/* PE comparison panel — live, opt-in */}
      <PePanel
        pe={pe}
        running={peRunning}
        elapsed={peRunning ? (now - (pe.startedAt ?? now)) / 1000 : null}
        onRun={runPe}
        axiomBaseline={baseline.data?.baseline.annual_cost}
        axiomFilers={baseline.data?.baseline.households_with_benefit}
        axiomAvg={baseline.data?.baseline.average_monthly_benefit}
        programId={programId}
      />

      <div className="grid gap-6 lg:grid-cols-[340px,1fr]">
        {/* Sidebar: levers */}
        <aside className="space-y-5 rounded-md border border-rule bg-paper-elev p-5">
          <h2 className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
            Reform parameters
          </h2>

          {program.levers.map((l) => (
            <LeverControl
              key={l.id}
              lever={l}
              // Default to the lever's baseline when draft hasn't been
              // initialised for this lever yet (happens for one render
              // when the program switches before the reset effect runs).
              value={draft[l.id] ?? l.baseline}
              applied={applied[l.id] ?? l.baseline}
              onChange={(v) => setDraft((prev) => ({ ...prev, [l.id]: v }))}
            />
          ))}

          <button
            onClick={onRunReform}
            disabled={!draftReforming || reformRunning || !dirty}
            className={`w-full rounded-sm px-4 py-2.5 text-sm font-semibold uppercase tracking-eyebrow transition ${
              !draftReforming || !dirty
                ? "cursor-not-allowed bg-rule text-ink-muted"
                : reformRunning
                  ? "cursor-wait bg-accent-hover text-white"
                  : "bg-accent text-white hover:bg-accent-hover"
            }`}
          >
            {reformRunning
              ? `Running… ${((now - (reform.startedAt ?? now)) / 1000).toFixed(1)}s`
              : !draftReforming
                ? "Move a slider"
                : !dirty
                  ? "Reform up to date"
                  : "▶ Run reform"}
          </button>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => setDraft(initialDraft(programId))}
              disabled={!draftReforming}
              className="rounded-sm border border-rule px-3 py-1.5 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted"
            >
              Reset
            </button>
            <button
              onClick={() => {
                setReform(initial);
                setApplied(initialDraft(programId));
              }}
              disabled={!appliedReforming}
              className="rounded-sm border border-rule px-3 py-1.5 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted"
            >
              Clear reform
            </button>
          </div>
        </aside>

        {/* Charts */}
        <section className="space-y-6">
          {(baseline.error || reform.error) && (
            <div className="rounded-sm border border-error bg-paper-elev p-3 text-sm text-error">
              {baseline.error && <div>Baseline error: {baseline.error}</div>}
              {reform.error && <div>Reform error: {reform.error}</div>}
            </div>
          )}

          <Card title="Distribution by income decile" subtitle={decileSubtitle(programId)}>
            {baseline.data && (
              <div className="h-72 w-full">
                <DecileChart
                  bins={baseline.data.baseline.decile_distribution}
                  metricLabel={decileMetricLabel(programId)}
                  metricSuffix={programId === "co-snap" ? "/mo" : "/yr"}
                />
              </div>
            )}
          </Card>

          <Card
            title={`Reform impact on ${programId === "co-snap" ? "households" : "tax units"}`}
            subtitle="Per-unit change vs baseline, weighted to the population."
          >
            {reform.data?.reform ? (
              <WinnersLosers
                reform={reform.data.reform}
                winnersLabel={program.winners_label}
                losersLabel={program.losers_label}
              />
            ) : (
              <div className="rounded-sm border border-dashed border-rule px-4 py-8 text-center text-sm text-ink-muted">
                Run a reform to populate.
              </div>
            )}
          </Card>

          <footer className="text-xs text-ink-muted">
            ECPS sample:{" "}
            {baseline.data?.n_households_sampled.toLocaleString() ?? "—"}{" "}
            {programId === "co-snap" ? "households" : "tax units"} ·{" "}
            {baseline.data?.n_persons_sampled.toLocaleString() ?? "—"} persons ·{" "}
            <code className="font-mono">enhanced_cps_2024.h5</code> · engine{" "}
            <code className="font-mono">axiom-rules-engine</code>. See{" "}
            <a href="/methodology" className="text-accent underline">/methodology</a>{" "}
            for slot mappings, calculations, and limitations.
          </footer>
        </section>
      </div>
    </main>
  );
}


// --- pieces -----------------------------------------------------------------

function LeverControl({
  lever,
  value,
  applied,
  onChange,
}: {
  lever: Lever;
  value: number;
  applied: number;
  onChange: (v: number) => void;
}) {
  const changed = value !== applied;
  const isAmount = lever.kind === "amount";
  const display = isAmount
    ? fmtCurrency(value)
    : `${(value * 100).toFixed(0)}%`;
  const appliedDisplay = isAmount
    ? fmtCurrency(applied)
    : `${(applied * 100).toFixed(0)}%`;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <label className="text-sm font-medium text-ink">{lever.label}</label>
        <span
          className={`font-mono text-xs tabular-nums ${
            changed ? "font-semibold text-warning" : "text-ink-secondary"
          }`}
        >
          {display}
          {changed && (
            <span className="ml-1 text-ink-muted">(was {appliedDisplay})</span>
          )}
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
      <div className="text-xs text-ink-muted">{lever.description}</div>
      <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
        baseline · {lever.baseline_label}
      </div>
    </div>
  );
}

function Headline({
  eyebrow, value, sub, loading, loadingElapsed, accent,
}: {
  eyebrow: string; value: string; sub: string;
  loading: boolean; loadingElapsed: number | null; accent?: string;
}) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-6">
      <div className="font-mono text-[0.7rem] uppercase tracking-eyebrow text-ink-muted">
        {eyebrow}
      </div>
      <div className={`mt-3 font-serif text-[3rem] leading-none tracking-tight ${accent ?? "text-ink"}`}>
        {loading ? (
          <span className="font-mono text-2xl text-ink-muted">
            running… {loadingElapsed?.toFixed(1)}s
          </span>
        ) : value}
      </div>
      <div className="mt-3 text-sm text-ink-secondary">{sub}</div>
    </div>
  );
}

function Card({
  title, subtitle, children,
}: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-6">
      <h3 className="font-serif text-lg text-ink">{title}</h3>
      {subtitle && <p className="mb-4 text-xs text-ink-muted">{subtitle}</p>}
      {children}
    </div>
  );
}

function PePanel({
  pe, running, elapsed, onRun,
  axiomBaseline, axiomFilers, axiomAvg, programId,
}: {
  pe: PeState;
  running: boolean;
  elapsed: number | null;
  onRun: () => void;
  axiomBaseline?: number;
  axiomFilers?: number;
  axiomAvg?: number;
  programId: ProgramId;
}) {
  const ratio = (a?: number | null, p?: number | null) =>
    a != null && p != null && p !== 0 ? `${((a / p) * 100).toFixed(0)}%` : "—";

  const hasResult = pe.total != null;

  return (
    <div className="mb-8 rounded-md border border-rule bg-paper-elev p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-accent">
            Side-by-side · PolicyEngine
          </div>
          <h3 className="mt-1 font-serif text-lg text-ink">
            Live comparison — same dataset, same parameters, computed fresh.
          </h3>
        </div>
        <button
          onClick={onRun}
          disabled={running}
          className={`rounded-sm px-3 py-2 font-mono text-xs uppercase tracking-eyebrow transition ${
            running
              ? "cursor-wait bg-accent-hover text-white"
              : "bg-accent text-white hover:bg-accent-hover"
          }`}
          title="Run PolicyEngine on the same scope (~100s)"
        >
          {running
            ? `Running PE… ${elapsed?.toFixed(1)}s`
            : hasResult
              ? "▶ Re-run PE comparison"
              : "▶ Run PE comparison"}
        </button>
      </div>

      {pe.error && (
        <div className="mb-3 rounded-sm border border-error bg-paper p-3 text-sm text-error">
          {pe.error}
        </div>
      )}

      {!hasResult && !running && !pe.error && (
        <p className="text-xs text-ink-muted">
          Click <em>Run PE comparison</em> to compute the same aggregate in PolicyEngine.
          Takes ~100 s; nothing is cached, every click recomputes both sides fresh.
        </p>
      )}

      {(hasResult || running) && (
        <div className="overflow-hidden rounded-sm border border-rule">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-rule-subtle font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
              <tr>
                <th className="px-3 py-2 text-left">Metric</th>
                <th className="px-3 py-2 text-right">Axiom</th>
                <th className="px-3 py-2 text-right">PolicyEngine</th>
                <th className="px-3 py-2 text-right">Axiom / PE</th>
              </tr>
            </thead>
            <tbody className="font-mono text-sm">
              <Row
                metric="Annual cost / revenue"
                axiom={axiomBaseline != null ? fmtCurrency(axiomBaseline) : "—"}
                pe={pe.total != null ? fmtCurrency(pe.total) : "—"}
                ratio={ratio(axiomBaseline, pe.total)}
              />
              <Row
                metric={programId === "co-snap" ? "Weighted recipients" : "Weighted units affected"}
                axiom={axiomFilers != null ? fmtCount(axiomFilers) : "—"}
                pe={pe.filers != null ? fmtCount(pe.filers) : "—"}
                ratio={ratio(axiomFilers, pe.filers)}
              />
              <Row
                metric={programId === "co-snap" ? "Avg monthly benefit" : programId === "federal-ctc" ? "Avg credit per recipient" : "Avg per filer"}
                axiom={axiomAvg != null ? fmtCurrency(axiomAvg) : "—"}
                pe={pe.avg != null ? fmtCurrency(pe.avg) : "—"}
                ratio={ratio(axiomAvg, pe.avg)}
              />
            </tbody>
          </table>
        </div>
      )}

      {hasResult && pe.loadingMs && (
        <p className="mt-3 font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          PE computed in {(pe.loadingMs / 1000).toFixed(1)}s · see{" "}
          <a href="/methodology" className="text-accent underline">/methodology</a>{" "}
          for what each side does and doesn't model.
        </p>
      )}
    </div>
  );
}

function Row({ metric, axiom, pe, ratio }: { metric: string; axiom: string; pe: string; ratio: string }) {
  return (
    <tr className="border-t border-rule">
      <td className="px-3 py-2 font-sans text-ink">{metric}</td>
      <td className="px-3 py-2 text-right text-ink">{axiom}</td>
      <td className="px-3 py-2 text-right text-ink-secondary">{pe}</td>
      <td className="px-3 py-2 text-right text-accent">{ratio}</td>
    </tr>
  );
}

function decileSubtitle(programId: ProgramId): string {
  if (programId === "co-snap")
    return "Households grouped by weighted decile of gross annual income. D1 = lowest income, D10 = highest.";
  return "Tax units grouped by weighted decile of AGI. D1 = lowest income, D10 = highest.";
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
