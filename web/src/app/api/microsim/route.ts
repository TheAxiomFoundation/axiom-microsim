import { NextRequest, NextResponse } from "next/server";

const UPSTREAM = process.env.AXIOM_MICROSIM_URL?.replace(/\/$/, "");

export async function POST(req: NextRequest) {
  if (!UPSTREAM) {
    return NextResponse.json(
      { error: "AXIOM_MICROSIM_URL not set. Copy web/.env.example to .env.local." },
      { status: 500 },
    );
  }
  const body = await req.text();
  const upstream = await fetch(`${UPSTREAM}/microsim`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
