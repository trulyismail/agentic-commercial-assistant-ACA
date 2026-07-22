import { NextRequest, NextResponse } from "next/server";
import { validateThread } from "@/lib/aca";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  try {
    const snapshot = await validateThread(id, body);
    return NextResponse.json(snapshot);
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
