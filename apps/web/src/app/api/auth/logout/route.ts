import { NextResponse } from "next/server";

import { clearSessionCookie } from "@/lib/server/session";

export async function POST() {
  const response = new NextResponse(null, {
    status: 204,
    headers: { "Cache-Control": "no-store" },
  });
  clearSessionCookie(response);
  return response;
}
