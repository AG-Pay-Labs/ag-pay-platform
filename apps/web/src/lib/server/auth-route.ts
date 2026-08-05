import "server-only";

import { NextRequest, NextResponse } from "next/server";

import type {
  AuthSession,
  LoginRequest,
  TokenResponse,
  UserRead,
  UserRegister,
} from "@/lib/api-types";
import {
  backendRequest,
  errorResponse,
  malformedJsonResponse,
} from "@/lib/server/backend";
import { setSessionCookie } from "@/lib/server/session";

type AuthBody = LoginRequest | UserRegister;

export async function establishSession(
  request: NextRequest,
  endpoint: "/auth/login" | "/auth/register",
): Promise<NextResponse> {
  let body: AuthBody;
  try {
    body = (await request.json()) as AuthBody;
  } catch {
    return malformedJsonResponse();
  }

  try {
    const token = await backendRequest<TokenResponse>(endpoint, {
      method: "POST",
      body: JSON.stringify(body),
    });
    const user = await backendRequest<UserRead>("/auth/me", {
      token: token.access_token,
    });
    const session: AuthSession = { user, expires_at: token.expires_at };
    const response = NextResponse.json(session, {
      status: endpoint === "/auth/register" ? 201 : 200,
      headers: { "Cache-Control": "no-store" },
    });
    setSessionCookie(response, token.access_token, token.expires_at);
    return response;
  } catch (error) {
    return errorResponse(error);
  }
}
