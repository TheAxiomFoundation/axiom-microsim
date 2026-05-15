"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Microsim" },
  { href: "/methodology", label: "Methodology" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="border-b border-rule bg-paper-elev">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        <Link
          href="/"
          className="flex items-center gap-2 font-mono text-[0.75rem] uppercase tracking-eyebrow text-accent"
        >
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-sm bg-accent font-bold text-white">
            A
          </span>
          axiom-microsim
        </Link>
        <div className="flex items-center gap-1">
          {LINKS.map((l) => {
            const active = l.href === "/" ? pathname === "/" : pathname?.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-sm px-3 py-1.5 text-sm transition ${
                  active
                    ? "bg-accent-light text-accent"
                    : "text-ink-secondary hover:bg-rule-subtle"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
