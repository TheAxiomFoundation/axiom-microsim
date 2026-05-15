"use client";

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  delta?: { value: string; positive: boolean | null };
}

export function StatCard({ label, value, hint, delta }: StatCardProps) {
  return (
    <div className="rounded-md border border-rule bg-paper-elev p-4">
      <div className="font-mono text-[0.65rem] uppercase tracking-eyebrow text-ink-muted">
        {label}
      </div>
      <div className="mt-2 font-serif text-[1.7rem] leading-none tracking-tight text-ink">
        {value}
      </div>
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
