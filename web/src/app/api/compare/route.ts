import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

// The undici dispatcher below is a Node API, so pin the route off the edge
// runtime. This also matches the 800s maxDuration in web/vercel.json.
export const runtime = "nodejs";

const UPSTREAM = process.env.AXIOM_MICROSIM_URL?.replace(/\/$/, "");

// Vercel allows this function 800s (web/vercel.json); we abort just under it.
const COMPARE_TIMEOUT_MS = 790_000;

// undici — the fetch Node ships — caps a single request at `headersTimeout`
// (300s by default) while waiting for response headers, and an AbortSignal
// can only ever shorten that window, never extend it. FastAPI emits no
// headers until the handler returns, so a cold PE /compare that needs more
// than 300s would be cut at 300s rather than at the 790s we advertise.
// Measured: the limit applies per redirect hop, so Modal's long-poll 303s do
// reset it — but that is Modal's current behaviour, not a contract, and a
// direct upstream has no hops at all. Set it explicitly to cover the window.
//
// This deliberately calls undici's own `fetch` instead of the global one: a
// dispatcher only works with the undici instance that built it, and global
// fetch is backed by whichever undici Node itself bundles. Passing this
// Agent to global fetch throws UND_ERR_INVALID_ARG whenever those two
// versions disagree, so keeping both on one instance is the safe pairing.
// Redirect handling, POST-to-GET on 303, and AbortSignal are unchanged.
const dispatcher = new Agent({
  headersTimeout: COMPARE_TIMEOUT_MS,
  bodyTimeout: COMPARE_TIMEOUT_MS,
});

export async function POST(req: NextRequest) {
  if (!UPSTREAM) {
    return NextResponse.json(
      { error: "AXIOM_MICROSIM_URL not set." },
      { status: 500 },
    );
  }
  const body = await req.text();
  const upstream = await undiciFetch(`${UPSTREAM}/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    // PE compute is slow; let it run right up to the 800s maxDuration.
    signal: AbortSignal.timeout(COMPARE_TIMEOUT_MS),
    dispatcher,
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
