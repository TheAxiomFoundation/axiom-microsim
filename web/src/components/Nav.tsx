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
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-6 py-4">
        {/* Axiom Foundation wordmark — same asset and link used across
            axiom-foundation.org, co-snap, and demo-shell. */}
        <Link
          href="/"
          className="flex items-center gap-3 no-underline"
          aria-label="axiom-microsim"
        >
          <a
            href="https://axiomfoundation.org"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex w-[100px] shrink-0"
            aria-label="Axiom Foundation"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/axiom-foundation.svg"
              alt="Axiom Foundation"
              width={100}
              className="block h-auto w-full"
            />
          </a>
          <div className="border-l border-rule pl-3">
            <div className="font-mono text-[0.6rem] uppercase tracking-eyebrow text-ink-muted">
              Interactive
            </div>
            <div className="font-serif text-base leading-tight text-ink">
              microsim
            </div>
          </div>
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
