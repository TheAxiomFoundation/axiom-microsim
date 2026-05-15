"use client";

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  delta?: { value: string; positive: boolean | null };
  /** Optional PE comparison value rendered as a second row under the headline. */
  peValue?: string;
  /** Ratio "Axiom / PE", rendered as a small chip. */
  peRatio?: string;
}

export function StatCard({ label, value, hint, delta, peValue, peRatio }: StatCardProps) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-4">
      <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
        {label}
      </div>
      <div className="mt-2 font-serif text-[1.7rem] leading-none tracking-tight text-ink">
        {value}
      </div>
      {peValue && (
        <div className="mt-2 flex items-baseline justify-between border-t border-rule pt-2">
          <div className="text-xs text-ink-secondary">
            <span className="font-mono text-[0.6rem] uppercase tracking-eyebrow text-ink-muted">
              PE
            </span>{" "}
            {peValue}
          </div>
          {peRatio && (
            <div className="rounded-sm bg-rule-subtle px-1.5 py-0.5 font-mono text-[0.65rem] text-ink-secondary">
              {peRatio}
            </div>
          )}
        </div>
      )}
      {delta && (
        <div
          className={`mt-2 font-mono text-xs ${
            delta.positive === null
              ? "text-ink-muted"
              : delta.positive
                ? "text-success"
                : "text-error"
          }`}
        >
          {delta.value}
        </div>
      )}
      {hint && (
        <div className="mt-2 font-mono text-[0.7rem] uppercase tracking-eyebrow text-ink-muted">
          {hint}
        </div>
      )}
    </div>
  );
}
