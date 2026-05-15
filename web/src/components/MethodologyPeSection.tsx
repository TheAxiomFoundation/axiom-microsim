"use client";

import { useState } from "react";

import { fmtCount, fmtCurrency } from "@/lib/format";

type ProgramId = "federal-ctc" | "federal-income-tax" | "co-snap";

interface PeResult {
  pe_total: number;
  pe_n_units: number;
  pe_weighted_filers: number;
  pe_weighted_total: number;
  pe_avg_per_filer: number;
  elapsed_seconds: number;
}

interface RunState {
  result: PeResult | null;
  running: boolean;
  startedAt: number | null;
  error: string | null;
}
const initial: RunState = { result: null, running: false, startedAt: null, error: null };

interface ScopeSpec {
  id: ProgramId;
  label: string;
  state: string;
  axiomLabel: string;
  peLabel: string;
  axiomNote: string;
}

const SCOPES: ScopeSpec[] = [
  {
    id: "federal-ctc",
    label: "Federal CTC · nationwide",
    state: "US",
    axiomLabel: "ctc_maximum_before_phase_out_under_subsection_h",
    peLabel: "ctc_value (post phase-out)",
    axiomNote:
      "Axiom shows max BEFORE phase-out (no AGI-based reduction yet); PE shows the actual credit allowed (post phase-out, post refundable cap).",
  },
  {
    id: "federal-income-tax",
    label: "Federal income tax · nationwide",
    state: "US",
    axiomLabel: "income_tax_main_rates",
    peLabel: "income_tax_main_rates",
    axiomNote:
      "Both compute the same §1(j) ordinary brackets variable; differences come from filing-status heuristic and our v1 AGI proxy.",
  },
  {
    id: "co-snap",
    label: "Colorado SNAP",
    state: "CO",
    axiomLabel: "snap_allotment (× 12 for annual)",
    peLabel: "snap (monthly × 12)",
    axiomNote:
      "Axiom assumes universal citizenship + utility costs (over-eligibility). Documented as v2 gaps.",
  },
];

export function MethodologyPeSection() {
  return (
    <div className="space-y-6">
      <p className="editorial">
        Each row below runs PolicyEngine fresh against the same{" "}
        <code className="font-mono text-[0.85em] text-accent">
          enhanced_cps_2024
        </code>{" "}
        dataset (uprated to 2026), aggregating the same variable Axiom
        outputs. No caching — every click re-runs PE through{" "}
        <code className="font-mono text-[0.85em] text-accent">
          policyengine_us.Microsimulation
        </code>
        . First click in a session is slowest (PE warms its data dir);
        subsequent clicks land in seconds.
      </p>
      {SCOPES.map((s) => (
        <PeRow key={s.id} scope={s} />
      ))}
      <p className="text-xs text-ink-muted">
        For the live runner with reform sliders, see{" "}
        <a href="/" className="text-accent underline">/</a>
        .
      </p>
    </div>
  );
}

function PeRow({ scope }: { scope: ScopeSpec }) {
  const [run, setRun] = useState<RunState>(initial);
  const [now, setNow] = useState(Date.now());
  const elapsed = run.startedAt ? (now - run.startedAt) / 1000 : null;

  // Tick during loading.
  useTickWhile(run.running, () => setNow(Date.now()));

  const onClick = async () => {
    const startedAt = Date.now();
    setRun({ result: null, running: true, startedAt, error: null });
    try {
      const r = await fetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ program: scope.id, state: scope.state, year: 2026 }),
      });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const data = (await r.json()) as PeResult;
      setRun({ result: data, running: false, startedAt: null, error: null });
    } catch (e) {
      setRun({
        result: null,
        running: false,
        startedAt: null,
        error: String((e as Error).message ?? e),
      });
    }
  };

  return (
    <div className="rounded-md border border-rule bg-paper-elev p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h3 className="font-serif text-lg text-ink">{scope.label}</h3>
          <div className="mt-0.5 font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
            Axiom: {scope.axiomLabel} · PE: {scope.peLabel}
          </div>
        </div>
        <button
          onClick={onClick}
          disabled={run.running}
          className={`rounded-sm px-3 py-2 font-mono text-xs uppercase tracking-eyebrow transition ${
            run.running
              ? "cursor-wait bg-accent-hover text-white"
              : "bg-accent text-white hover:bg-accent-hover"
          }`}
        >
          {run.running
            ? `Running PE… ${elapsed?.toFixed(1)}s`
            : run.result
              ? "▶ Re-run"
              : "▶ Run PE"}
        </button>
      </div>

      <p className="mb-3 text-xs text-ink-muted">{scope.axiomNote}</p>

      {run.error && (
        <div className="mb-3 rounded-sm border border-error bg-paper p-3 text-sm text-error">
          {run.error}
        </div>
      )}

      {run.result && (
        <div className="overflow-hidden rounded-sm border border-rule">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-rule-subtle font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
              <tr>
                <th className="px-3 py-2 text-left">PE metric</th>
                <th className="px-3 py-2 text-right">Value</th>
              </tr>
            </thead>
            <tbody className="font-mono text-sm">
              <Row metric="Total" value={fmtCurrency(run.result.pe_total)} />
              <Row metric="Units in dataset" value={fmtCount(run.result.pe_n_units)} />
              <Row
                metric="Weighted units affected"
                value={fmtCount(run.result.pe_weighted_filers)}
              />
              <Row
                metric="Avg per affected unit"
                value={fmtCurrency(run.result.pe_avg_per_filer)}
              />
              <Row
                metric="Wall-clock"
                value={`${run.result.elapsed_seconds.toFixed(1)} s`}
              />
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Row({ metric, value }: { metric: string; value: string }) {
  return (
    <tr className="border-t border-rule">
      <td className="px-3 py-2 font-sans text-ink">{metric}</td>
      <td className="px-3 py-2 text-right text-ink">{value}</td>
    </tr>
  );
}

function useTickWhile(active: boolean, tick: () => void) {
  // Trigger a render every 200ms while active so the elapsed counter advances.
  if (typeof window === "undefined") return;
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { useEffect } = require("react") as typeof import("react");
  useEffect(() => {
    if (!active) return;
    const id = setInterval(tick, 200);
    return () => clearInterval(id);
  }, [active, tick]);
}
