import fs from "node:fs/promises";
import path from "node:path";

import { ALL_PROGRAMS, type ProgramMethodology } from "@/lib/methodology";

interface ComparisonScope {
  scope: string;
  axiom_output?: string;
  pe_variable?: string;
  axiom_total_revenue?: number;
  pe_total_revenue?: number;
  axiom_n_tax_units?: number;
  pe_n_tax_units?: number;
  axiom_weighted_filers?: number;
  pe_weighted_filers?: number;
  pe_weighted_total?: number;
  axiom_avg_per_filer?: number;
  pe_avg_per_filer?: number;
  axiom_total_annual_cost?: number;
  pe_total_annual_cost?: number;
  axiom_n_households?: number;
  axiom_weighted_recipients?: number;
  axiom_avg_monthly_benefit?: number;
}

interface Comparison {
  computed_at: string;
  year: number;
  dataset: string;
  federal_income_tax: ComparisonScope | null;
  co_snap: ComparisonScope | null;
  errors: string[];
}

async function loadComparison(): Promise<Comparison | null> {
  try {
    const p = path.join(process.cwd(), "public", "comparison.json");
    const text = await fs.readFile(p, "utf-8");
    return JSON.parse(text) as Comparison;
  } catch {
    return null;
  }
}

const fmtUSD = (n: number | undefined): string => {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
};

const fmtCount = (n: number | undefined): string => {
  if (n === undefined || n === null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toFixed(0);
};

const fmtRatio = (axiom: number | undefined, pe: number | undefined): string => {
  if (!axiom || !pe) return "—";
  const ratio = axiom / pe;
  return `${(ratio * 100).toFixed(0)}%`;
};

export default async function MethodologyPage() {
  const comparison = await loadComparison();

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="mb-12 border-b border-rule pb-8">
        <div className="font-mono text-[0.7rem] uppercase tracking-eyebrow text-accent">
          axiom-microsim · methodology
        </div>
        <h1 className="mt-3 font-serif text-[2.6rem] leading-[1.1] tracking-tight text-ink">
          How a household becomes a number.
        </h1>
        <p className="editorial mt-5 max-w-3xl">
          Every aggregate on the previous page is the end of a six-step
          pipeline: read the Enhanced CPS file, project its columns into the
          input contract a RuleSpec module expects, batch every household or
          tax unit into a single engine call, decode the result, and weight
          it back to the population. This page documents what those steps
          actually do — slot by slot — and where the result departs from
          PolicyEngine, run on the same data with the same parameters.
        </p>
      </header>

      <Section eyebrow="01" title="Pipeline">
        <p className="editorial mb-5">
          Both programs share the same skeleton; only the projection
          layer and the program YAML change.
        </p>
        <Pipeline />
        <p className="mt-5 text-sm text-ink-secondary">
          The runtime imports zero PolicyEngine code. The Enhanced CPS{" "}
          <code className="font-mono text-xs text-accent">.h5</code> file
          is read with{" "}
          <code className="font-mono text-xs text-accent">h5py</code>{" "}
          directly. Rule evaluation goes through{" "}
          <code className="font-mono text-xs text-accent">
            axiom-rules-engine
          </code>
          's{" "}
          <code className="font-mono text-xs text-accent">run-compiled</code>{" "}
          subprocess. Aggregation is plain numpy.
        </p>
      </Section>

      {ALL_PROGRAMS.map((p) => (
        <ProgramSection key={p.id} program={p} />
      ))}

      <Section
        eyebrow="comparison"
        title="Side-by-side with PolicyEngine"
      >
        <p className="editorial mb-5">
          The same Enhanced CPS file (
          <code className="font-mono text-xs text-accent">
            enhanced_cps_2024
          </code>{" "}
          uprated to 2026), the same RuleSpec parameter values, the same
          population scope. PolicyEngine numbers are computed via the{" "}
          <code className="font-mono text-xs text-accent">policyengine</code>{" "}
          package using{" "}
          <code className="font-mono text-xs text-accent">Simulation</code>{" "}
          on{" "}
          <code className="font-mono text-xs text-accent">
            us_latest
          </code>
          , aggregating the same variable name we expose from Axiom (
          <code className="font-mono text-xs text-accent">
            income_tax_main_rates
          </code>{" "}
          for §1(j),{" "}
          <code className="font-mono text-xs text-accent">snap</code> for
          CO SNAP).
        </p>

        {!comparison && (
          <div className="rounded-md border border-dashed border-rule bg-rule-subtle p-5 text-sm text-ink-secondary">
            <p>
              No comparison file yet. Generate one with:
            </p>
            <pre className="mt-3 overflow-x-auto rounded-sm bg-ink p-3 font-mono text-xs text-paper">
              /Users/pavelmakarchuk/policyengine.py/.venv/bin/python \{"\n"}
              {"  "}/Users/pavelmakarchuk/axiom-microsim/scripts/refresh_pe_comparison.py
            </pre>
            <p className="mt-3 text-xs">
              Writes{" "}
              <code className="font-mono text-xs text-accent">
                web/public/comparison.json
              </code>
              ; reload this page.
            </p>
          </div>
        )}

        {comparison && (
          <>
            <p className="mb-5 font-mono text-[0.7rem] uppercase tracking-eyebrow text-ink-muted">
              Computed {comparison.computed_at} · dataset{" "}
              {comparison.dataset}
            </p>

            {comparison.federal_income_tax && (
              <ComparisonTable
                title="Federal income tax (§1(j) ordinary brackets) · nationwide"
                rows={[
                  {
                    metric: "Total annual revenue",
                    axiom: fmtUSD(
                      comparison.federal_income_tax.axiom_total_revenue,
                    ),
                    pe: fmtUSD(comparison.federal_income_tax.pe_total_revenue),
                    ratio: fmtRatio(
                      comparison.federal_income_tax.axiom_total_revenue,
                      comparison.federal_income_tax.pe_total_revenue,
                    ),
                  },
                  {
                    metric: "Tax units in dataset",
                    axiom: fmtCount(
                      comparison.federal_income_tax.axiom_n_tax_units,
                    ),
                    pe: fmtCount(comparison.federal_income_tax.pe_n_tax_units),
                  },
                  {
                    metric: "Weighted filers w/ liability",
                    axiom: fmtCount(
                      comparison.federal_income_tax.axiom_weighted_filers,
                    ),
                    pe: fmtCount(
                      comparison.federal_income_tax.pe_weighted_filers,
                    ),
                  },
                  {
                    metric: "Avg per filer",
                    axiom: fmtUSD(
                      comparison.federal_income_tax.axiom_avg_per_filer,
                    ),
                    pe: fmtUSD(comparison.federal_income_tax.pe_avg_per_filer),
                  },
                ]}
              />
            )}

            {comparison.co_snap && (
              <ComparisonTable
                title="Colorado SNAP · CO subset"
                rows={[
                  {
                    metric: "Total annual cost",
                    axiom: fmtUSD(
                      comparison.co_snap.axiom_total_annual_cost,
                    ),
                    pe: fmtUSD(comparison.co_snap.pe_total_annual_cost),
                    ratio: fmtRatio(
                      comparison.co_snap.axiom_total_annual_cost,
                      comparison.co_snap.pe_total_annual_cost,
                    ),
                  },
                  {
                    metric: "Households in dataset",
                    axiom: fmtCount(comparison.co_snap.axiom_n_households),
                    pe: "—",
                  },
                  {
                    metric: "Weighted recipients",
                    axiom: fmtCount(
                      comparison.co_snap.axiom_weighted_recipients,
                    ),
                    pe: "—",
                  },
                ]}
              />
            )}

            {comparison.errors.length > 0 && (
              <div className="mt-5 rounded-sm border border-error bg-paper-elev p-3 text-sm text-error">
                <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow">
                  PE comparison errors
                </div>
                {comparison.errors.map((e, i) => (
                  <div key={i} className="mt-1">
                    {e}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        <p className="mt-6 text-xs text-ink-muted">
          The Axiom side is not an oracle — PolicyEngine has been
          maintained for years and models things this v1 doesn't yet
          (above-the-line deductions, refundable credits, AMT, NIIT, the
          §1(h) capital-gains chain). When the Axiom number is lower than
          PE's, that almost always reflects a v1 limitation, not an
          encoding disagreement.
        </p>
      </Section>
    </main>
  );
}


// --- pieces -----------------------------------------------------------------

function Section({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-14">
      <div className="mb-5 border-b border-rule pb-3">
        <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
          § {eyebrow}
        </div>
        <h2 className="mt-1 font-serif text-2xl text-ink">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function ProgramSection({ program }: { program: ProgramMethodology }) {
  return (
    <Section eyebrow={program.id} title={program.name}>
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <Meta label="Root entity" value={program.rootEntity} />
        <Meta label="Period" value={program.period} />
        <Meta label="Engine relation" value={program.rootEntity === "Household" ? "member_of_household" : "—"} />
        <Meta label="Program YAML" value={program.programYaml} mono />
      </div>

      <h3 className="mb-3 font-serif text-lg text-ink">Outputs</h3>
      <div className="mb-8 space-y-3">
        {program.outputs.map((o) => (
          <div key={o.axiomId} className="rounded-md border border-rule bg-paper-elev p-4">
            <div className="font-mono text-xs text-accent">{o.axiomId}</div>
            <div className="mt-1 text-sm text-ink-secondary">{o.description}</div>
          </div>
        ))}
      </div>

      <h3 className="mb-3 font-serif text-lg text-ink">
        ECPS → Axiom input mapping
      </h3>
      <div className="mb-8 overflow-x-auto rounded-md border border-rule">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-rule-subtle">
            <tr>
              <Th>Axiom slot</Th>
              <Th>Entity</Th>
              <Th>ECPS source</Th>
              <Th>Derivation</Th>
            </tr>
          </thead>
          <tbody>
            {program.inputs.map((row) => (
              <tr key={row.axiomSlot} className="border-t border-rule">
                <Td mono accent>{row.axiomSlot}</Td>
                <Td mono>{row.axiomEntity}</Td>
                <Td mono>{row.ecpsColumn ?? "—"}</Td>
                <Td>{row.derivation}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="mb-3 font-serif text-lg text-ink">Calculations</h3>
      <div className="mb-8 space-y-4">
        {program.calculations.map((c) => (
          <div key={c.name} className="rounded-md border border-rule bg-paper-elev p-4">
            <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
              {c.name}
            </div>
            <div className="mt-2 overflow-x-auto rounded-sm bg-ink/95 p-3 font-mono text-xs text-paper">
              {c.formula}
            </div>
            {c.note && <div className="mt-2 text-xs text-ink-secondary">{c.note}</div>}
          </div>
        ))}
      </div>

      <h3 className="mb-3 font-serif text-lg text-ink">Limitations</h3>
      <div className="overflow-x-auto rounded-md border border-rule">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-rule-subtle">
            <tr>
              <Th>Issue</Th>
              <Th>Impact on result</Th>
              <Th>Status in rules-us</Th>
            </tr>
          </thead>
          <tbody>
            {program.limitations.map((l) => (
              <tr key={l.item} className="border-t border-rule align-top">
                <Td>{l.item}</Td>
                <Td>{l.impact}</Td>
                <Td mono>{l.rulesusStatus}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-3">
      <div className="font-mono text-[0.6rem] uppercase tracking-eyebrow text-ink-muted">
        {label}
      </div>
      <div className={`mt-1 text-sm text-ink ${mono ? "font-mono text-xs break-all" : ""}`}>
        {value}
      </div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
  accent,
}: {
  children: React.ReactNode;
  mono?: boolean;
  accent?: boolean;
}) {
  return (
    <td
      className={`px-3 py-2 ${mono ? "font-mono text-xs" : "text-sm"} ${
        accent ? "text-accent" : "text-ink"
      }`}
    >
      {children}
    </td>
  );
}

function ComparisonTable({
  title,
  rows,
}: {
  title: string;
  rows: { metric: string; axiom: string; pe: string; ratio?: string }[];
}) {
  return (
    <div className="mb-6 overflow-hidden rounded-md border border-rule bg-paper-elev">
      <div className="border-b border-rule bg-rule-subtle px-4 py-2 font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
        {title}
      </div>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-rule">
            <Th>Metric</Th>
            <Th>Axiom</Th>
            <Th>PolicyEngine</Th>
            <Th>Axiom / PE</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.metric} className="border-t border-rule">
              <Td>{r.metric}</Td>
              <Td mono>{r.axiom}</Td>
              <Td mono>{r.pe}</Td>
              <Td mono>{r.ratio ?? "—"}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pipeline() {
  const steps: { eyebrow: string; title: string; body: string }[] = [
    {
      eyebrow: "01",
      title: "Read",
      body: "h5py opens enhanced_cps_2024.h5 and pulls only the columns the chosen program needs. State filter applied at this stage.",
    },
    {
      eyebrow: "02",
      title: "Project",
      body: "Per-program: ECPS columns → engine input slots, with the right entity grouping (Household for SNAP, TaxUnit for §1(j)).",
    },
    {
      eyebrow: "03",
      title: "Patch",
      body: "Reform only: copy rules-us trees to a temp dir, apply ParameterOverride patches to the YAMLs (scale_values / set_values / scale_formula).",
    },
    {
      eyebrow: "04",
      title: "Compile",
      body: "axiom-rules-engine compile resolves imports, type-checks formulas, and emits a JSON artifact (~70 ms).",
    },
    {
      eyebrow: "05",
      title: "Execute",
      body: "Single subprocess call: axiom-rules-engine run-compiled. All N households / tax units in one batched dataset; outputs come back as tagged scalars per query.",
    },
    {
      eyebrow: "06",
      title: "Aggregate",
      body: "Decode to numpy arrays. Σ output × weight for cost / revenue. Weighted decile groupby for distribution. Baseline – reform delta for winners/losers.",
    },
  ];
  return (
    <ol className="space-y-3">
      {steps.map((s) => (
        <li key={s.eyebrow} className="flex gap-4 rounded-md border border-rule bg-paper-elev p-4">
          <div className="flex-shrink-0 font-mono text-[0.7rem] uppercase tracking-eyebrow text-accent">
            § {s.eyebrow}
          </div>
          <div>
            <div className="font-serif text-base text-ink">{s.title}</div>
            <div className="mt-1 text-sm text-ink-secondary">{s.body}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}
