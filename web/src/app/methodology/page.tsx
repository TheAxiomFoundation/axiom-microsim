import { MethodologyPeSection } from "@/components/MethodologyPeSection";
import { ALL_PROGRAMS, type ProgramMethodology } from "@/lib/methodology";

export default function MethodologyPage() {
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
          Every aggregate on the runner page is the end of a six-step
          pipeline: read the Enhanced CPS file, project its columns into
          the input contract a RuleSpec module expects, batch every
          household or tax unit into a single engine call, decode the
          result, and weight it back to the population. This page
          documents what those steps actually do — slot by slot — and
          where the result departs from PolicyEngine, run on the same
          data with the same parameters.
        </p>
      </header>

      <Section eyebrow="01" title="Pipeline">
        <p className="editorial mb-5">
          Every program shares the same skeleton; only the projection
          layer and the program YAML change.
        </p>
        <Pipeline />
        <p className="mt-5 text-sm text-ink-secondary">
          The runtime imports zero PolicyEngine code. The Enhanced CPS{" "}
          <code className="font-mono text-xs text-accent">.h5</code> file
          is read with{" "}
          <code className="font-mono text-xs text-accent">h5py</code>{" "}
          directly. Rule evaluation goes through{" "}
          <code className="font-mono text-xs text-accent">axiom-rules-engine</code>'s{" "}
          <code className="font-mono text-xs text-accent">run-compiled</code>{" "}
          subprocess. Aggregation is plain numpy.
        </p>
      </Section>

      {ALL_PROGRAMS.map((p) => (
        <ProgramSection key={p.id} program={p} />
      ))}

      <Section eyebrow="comparison" title="Side-by-side with PolicyEngine">
        <MethodologyPeSection />
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
        <Meta
          label="Engine relation"
          value={program.rootEntity === "Household" ? "member_of_household" : "—"}
        />
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
                <Td mono accent>
                  {row.axiomSlot}
                </Td>
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

function Meta({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-3">
      <div className="font-mono text-[0.6rem] uppercase tracking-eyebrow text-ink-muted">
        {label}
      </div>
      <div
        className={`mt-1 text-sm text-ink ${mono ? "font-mono text-xs break-all" : ""}`}
      >
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
      body: "Per-program: ECPS columns → engine input slots, with the right entity grouping (Household for SNAP, TaxUnit for §1(j) and §24(h) CTC).",
    },
    {
      eyebrow: "03",
      title: "Patch",
      body: "Reform only: copy rules-us trees to a temp dir, apply ParameterOverride patches to the YAMLs (scale_values / set_values / scale_formula / set_formula).",
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
