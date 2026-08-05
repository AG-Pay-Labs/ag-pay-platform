import { type NextRequest } from "next/server";

import { establishSession } from "@/lib/server/auth-route";

export async function POST(request: NextRequest) {
  return establishSession(request, "/auth/register");
}
