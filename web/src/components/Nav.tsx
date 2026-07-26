"use client";

import Link from "next/link";

// /methodology stays routable but is intentionally unlisted here.

export function Nav() {
  return (
    <nav className="border-b border-rule bg-paper-elev">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-6 py-4 sm:gap-6">
        {/* Axiom Foundation wordmark links out; the title block links to
            the runner. Two separate anchors — they can't nest. */}
        <div className="flex items-center gap-3">
          <a
            href="https://axiom-foundation.org"
            className="inline-flex w-[100px] shrink-0"
            aria-label="Axiom Foundation"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/gallery/microsim/axiom-foundation.svg"
              alt="Axiom Foundation"
              width={100}
              className="block h-auto w-full"
            />
          </a>
          <Link href="/" className="border-l border-rule pl-3 no-underline" aria-label="Microsim">
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-muted">
              Interactive
            </div>
            <div className="font-serif text-base leading-tight text-ink">Microsim</div>
          </Link>
        </div>

        <a
          href="https://axiom.org/demos"
          className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-muted no-underline transition hover:text-accent hover:underline"
        >
          All demos
        </a>
      </div>
    </nav>
  );
}
