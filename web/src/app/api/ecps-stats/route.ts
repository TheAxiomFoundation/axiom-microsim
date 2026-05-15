import { NextRequest, NextResponse } from "next/server";

const UPSTREAM = process.env.AXIOM_MICROSIM_URL?.replace(/\/$/, "");

export async function GET(req: NextRequest) {
  if (!UPSTREAM) {
    return NextResponse.json({ error: "AXIOM_MICROSIM_URL not set." }, { status: 500 });
  }
  const url = new URL(req.url);
  const params = new URLSearchParams();
  const program = url.searchParams.get("program");
  const state = url.searchParams.get("state");
  if (program) params.set("program", program);
  if (state) params.set("state", state);
  const upstream = await fetch(`${UPSTREAM}/ecps-stats?${params}`, { method: "GET" });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
