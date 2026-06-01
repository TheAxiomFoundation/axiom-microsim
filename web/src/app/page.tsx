"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DecileChart } from "@/components/DecileChart";
import { DecileImpactChart } from "@/components/DecileImpactChart";
import { WinnersLosers } from "@/components/WinnersLosers";
import { PROGRAMS, programById, type Lever, type ProgramId } from "@/lib/levers";
import { fmtCount, fmtCurrency } from "@/lib/format";
import type { MicrosimRequest, MicrosimResponse } from "@/lib/types";

const cacheKey = (programId: string, state: string, year: number) =>
  `${programId}|${state}|${year}`;

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
  baselineTotal: number | null;
  filers: number | null;
  avg: number | null;
  loadingMs: number | null;
  startedAt: number | null;
  error: string | null;
}
const peInitial: PeState = {
  total: null, baselineTotal: null, filers: null, avg: null,
  loadingMs: null, startedAt: null, error: null,
};

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
  const [runPeSideBySide, setRunPeSideBySide] = useState(false);
  const [now, setNow] = useState(Date.now());

  // Session caches keyed by `program|state|year`. Switching programs or
  // scopes is now free if we've already computed that combination.
  // Refs (not state) so writes don't trigger renders.
  const baselineCache = useRef<Map<string, MicrosimResponse>>(new Map());
  const peCache = useRef<Map<string, { total: number; baselineTotal: number | null; filers: number; avg: number; loadingMs: number }>>(
    new Map(),
  );
  // Reform-PE cache keyed by (program|state|year|appliedOverridesHash).
  const peReformCache = useRef<Map<string, { total: number; baselineTotal: number | null; filers: number; avg: number; loadingMs: number }>>(
    new Map(),
  );

  useEffect(() => {
    setState(program.default_state);
    setDraft(initialDraft(programId));
    setApplied(initialDraft(programId));
    setReform(initial);
    setPeReform(peInitial);
    const k = cacheKey(programId, program.default_state, YEAR);
    const cb = baselineCache.current.get(k);
    setBaseline(cb ? { data: cb, loadingMs: 0, startedAt: null, error: null } : initial);
    const cp = peCache.current.get(k);
    setPe(cp ? { ...cp, startedAt: null, error: null } : peInitial);
  }, [programId, program.default_state]);

  useEffect(() => {
    setDraft(initialDraft(programId));
    setApplied(initialDraft(programId));
    setReform(initial);
    setPeReform(peInitial);
    const k = cacheKey(programId, state, YEAR);
    const cb = baselineCache.current.get(k);
    setBaseline(cb ? { data: cb, loadingMs: 0, startedAt: null, error: null } : initial);
    const cp = peCache.current.get(k);
    setPe(cp ? { ...cp, startedAt: null, error: null } : peInitial);
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
      pe.startedAt === null &&
      peReform.startedAt === null
    ) return;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [baseline.startedAt, reform.startedAt, pe.startedAt, peReform.startedAt]);

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
        if (kind === "baseline") {
          baselineCache.current.set(cacheKey(programId, state, YEAR), data);
        }
        if (kind === "reform") setApplied(values);
      } catch (e) {
        setter({
          data: null, loadingMs: null, startedAt: null,
          error: String((e as Error).message ?? e),
        });
      }
    },
    [programId, state, program.levers],
  );

  const buildPeOverrides = useCallback((values: Record<string, number>) => {
    const out: { path: string; value: number }[] = [];
    for (const l of program.levers) {
      const v = values[l.id];
      if (v === undefined || v === l.baseline) continue;
      if (!l.peBuild) continue;
      out.push(...l.peBuild(v));
    }
    return out.length ? out : null;
  }, [program.levers]);

  /** Build PE overrides from the currently-applied lever values, using
   *  each lever's optional peBuild translation. Returns null if no
   *  reform is applied or no lever has a PE mapping. */
  const peReformOverrides = useMemo(() => buildPeOverrides(applied), [applied, buildPeOverrides]);

  const reformCacheKey = useMemo(() => {
    if (!peReformOverrides) return null;
    return cacheKey(programId, state, YEAR) + "|" + JSON.stringify(peReformOverrides);
  }, [programId, state, peReformOverrides]);

  const runPeReform = useCallback(async (values: Record<string, number> = applied) => {
    const overrides = buildPeOverrides(values);
    if (!overrides) {
      setPeReform({
        total: null, baselineTotal: null, filers: null, avg: null,
        loadingMs: null, startedAt: null,
        error: "No PolicyEngine mapping is defined for the moved sliders.",
      });
      return;
    }
    const key = cacheKey(programId, state, YEAR) + "|" + JSON.stringify(overrides);
    const startedAt = Date.now();
    setPeReform({ total: null, baselineTotal: null, filers: null, avg: null, loadingMs: 0, startedAt, error: null });
    try {
      const r = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          program: programId, state, year: YEAR, overrides,
        }),
      });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const data = await r.json();
      const result = {
        total: data.pe_total as number,
        baselineTotal: (data.pe_reform?.baseline_annual_cost ?? null) as number | null,
        filers: data.pe_weighted_filers as number,
        avg: data.pe_avg_per_filer as number,
        loadingMs: Date.now() - startedAt,
      };
      peReformCache.current.set(key, result);
      setPeReform({ ...result, startedAt: null, error: null });
    } catch (e) {
      setPeReform({
        total: null, baselineTotal: null, filers: null, avg: null,
        loadingMs: null, startedAt: null,
        error: String((e as Error).message ?? e),
      });
    }
  }, [applied, buildPeOverrides, programId, state]);

  // Hydrate reform-PE from cache when applied changes.
  useEffect(() => {
    if (!reformCacheKey) {
      setPeReform(peInitial);
      return;
    }
    const cached = peReformCache.current.get(reformCacheKey);
    setPeReform(
      cached ? { ...cached, startedAt: null, error: null } : peInitial,
    );
  }, [reformCacheKey]);

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
      const peResult = {
        total: data.pe_total as number,
        baselineTotal: (data.pe_baseline?.annual_cost ?? data.pe_total ?? null) as number | null,
        filers: data.pe_weighted_filers as number,
        avg: data.pe_avg_per_filer as number,
        loadingMs: Date.now() - startedAt,
      };
      peCache.current.set(cacheKey(programId, state, YEAR), peResult);
      setPe({ ...peResult, startedAt: null, error: null });
    } catch (e) {
      setPe({
        total: null, baselineTotal: null, filers: null, avg: null,
        loadingMs: null, startedAt: null,
        error: String((e as Error).message ?? e),
      });
    }
  }, [programId, state]);

  const onRunBaseline = () => {
    if (!program.state_choices.includes(state)) return;
    void runMicrosim("baseline", initialDraft(programId));
    if (runPeSideBySide) void runPe();
  };

  const onRunReform = () => {
    if (!draftReforming) return;
    void runMicrosim("reform", { ...draft });
    if (runPeSideBySide) void runPeReform({ ...draft });
  };

  const baselineRunning = baseline.startedAt !== null;
  const reformRunning = reform.startedAt !== null;
  const peRunning = pe.startedAt !== null;
  const peReformRunning = peReform.startedAt !== null;
  const reformDelta = reform.data?.reform?.delta_annual_cost ?? null;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      {/* ---- Title + program/scope switcher ---- */}
      <header className="mb-8 border-b border-rule pb-6">
        <h1 className="font-serif text-[2.4rem] leading-[1.1] tracking-tight text-ink">
          {program.name}.
        </h1>
        {program.blurb && <p className="editorial mt-4 max-w-3xl">{program.blurb}</p>}

        <div className="mt-5 flex flex-wrap items-center gap-3">
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

          <label className="ml-auto inline-flex items-center gap-2 rounded-sm border border-rule bg-paper-elev px-3 py-1.5 text-xs text-ink-secondary">
            <input
              type="checkbox"
              checked={runPeSideBySide}
              onChange={(e) => setRunPeSideBySide(e.target.checked)}
              className="h-3.5 w-3.5 accent-accent"
            />
            Run PE side-by-side
          </label>
        </div>
      </header>

      {/* ===========================================================
          1 — BASELINE
          =========================================================== */}
      <SectionHeading number="01" title={`Baseline · FY ${YEAR}`} />

      <section className="mb-12 space-y-6">
        {baseline.error && (
          <ErrorBox>Baseline error: {baseline.error}</ErrorBox>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={onRunBaseline}
            disabled={baselineRunning}
            className={`rounded-sm px-5 py-2.5 text-sm font-semibold uppercase tracking-eyebrow transition ${
              baselineRunning
                ? "cursor-wait bg-accent-hover text-white"
                : "bg-accent text-white hover:bg-accent-hover"
            }`}
          >
            {baselineRunning
              ? `Running… ${((now - (baseline.startedAt ?? now)) / 1000).toFixed(1)}s`
              : baseline.data ? "↻ Re-run baseline" : "▶ Run baseline"}
          </button>
          {runPeSideBySide && (
            <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
              PE will run with baseline
            </span>
          )}
        </div>

        <BigStat
          eyebrow={program.headline_label}
          value={baseline.data ? fmtCurrency(baseline.data.baseline.annual_cost) : "—"}
          loading={baselineRunning}
          loadingElapsed={baselineRunning ? (now - (baseline.startedAt ?? now)) / 1000 : null}
          subRows={
            baseline.data
              ? [
                  {
                    label: programId === "co-snap" ? "Households w/ benefit" : "Tax units affected",
                    value: fmtCount(baseline.data.baseline.households_with_benefit),
                  },
                  {
                    label: programId === "co-snap" ? "Avg monthly benefit" : programId === "federal-ctc" ? "Avg credit per recipient" : "Avg per filer",
                    value: fmtCurrency(baseline.data.baseline.average_monthly_benefit),
                  },
                  {
                    label: "Sample / weighted",
                    value: `${baseline.data.n_households_sampled.toLocaleString()} / ${fmtCount(baseline.data.households_total_weighted)}`,
                  },
                ]
              : []
          }
        />

        <Card title="Distribution by income decile" subtitle={decileSubtitle(programId)}>
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
              Run the baseline to load the distribution.
            </div>
          )}
        </Card>

        {(runPeSideBySide || pe.total != null || pe.error || peRunning) && (
          <PePanel
            pe={pe}
            running={peRunning}
            elapsed={peRunning ? (now - (pe.startedAt ?? now)) / 1000 : null}
            axiomBaseline={baseline.data?.baseline.annual_cost}
            axiomFilers={baseline.data?.baseline.households_with_benefit}
            axiomAvg={baseline.data?.baseline.average_monthly_benefit}
            programId={programId}
          />
        )}
      </section>

      {/* ===========================================================
          2 — REFORM CONTROLS
          =========================================================== */}
      <SectionHeading
        number="02"
        title="Reform"
        subtitle="Adjust parameters, then run the patched program over the same population."
      />

      <section className="mb-12">
        <div className="rounded-md border border-rule bg-paper-elev p-6">
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {program.levers.map((l) => (
              <LeverControl
                key={l.id}
                lever={l}
                value={draft[l.id] ?? l.baseline}
                applied={applied[l.id] ?? l.baseline}
                onChange={(v) => setDraft((prev) => ({ ...prev, [l.id]: v }))}
              />
            ))}
          </div>

          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-5">
            <div className="flex items-center gap-2">
              <button
                onClick={onRunReform}
                disabled={!draftReforming || reformRunning || !dirty}
                className={`rounded-sm px-5 py-2.5 text-sm font-semibold uppercase tracking-eyebrow transition ${
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
                    ? "Move a slider to enable"
                    : !dirty
                      ? "Reform up to date"
                      : "▶ Run reform"}
              </button>
              {runPeSideBySide && draftReforming && (
                <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
                  PE will run with reform
                </span>
              )}
              <button
                onClick={() => setDraft(initialDraft(programId))}
                disabled={!draftReforming}
                className="rounded-sm border border-rule px-3 py-2 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted"
              >
                Reset sliders
              </button>
            </div>

            {dirty && draftReforming && !reformRunning && (
              <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-warning">
                ⚠ unsaved · click run reform
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ===========================================================
          3 — REFORM OUTPUT
          =========================================================== */}
      <SectionHeading
        number="03"
        title="Reform impact"
      />

      <section className="mb-8 space-y-6">
        {reform.error && (
          <ErrorBox>Reform error: {reform.error}</ErrorBox>
        )}

        {!reform.data?.reform && !reformRunning && (
          <div className="rounded-md border border-dashed border-rule bg-paper-elev p-8 text-center text-sm text-ink-muted">
            Adjust a slider above and click <strong className="text-ink">▶ Run reform</strong>.
          </div>
        )}

        {(reform.data?.reform || reformRunning) && (
          <>
            <BigStat
              eyebrow="Change vs baseline"
              value={
                reform.data?.reform
                  ? fmtSignedCurrency(reform.data.reform.delta_annual_cost)
                  : "—"
              }
              loading={reformRunning}
              loadingElapsed={reformRunning ? (now - (reform.startedAt ?? now)) / 1000 : null}
              accent={
                reformDelta == null
                  ? undefined
                  : reformDelta > 0
                    ? "text-error"
                    : reformDelta < 0
                      ? "text-success"
                      : undefined
              }
              subRows={
                reform.data?.reform
                  ? [
                      {
                        label: "Reform total",
                        value: fmtCurrency(reform.data.reform.reform_annual_cost),
                      },
                      {
                        label: "Baseline total",
                        value: fmtCurrency(reform.data.reform.baseline_annual_cost),
                      },
                      {
                        label: "% change",
                        value:
                          reform.data.reform.baseline_annual_cost !== 0
                            ? `${((reform.data.reform.delta_annual_cost / reform.data.reform.baseline_annual_cost) * 100).toFixed(1)}%`
                            : "—",
                      },
                    ]
                  : []
              }
            />

            {reform.data?.reform?.decile_impact && reform.data.reform.decile_impact.length > 0 && (
              <Card
                title="Mean change by income decile"
                subtitle={
                  programId === "co-snap"
                    ? "Households grouped by gross income decile. Bars show mean monthly benefit change."
                    : "Tax units grouped by AGI decile. Bars show mean annual change in liability/credit."
                }
              >
                <DecileImpactChart
                  bins={reform.data.reform.decile_impact}
                  metricLabel={
                    programId === "co-snap" ? "Mean monthly Δ" : "Mean annual Δ"
                  }
                  metricSuffix={programId === "co-snap" ? "/mo" : "/yr"}
                />
              </Card>
            )}

            <Card
              title="Winners and losers"
              subtitle={
                programId === "co-snap"
                  ? "Households whose monthly benefit goes up vs down under the reform."
                  : "Tax units whose liability or credit changes under the reform."
              }
            >
              {reform.data?.reform ? (
                <WinnersLosers
                  reform={reform.data.reform}
                  winnersLabel={program.winners_label}
                  losersLabel={program.losers_label}
                  unitLabel={programId === "co-snap" ? "households" : "tax units"}
                />
              ) : (
                <div className="py-2 text-sm text-ink-muted">computing…</div>
              )}
            </Card>

            {/* PE comparison for the reform — only if any applied lever
                has a PE translation. */}
            {appliedReforming && (runPeSideBySide || peReform.total != null || peReform.error || peReformRunning) && (
              <PeReformPanel
                pe={peReform}
                running={peReformRunning}
                elapsed={peReformRunning ? (now - (peReform.startedAt ?? now)) / 1000 : null}
                hasMapping={peReformOverrides != null}
                axiomReform={reform.data?.reform?.reform_annual_cost}
                axiomBaseline={reform.data?.reform?.baseline_annual_cost}
                peBaseline={peReform.baselineTotal ?? pe.total}
                programId={programId}
              />
            )}
          </>
        )}
      </section>

      <footer className="border-t border-rule pt-4 text-xs text-ink-muted">
        ECPS sample: {baseline.data?.n_households_sampled.toLocaleString() ?? "—"}{" "}
        {programId === "co-snap" ? "households" : "tax units"} ·{" "}
        {baseline.data?.n_persons_sampled.toLocaleString() ?? "—"} persons ·{" "}
        <code className="font-mono">enhanced_cps_2024.h5</code> · engine{" "}
        <code className="font-mono">axiom-rules-engine</code>. See{" "}
        <a href="/methodology" className="text-accent underline">/methodology</a>{" "}
        for slot mappings, calculations, and limitations.
      </footer>
    </main>
  );
}


// --- pieces -----------------------------------------------------------------

function SectionHeading({
  number,
  title,
  subtitle,
}: {
  number: string;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-[0.7rem] uppercase tracking-eyebrow text-accent">
          § {number}
        </span>
        <h2 className="font-serif text-2xl text-ink">{title}</h2>
      </div>
      {subtitle && <p className="mt-1 text-sm text-ink-secondary">{subtitle}</p>}
    </div>
  );
}

function BigStat({
  eyebrow,
  value,
  loading,
  loadingElapsed,
  accent,
  subRows,
}: {
  eyebrow: string;
  value: string;
  loading: boolean;
  loadingElapsed: number | null;
  accent?: string;
  subRows?: { label: string; value: string }[];
}) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-6">
      <div className="font-mono text-[0.7rem] uppercase tracking-eyebrow text-ink-muted">
        {eyebrow}
      </div>
      <div className={`mt-3 font-serif text-[3.5rem] leading-none tracking-tight ${accent ?? "text-ink"}`}>
        {loading ? (
          <span className="font-mono text-2xl text-ink-muted">
            running… {loadingElapsed?.toFixed(1)}s
          </span>
        ) : value}
      </div>
      {subRows && subRows.length > 0 && (
        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-rule pt-4 md:grid-cols-3">
          {subRows.map((row) => (
            <div key={row.label}>
              <dt className="font-mono text-[0.6rem] uppercase tracking-eyebrow text-ink-muted">
                {row.label}
              </dt>
              <dd className="mt-0.5 font-mono text-sm text-ink">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

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
  const display = isAmount ? fmtCurrency(value) : `${(value * 100).toFixed(0)}%`;
  const appliedDisplay = isAmount ? fmtCurrency(applied) : `${(applied * 100).toFixed(0)}%`;
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
        {changed && <span className="ml-2 text-warning">last run · {appliedDisplay}</span>}
      </div>
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

function ErrorBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-sm border border-error bg-paper-elev p-3 text-sm text-error">
      {children}
    </div>
  );
}

function PePanel({
  pe, running, elapsed,
  axiomBaseline, axiomFilers, axiomAvg, programId,
}: {
  pe: PeState;
  running: boolean;
  elapsed: number | null;
  axiomBaseline?: number;
  axiomFilers?: number;
  axiomAvg?: number;
  programId: ProgramId;
}) {
  const ratio = (a?: number | null, p?: number | null) =>
    a != null && p != null && p !== 0 ? `${((a / p) * 100).toFixed(0)}%` : "—";
  const hasResult = pe.total != null;

  return (
    <div className="rounded-md border border-rule bg-paper-elev p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-accent">
            Side-by-side · PolicyEngine
          </div>
          <div className="mt-0.5 text-sm text-ink-secondary">
            Same dataset, same parameters; runs when PE side-by-side is checked.
          </div>
        </div>
        <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          {running ? `Running PE… ${elapsed?.toFixed(1)}s` : hasResult ? "PE complete" : "PE pending"}
        </div>
      </div>

      {pe.error && <ErrorBox>{pe.error}</ErrorBox>}

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
                metric={
                  programId === "co-snap" ? "Avg monthly benefit"
                    : programId === "federal-ctc" ? "Avg credit per recipient"
                      : "Avg per filer"
                }
                axiom={axiomAvg != null ? fmtCurrency(axiomAvg) : "—"}
                pe={pe.avg != null ? fmtCurrency(pe.avg) : "—"}
                ratio={ratio(axiomAvg, pe.avg)}
              />
            </tbody>
          </table>
        </div>
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

function PeReformPanel({
  pe, running, elapsed, hasMapping,
  axiomReform, axiomBaseline, peBaseline, programId,
}: {
  pe: PeState;
  running: boolean;
  elapsed: number | null;
  hasMapping: boolean;
  axiomReform?: number;
  axiomBaseline?: number;
  peBaseline?: number | null;
  programId: ProgramId;
}) {
  const ratio = (a?: number | null, p?: number | null) =>
    a != null && p != null && p !== 0 ? `${((a / p) * 100).toFixed(0)}%` : "—";
  const peDelta = pe.total != null && peBaseline != null ? pe.total - peBaseline : null;
  const axiomDelta =
    axiomReform != null && axiomBaseline != null ? axiomReform - axiomBaseline : null;
  const hasResult = pe.total != null;

  return (
    <div className="rounded-md border border-rule bg-paper-elev p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-accent">
            Side-by-side · PolicyEngine · with this reform
          </div>
          <div className="mt-0.5 text-sm text-ink-secondary">
            Same parametric reform applied to PE when side-by-side is checked.
          </div>
        </div>
        <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          {!hasMapping
            ? "PE mapping not defined"
            : running
              ? `Running PE… ${elapsed?.toFixed(1)}s`
              : hasResult ? "PE complete" : "PE pending"}
        </div>
      </div>

      {pe.error && <ErrorBox>{pe.error}</ErrorBox>}

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
                metric="Reform total"
                axiom={axiomReform != null ? fmtCurrency(axiomReform) : "—"}
                pe={pe.total != null ? fmtCurrency(pe.total) : "—"}
                ratio={ratio(axiomReform, pe.total)}
              />
              <Row
                metric="Δ vs baseline"
                axiom={axiomDelta != null ? fmtSignedCurrency(axiomDelta) : "—"}
                pe={peDelta != null ? fmtSignedCurrency(peDelta) : "—"}
                ratio={ratio(axiomDelta, peDelta)}
              />
              <Row
                metric={programId === "co-snap" ? "Weighted recipients" : "Weighted units affected"}
                axiom={"—"}
                pe={pe.filers != null ? fmtCount(pe.filers) : "—"}
                ratio={"—"}
              />
            </tbody>
          </table>
        </div>
      )}
    </div>
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
