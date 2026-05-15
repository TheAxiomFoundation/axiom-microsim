"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { DecileChart } from "@/components/DecileChart";
import { StatCard } from "@/components/StatCard";
import { WinnersLosers } from "@/components/WinnersLosers";
import { PROGRAMS, programById, type ProgramId } from "@/lib/levers";
import { fmtCount, fmtCurrency } from "@/lib/format";
import type { MicrosimRequest, MicrosimResponse } from "@/lib/types";

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
  const [programId, setProgramId] = useState<ProgramId>("federal-income-tax");
  const program = useMemo(() => programById(programId), [programId]);
  const [state, setState] = useState<string>(program.default_state);
  const [draft, setDraft] = useState<Record<string, number>>(() => initialMultipliers(programId));
  const [applied, setApplied] = useState<Record<string, number>>(() => initialMultipliers(programId));
  const [baseline, setBaseline] = useState<RunState>(initial);
  const [reform, setReform] = useState<RunState>(initial);
  const [now, setNow] = useState(Date.now());

  // When the program changes, reset state, sliders, and runs.
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
          ? program.levers.flatMap((l) => (multipliers[l.id] === 1 ? [] : l.build(multipliers[l.id])))
          : [];

      const startedAt = Date.now();
      setter((prev) => ({ ...prev, loadingMs: 0, startedAt, error: null }));

      const body: MicrosimRequest = {
        program: programId,
        state,
        year: YEAR,
        overrides,
      };
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
        setter({ data: null, loadingMs: null, startedAt: null, error: String((e as Error).message ?? e) });
      }
    },
    [programId, state, program.levers],
  );

  // Run baseline whenever program/state changes.
  useEffect(() => {
    void runMicrosim("baseline", initialMultipliers(programId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programId, state]);

  const onRunReform = () => {
    if (!draftReforming) return;
    void runMicrosim("reform", { ...draft });
  };
  const onResetSliders = () => setDraft(initialMultipliers(programId));
  const onClearReform = () => {
    setReform(initial);
    setApplied(initialMultipliers(programId));
  };

  const baselineRunning = baseline.startedAt !== null;
  const reformRunning = reform.startedAt !== null;

  // Build per-program copy.
  const isTax = programId === "federal-income-tax";

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <header className="mb-10 border-b border-rule pb-8">
        <div className="flex items-center gap-2 font-mono text-[0.7rem] uppercase tracking-eyebrow text-accent">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent" />
          axiom-microsim · FY {YEAR}
        </div>
        <h1 className="mt-4 font-serif text-[2.6rem] leading-[1.1] tracking-tight text-ink">
          {isTax
            ? "Federal income tax, on Axiom rules,"
            : "Colorado SNAP, on Axiom rules,"}
          <br />
          on Enhanced CPS.
        </h1>
        <p className="editorial mt-5 max-w-3xl">{program.blurb}</p>

        {/* Program switcher */}
        <div className="mt-6 flex items-center gap-2">
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
              <span className="ml-4 font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
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

      <div className="grid gap-8 lg:grid-cols-[360px,1fr]">
        <aside className="space-y-7 rounded-md border border-rule bg-paper-elev p-6">
          <div>
            <h2 className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
              Reform parameters
            </h2>
            <p className="mt-2 text-xs text-ink-muted">
              Adjust a slider, then run the reform. Sliders that differ from
              the last run are highlighted.
            </p>
          </div>

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
                    {changed && (
                      <span className="ml-1 text-ink-muted">
                        (last: {(a * 100).toFixed(0)}%)
                      </span>
                    )}
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
                <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
                  baseline · {l.baseline_label}
                </div>
              </div>
            );
          })}

          <div className="space-y-2 border-t border-rule pt-5">
            <button
              onClick={onRunReform}
              disabled={!draftReforming || reformRunning || !dirty}
              className={`group w-full rounded-sm px-4 py-2.5 text-sm font-semibold uppercase tracking-eyebrow transition ${
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
                onClick={onResetSliders}
                disabled={!draftReforming}
                className="rounded-sm border border-rule px-3 py-1.5 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted disabled:hover:bg-transparent"
              >
                Reset sliders
              </button>
              <button
                onClick={onClearReform}
                disabled={!appliedReforming}
                className="rounded-sm border border-rule px-3 py-1.5 text-xs text-ink-secondary hover:bg-rule-subtle disabled:cursor-not-allowed disabled:text-ink-muted disabled:hover:bg-transparent"
              >
                Clear reform
              </button>
            </div>
          </div>

          <div className="space-y-1.5 rounded-sm bg-rule-subtle p-3 font-mono text-[0.7rem] tracking-wide text-ink-secondary">
            <div className="uppercase tracking-eyebrow text-ink-muted">Status</div>
            {baselineRunning && (
              <div>baseline · running… {((now - (baseline.startedAt ?? now)) / 1000).toFixed(1)}s</div>
            )}
            {!baselineRunning && baseline.loadingMs !== null && (
              <div>baseline · {(baseline.loadingMs / 1000).toFixed(2)}s</div>
            )}
            {reformRunning && (
              <div>reform · running… {((now - (reform.startedAt ?? now)) / 1000).toFixed(1)}s</div>
            )}
            {!reformRunning && reform.loadingMs !== null && (
              <div>reform · {(reform.loadingMs / 1000).toFixed(2)}s</div>
            )}
            {baseline.data && (
              <div className="pt-1 text-ink-muted">
                {baseline.data.n_households_sampled.toLocaleString()}{" "}
                {isTax ? "tax units" : "hh"} · weighted to{" "}
                {fmtCount(baseline.data.households_total_weighted)}
              </div>
            )}
          </div>
        </aside>

        <section className="space-y-8">
          {(baseline.error || reform.error) && (
            <div className="rounded-sm border border-error bg-paper-elev p-3 text-sm text-error">
              {baseline.error && <div>Baseline error: {baseline.error}</div>}
              {reform.error && <div>Reform error: {reform.error}</div>}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard
              label={`Baseline ${program.headline_label.toLowerCase()}`}
              value={baseline.data ? fmtCurrency(baseline.data.baseline.annual_cost) : "—"}
            />
            <StatCard
              label={`Reform ${program.headline_label.toLowerCase()}`}
              value={
                reform.data?.reform
                  ? fmtCurrency(reform.data.reform.reform_annual_cost)
                  : "—"
              }
              delta={
                reform.data?.reform
                  ? {
                      value: `${reform.data.reform.delta_annual_cost >= 0 ? "+" : ""}${fmtCurrency(
                        reform.data.reform.delta_annual_cost,
                      )}`,
                      // For SNAP: more cost = government spending more = ambiguous.
                      // For tax: more revenue = government collecting more.
                      positive:
                        reform.data.reform.delta_annual_cost === 0
                          ? null
                          : reform.data.reform.delta_annual_cost > 0,
                    }
                  : undefined
              }
              hint={!reform.data?.reform ? "no reform run yet" : undefined}
            />
            <StatCard
              label={isTax ? "Tax units w/ liability" : "Households w/ benefit"}
              value={
                baseline.data
                  ? fmtCount(baseline.data.baseline.households_with_benefit)
                  : "—"
              }
              hint="baseline"
            />
            <StatCard
              label={isTax ? "Avg annual tax" : "Avg monthly benefit"}
              value={
                baseline.data
                  ? fmtCurrency(baseline.data.baseline.average_monthly_benefit)
                  : "—"
              }
              hint={isTax ? "baseline · per filer" : "baseline · per recipient"}
            />
          </div>

          <div className="rounded-md border border-rule bg-paper-elev p-6">
            <div className="mb-1 flex items-baseline justify-between">
              <h3 className="font-serif text-lg text-ink">
                {isTax
                  ? "Mean tax liability by liability decile"
                  : "Mean SNAP allotment by household income decile"}
              </h3>
              <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
                baseline
              </span>
            </div>
            <p className="mb-4 text-xs text-ink-muted">
              {isTax
                ? "Tax units grouped by weighted decile of their income tax liability. D10 = top payers."
                : "Households grouped by weighted decile of gross annual income."}
            </p>
            {baseline.data && (
              <DecileChart bins={baseline.data.baseline.decile_distribution} />
            )}
          </div>

          <div className="rounded-md border border-rule bg-paper-elev p-6">
            <div className="mb-1 flex items-baseline justify-between">
              <h3 className="font-serif text-lg text-ink">
                Reform impact on {isTax ? "tax units" : "households"}
              </h3>
              <span className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
                vs baseline
              </span>
            </div>
            <p className="mb-4 text-xs text-ink-muted">
              {isTax
                ? "Per-tax-unit annual tax change against baseline, weighted to the population. Pay-less = winners."
                : "Per-household monthly benefit change against baseline."}
            </p>
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
          </div>

          <footer className="pt-4 text-xs text-ink-muted">
            ECPS sample: {baseline.data?.n_households_sampled.toLocaleString() ?? "—"}{" "}
            {isTax ? "tax units" : "households"} ·{" "}
            {baseline.data?.n_persons_sampled.toLocaleString() ?? "—"} persons ·{" "}
            <code className="font-mono text-[0.72rem]">enhanced_cps_2024.h5</code>.
            Engine: <code className="font-mono text-[0.72rem]">axiom-rules-engine</code>{" "}
            via execute-compiled. {isTax && "v1 ordinary brackets only — excludes capital gains, AMT, and credits."}
          </footer>
        </section>
      </div>
    </main>
  );
}
