import { NextRequest, NextResponse } from "next/server";
import { rejectThread } from "@/lib/aca";

export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const snapshot = await rejectThread(id);
    return NextResponse.json(snapshot);
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
