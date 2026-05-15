import { NextRequest, NextResponse } from "next/server";

const UPSTREAM = process.env.AXIOM_MICROSIM_URL?.replace(/\/$/, "");

export async function POST(req: NextRequest) {
  if (!UPSTREAM) {
    return NextResponse.json(
      { error: "AXIOM_MICROSIM_URL not set." },
      { status: 500 },
    );
  }
  const body = await req.text();
  const upstream = await fetch(`${UPSTREAM}/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    // PE compute is slow; let it run.
    signal: AbortSignal.timeout(600_000),
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
