import "server-only";

import { NextResponse } from "next/server";

const DEFAULT_BACKEND_ORIGIN = "http://localhost:8000";

function backendBaseUrl(): string {
  const configured = process.env.AGPAY_API_URL?.trim() || DEFAULT_BACKEND_ORIGIN;
  const withoutTrailingSlash = configured.replace(/\/+$/, "");
  return withoutTrailingSlash.endsWith("/api/v1")
    ? withoutTrailingSlash
    : `${withoutTrailingSlash}/api/v1`;
}

export function backendUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new TypeError("Backend paths must begin with a single '/'.");
  }
  return `${backendBaseUrl()}${path}`;
}

export interface BackendRequestInit extends RequestInit {
  token?: string;
}

export async function backendFetch(
  path: string,
  { token, ...init }: BackendRequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(backendUrl(path), {
    ...init,
    headers,
    cache: "no-store",
  });
}

async function parseBackendPayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { detail: "The backend returned an invalid response." };
  }
}

export class BackendApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, payload: unknown) {
    super(`Backend API request failed with status ${status}`);
    this.name = "BackendApiError";
    this.status = status;
    this.payload = payload;
  }
}

export async function backendRequest<T>(
  path: string,
  init: BackendRequestInit = {},
): Promise<T> {
  const response = await backendFetch(path, init);
  const payload = await parseBackendPayload(response);
  if (!response.ok) throw new BackendApiError(response.status, payload);
  return payload as T;
}

export function errorResponse(error: unknown): NextResponse {
  if (error instanceof BackendApiError) {
    const payload = error.payload ?? { detail: "The backend rejected the request." };
    return NextResponse.json(payload, {
      status: error.status,
      headers: { "Cache-Control": "no-store" },
    });
  }

  return NextResponse.json(
    { detail: "The backend API is unavailable." },
    { status: 502, headers: { "Cache-Control": "no-store" } },
  );
}

export function malformedJsonResponse(): NextResponse {
  return NextResponse.json(
    { detail: "Request body must be valid JSON." },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}
