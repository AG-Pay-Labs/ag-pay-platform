import { NextResponse } from "next/server";

import type { AuthSession, UserRead } from "@/lib/api-types";
import {
  BackendApiError,
  backendRequest,
  errorResponse,
} from "@/lib/server/backend";
import {
  clearSessionCookie,
  isExpired,
  readSessionToken,
  tokenExpiresAt,
} from "@/lib/server/session";

function unauthenticatedResponse(): NextResponse {
  const response = NextResponse.json(
    { detail: "Not authenticated." },
    { status: 401, headers: { "Cache-Control": "no-store" } },
  );
  clearSessionCookie(response);
  return response;
}

export async function GET() {
  const token = await readSessionToken();
  if (!token) return unauthenticatedResponse();

  const expiresAt = tokenExpiresAt(token);
  if (!expiresAt || isExpired(expiresAt)) return unauthenticatedResponse();

  try {
    const user = await backendRequest<UserRead>("/auth/me", { token });
    const session: AuthSession = { user, expires_at: expiresAt };
    return NextResponse.json(session, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    if (error instanceof BackendApiError && error.status === 401) {
      return unauthenticatedResponse();
    }
    return errorResponse(error);
  }
}
