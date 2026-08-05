import type { ApiErrorPayload, ValidationIssue } from "@/lib/api-types";

export const API_UNAUTHORIZED_EVENT = "agpay:unauthorized";

function isValidationIssue(value: unknown): value is ValidationIssue {
  if (typeof value !== "object" || value === null) return false;
  const issue = value as Partial<ValidationIssue>;
  return typeof issue.msg === "string" && Array.isArray(issue.loc);
}

function readDetail(payload: unknown): string | ValidationIssue[] | null {
  if (typeof payload !== "object" || payload === null || !("detail" in payload)) {
    return null;
  }

  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.every(isValidationIssue)) return detail;
  return null;
}

function validationMessage(issues: ValidationIssue[]): string {
  const issue = issues[0];
  if (!issue) return "The submitted data is invalid.";

  const field = issue.loc
    .filter((part) => part !== "body" && part !== "query" && part !== "path")
    .join(".");
  return field ? `${field}: ${issue.msg}` : issue.msg;
}

export class ApiError extends Error {
  readonly status: number;
  readonly payload: ApiErrorPayload | unknown;
  readonly issues: ValidationIssue[];

  constructor(status: number, payload: unknown, statusText = "Request failed") {
    const detail = readDetail(payload);
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? validationMessage(detail)
          : statusText || `Request failed with status ${status}`;

    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.issues = Array.isArray(detail) ? detail : [];
  }
}

export function getErrorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  if (typeof error === "string" && error.trim()) return error;
  return fallback;
}

async function responsePayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;

  const text = await response.text();
  if (!text) return undefined;

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return { detail: "The server returned an invalid JSON response." };
    }
  }

  return text;
}

function notifyUnauthorized(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(API_UNAUTHORIZED_EVENT));
  }
}

function requestHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");

  if (
    init?.body != null &&
    !(init.body instanceof FormData) &&
    !(init.body instanceof URLSearchParams) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  return headers;
}

async function requestJson<T>(
  url: string,
  init?: RequestInit,
  notifyOnUnauthorized = true,
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: requestHeaders(init),
    credentials: "same-origin",
    cache: "no-store",
  });
  const payload = await responsePayload(response);

  if (!response.ok) {
    if (response.status === 401 && notifyOnUnauthorized) notifyUnauthorized();
    throw new ApiError(response.status, payload, response.statusText);
  }

  return payload as T;
}

/**
 * Calls an allowlisted FastAPI human endpoint through the same-origin Next.js BFF.
 * Pass paths such as `/agents` or `/cart-items?status=proposed`.
 */
export function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new TypeError("API paths must be same-origin absolute paths beginning with '/'.");
  }

  return requestJson<T>(`/api/backend${path}`, init);
}

export type AuthRoute = "/login" | "/register" | "/logout" | "/session";

/** Internal auth-BFF client used by AuthProvider. */
export function authRequest<T>(path: AuthRoute, init?: RequestInit): Promise<T> {
  return requestJson<T>(`/api/auth${path}`, init, false);
}
