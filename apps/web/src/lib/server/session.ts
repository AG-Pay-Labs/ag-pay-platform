import "server-only";

import { cookies } from "next/headers";
import type { NextResponse } from "next/server";

export const SESSION_COOKIE_NAME = "agpay_session";

function cookieSecurityOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
  };
}

export function setSessionCookie(
  response: NextResponse,
  token: string,
  expiresAt: string,
): void {
  const expires = new Date(expiresAt);
  if (Number.isNaN(expires.getTime())) {
    throw new TypeError("The backend returned an invalid token expiry.");
  }

  response.cookies.set(SESSION_COOKIE_NAME, token, {
    ...cookieSecurityOptions(),
    expires,
  });
}

export function clearSessionCookie(response: NextResponse): void {
  response.cookies.set(SESSION_COOKIE_NAME, "", {
    ...cookieSecurityOptions(),
    expires: new Date(0),
    maxAge: 0,
  });
}

export async function readSessionToken(): Promise<string | null> {
  const cookieStore = await cookies();
  return cookieStore.get(SESSION_COOKIE_NAME)?.value ?? null;
}

function decodeBase64Url(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

export function tokenExpiresAt(token: string): string | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  try {
    const payload = JSON.parse(decodeBase64Url(parts[1])) as { exp?: unknown };
    if (typeof payload.exp !== "number" || !Number.isFinite(payload.exp)) return null;
    return new Date(payload.exp * 1000).toISOString();
  } catch {
    return null;
  }
}

export function isExpired(expiresAt: string): boolean {
  const value = new Date(expiresAt).getTime();
  return !Number.isFinite(value) || value <= Date.now();
}
