"use client";

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  delta?: { value: string; positive: boolean | null };
}

export function StatCard({ label, value, hint, delta }: StatCardProps) {
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-stone-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-stone-900">{value}</div>
      {delta && (
        <div
          className={`mt-1 text-sm ${
            delta.positive === null
              ? "text-stone-500"
              : delta.positive
                ? "text-emerald-600"
                : "text-rose-600"
          }`}
        >
          {delta.value}
        </div>
      )}
      {hint && <div className="mt-1 text-xs text-stone-500">{hint}</div>}
    </div>
  );
}
