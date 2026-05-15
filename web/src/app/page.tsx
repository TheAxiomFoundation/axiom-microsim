"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { DecileChart } from "@/components/DecileChart";
import { WinnersLosers } from "@/components/WinnersLosers";
import { PROGRAMS, programById, type ProgramId } from "@/lib/levers";
import { fmtCount, fmtCurrency } from "@/lib/format";
import type {
  MicrosimRequest,
  MicrosimResponse,
  PeComparison,
  PeProgramNumbers,
} from "@/lib/types";

const YEAR = 2026;
const initialMultipliers = (programId: ProgramId): Record<string, number> =>
  Object.fromEntries(programById(programId).levers.map((l) => [l.id, 1]));

interface RunState {
  data: MicrosimResponse | null;
  loadingMs: number | null;
  startedAt: number | null;
  error: string | null;
}
const initial: RunState = { data: null, loadingMs: null, startedAt: null, error: null };

export default function Page() {
  const [programId, setProgramId] = useState<ProgramId>("federal-ctc");
  const program = useMemo(() => programById(programId), [programId]);
  const [state, setState] = useState<string>(program.default_state);
  const [draft, setDraft] = useState<Record<string, number>>(() => initialMultipliers(programId));
  const [applied, setApplied] = useState<Record<string, number>>(() => initialMultipliers(programId));
  const [baseline, setBaseline] = useState<RunState>(initial);
  const [reform, setReform] = useState<RunState>(initial);
  const [now, setNow] = useState(Date.now());
  const [peData, setPeData] = useState<PeComparison | null>(null);

  // Load the cached PE comparison once on mount.
  useEffect(() => {
    fetch("/comparison.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setPeData(d as PeComparison | null))
      .catch(() => setPeData(null));
  }, []);

  const peNumbers = useMemo<PeProgramNumbers | null>(() => {
    if (!peData) return null;
    if (programId === "federal-income-tax") return peData.federal_income_tax;
    if (programId === "co-snap") return peData.co_snap;
    if (programId === "federal-ctc") return peData.federal_ctc;
    return null;
  }, [peData, programId]);

  // Reset state when program changes.
  useEffect(() => {
    setState(program.default_state);
    setDraft(initialMultipliers(programId));
    setApplied(initialMultipliers(programId));
    setBaseline(initial);
    setReform(initial);
  }, [programId, program.default_state]);

  const dirty = useMemo(
    () => program.levers.some((l) => draft[l.id] !== applied[l.id]),
    [draft, applied, program.levers],
  );
  const draftReforming = useMemo(
    () => program.levers.some((l) => draft[l.id] !== 1),
    [draft, program.levers],
  );
  const appliedReforming = useMemo(
    () => program.levers.some((l) => applied[l.id] !== 1),
    [applied, program.levers],
  );

  useEffect(() => {
    if (baseline.startedAt === null && reform.startedAt === null) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [baseline.startedAt, reform.startedAt]);

  const runMicrosim = useCallback(
    async (kind: "baseline" | "reform", multipliers: Record<string, number>) => {
      const setter = kind === "baseline" ? setBaseline : setReform;
      const overrides =
        kind === "reform"
          ? program.levers.flatMap((l) =>
              multipliers[l.id] === 1 ? [] : l.build(multipliers[l.id]),
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
        if (kind === "reform") setApplied(multipliers);
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

  useEffect(() => {
    void runMicrosim("baseline", initialMultipliers(programId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programId, state]);

  const onRunReform = () => {
    if (!draftReforming) return;
    void runMicrosim("reform", { ...draft });
  };

  const baselineRunning = baseline.startedAt !== null;
  const reformRunning = reform.startedAt !== null;

  // PE accessors.
  const peHeadline = pePrimaryValue(peNumbers, programId);
  const peHeadlineRatio =
    peHeadline != null && baseline.data
      ? `${((baseline.data.baseline.annual_cost / peHeadline) * 100).toFixed(0)}%`
      : null;

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

      {/* PE side-by-side panel — always visible */}
      <PeComparisonPanel
        pe={peNumbers}
        peData={peData}
        programId={programId}
        axiomBaseline={baseline.data?.baseline.annual_cost}
        axiomFilers={baseline.data?.baseline.households_with_benefit}
        axiomAvg={baseline.data?.baseline.average_monthly_benefit}
      />

      <div className="grid gap-6 lg:grid-cols-[340px,1fr]">
        {/* Sidebar: levers */}
        <aside className="space-y-5 rounded-md border border-rule bg-paper-elev p-5">
          <h2 className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
            Reform parameters
          </h2>

          {program.levers.map((l) => {
            const m = draft[l.id];
            const a = applied[l.id];
            const changed = m !== a;
            return (
              <div key={l.id} className="space-y-2">
                <div className="flex items-baseline justify-between gap-3">
                  <label className="text-sm font-medium text-ink">{l.label}</label>
                  <span
                    className={`font-mono text-xs tabular-nums ${
                      changed ? "font-semibold text-warning" : "text-ink-secondary"
                    }`}
                  >
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
                    setDraft((prev) => ({ ...prev, [l.id]: Number(e.target.value) }))
                  }
                  className="w-full"
                />
                <div className="text-xs text-ink-muted">{l.description}</div>
              </div>
            );
          })}

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
              onClick={() => setDraft(initialMultipliers(programId))}
              disabled={!draftReforming}
              className="rounded-sm border border-rule px-3 py-1.5 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted"
            >
              Reset
            </button>
            <button
              onClick={() => {
                setReform(initial);
                setApplied(initialMultipliers(programId));
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

          <Card title="Distribution by decile" subtitle={decileSubtitle(programId)}>
            {baseline.data && (
              <div className="h-72 w-full">
                <DecileChart bins={baseline.data.baseline.decile_distribution} />
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
            <a href="/methodology" className="text-accent underline">
              /methodology
            </a>{" "}
            for slot mappings, calculations, and limitations.
          </footer>
        </section>
      </div>
    </main>
  );
}


// --- pieces -----------------------------------------------------------------

function Headline({
  eyebrow,
  value,
  sub,
  loading,
  loadingElapsed,
  accent,
}: {
  eyebrow: string;
  value: string;
  sub: string;
  loading: boolean;
  loadingElapsed: number | null;
  accent?: string;
}) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-6">
      <div className="font-mono text-[0.7rem] uppercase tracking-eyebrow text-ink-muted">
        {eyebrow}
      </div>
      <div
        className={`mt-3 font-serif text-[3rem] leading-none tracking-tight ${
          accent ?? "text-ink"
        }`}
      >
        {loading ? (
          <span className="font-mono text-2xl text-ink-muted">
            running… {loadingElapsed?.toFixed(1)}s
          </span>
        ) : (
          value
        )}
      </div>
      <div className="mt-3 text-sm text-ink-secondary">{sub}</div>
    </div>
  );
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-6">
      <h3 className="font-serif text-lg text-ink">{title}</h3>
      {subtitle && <p className="mb-4 text-xs text-ink-muted">{subtitle}</p>}
      {children}
    </div>
  );
}

function PeComparisonPanel({
  pe,
  peData,
  programId,
  axiomBaseline,
  axiomFilers,
  axiomAvg,
}: {
  pe: PeProgramNumbers | null;
  peData: PeComparison | null;
  programId: ProgramId;
  axiomBaseline?: number;
  axiomFilers?: number;
  axiomAvg?: number;
}) {
  if (!peData) {
    return (
      <div className="mb-8 rounded-md border border-dashed border-rule bg-rule-subtle p-4 text-xs text-ink-muted">
        Loading PolicyEngine comparison…
      </div>
    );
  }
  if (!pe) {
    return (
      <div className="mb-8 rounded-md border border-dashed border-rule bg-rule-subtle p-4 text-xs text-ink-muted">
        No PolicyEngine comparison cached for{" "}
        <code className="font-mono">{programId}</code> yet. Run{" "}
        <code className="font-mono">scripts/refresh_pe_comparison.py</code>.
      </div>
    );
  }

  const peTotal =
    programId === "federal-income-tax"
      ? pe.pe_total_revenue
      : programId === "federal-ctc"
        ? (pe as PeProgramNumbers & { pe_total_cost?: number }).pe_total_cost ??
          pe.pe_total_annual_cost
        : pe.pe_total_annual_cost;

  const peFilers =
    programId === "federal-income-tax"
      ? pe.pe_weighted_filers
      : pe.pe_weighted_recipients;

  const peAvg = pe.pe_avg_per_filer;

  const ratioStr = (axiom?: number, p?: number) =>
    axiom != null && p != null && p !== 0
      ? `${((axiom / p) * 100).toFixed(0)}%`
      : "—";

  return (
    <div className="mb-8 rounded-md border border-rule bg-paper-elev p-5">
      <div className="mb-4 flex items-baseline justify-between">
        <div>
          <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-accent">
            Side-by-side · PolicyEngine
          </div>
          <h3 className="mt-1 font-serif text-lg text-ink">
            Same dataset, same scope, same parameter values.
          </h3>
        </div>
        <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          PE precomputed {peData.computed_at.slice(0, 10)}
        </div>
      </div>
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
              pe={peTotal != null ? fmtCurrency(peTotal) : "—"}
              ratio={ratioStr(axiomBaseline, peTotal)}
            />
            <Row
              metric={
                programId === "co-snap"
                  ? "Weighted recipients"
                  : "Weighted units w/ liability or credit"
              }
              axiom={axiomFilers != null ? fmtCount(axiomFilers) : "—"}
              pe={peFilers != null ? fmtCount(peFilers) : "—"}
              ratio={ratioStr(axiomFilers, peFilers)}
            />
            {programId !== "co-snap" && (
              <Row
                metric={programId === "federal-ctc" ? "Avg credit per recipient" : "Avg per filer"}
                axiom={axiomAvg != null ? fmtCurrency(axiomAvg) : "—"}
                pe={peAvg != null ? fmtCurrency(peAvg) : "—"}
                ratio={ratioStr(axiomAvg, peAvg)}
              />
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-ink-muted">
        Axiom vs PE differences come from v1 limitations documented on{" "}
        <a href="/methodology" className="text-accent underline">
          /methodology
        </a>{" "}
        — they are not encoding disagreements. Reform comparisons stay Axiom-only
        (a PE rerun would block ~2 minutes).
      </p>
    </div>
  );
}

function Row({
  metric,
  axiom,
  pe,
  ratio,
}: {
  metric: string;
  axiom: string;
  pe: string;
  ratio: string;
}) {
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
    return "Households grouped by weighted decile of gross annual income.";
  if (programId === "federal-income-tax")
    return "Tax units grouped by decile of their tax liability. D10 = top payers.";
  return "Tax units grouped by decile of their CTC amount. D10 = largest credits.";
}

function pePrimaryValue(pe: PeProgramNumbers | null, programId: ProgramId): number | undefined {
  if (!pe) return undefined;
  if (programId === "federal-income-tax") return pe.pe_total_revenue;
  return pe.pe_total_annual_cost;
}

function fmtSignedCurrency(n: number): string {
  if (n === 0) return "$0";
  const sign = n > 0 ? "+" : "−";
  return `${sign}${fmtCurrency(Math.abs(n))}`;
}
