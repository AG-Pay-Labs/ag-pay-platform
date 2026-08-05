import { type NextRequest, NextResponse } from "next/server";

import { backendFetch, errorResponse } from "@/lib/server/backend";
import {
  clearSessionCookie,
  isExpired,
  readSessionToken,
  tokenExpiresAt,
} from "@/lib/server/session";

type RouteContext = { params: Promise<{ path: string[] }> };

const UUID =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

const ALLOWED_ROUTES: ReadonlyArray<readonly [string, RegExp]> = [
  ["GET", /^\/agents$/],
  ["POST", /^\/agents$/],
  ["GET", new RegExp(`^/agents/${UUID}$`)],
  ["DELETE", new RegExp(`^/agents/${UUID}$`)],
  ["POST", new RegExp(`^/agents/${UUID}/pairing-token$`)],
  ["GET", new RegExp(`^/agents/${UUID}/payment-methods$`)],
  ["PUT", new RegExp(`^/agents/${UUID}/payment-methods/${UUID}$`)],
  ["DELETE", new RegExp(`^/agents/${UUID}/payment-methods/${UUID}$`)],
  ["GET", /^\/payment-methods$/],
  ["POST", /^\/payment-methods$/],
  ["DELETE", new RegExp(`^/payment-methods/${UUID}$`)],
  ["GET", /^\/payment-policies$/],
  ["PATCH", new RegExp(`^/agents/${UUID}/payment-policy$`)],
  ["GET", /^\/cart-items$/],
  ["GET", new RegExp(`^/cart-items/${UUID}$`)],
  ["POST", new RegExp(`^/cart-items/${UUID}/approve$`)],
  ["POST", new RegExp(`^/cart-items/${UUID}/cancel$`)],
  ["POST", new RegExp(`^/cart-items/${UUID}/credential/reveal$`)],
  ["GET", /^\/purchases$/],
  ["GET", new RegExp(`^/purchases/${UUID}$`)],
  ["GET", /^\/subscriptions$/],
  ["PATCH", new RegExp(`^/subscriptions/${UUID}$`)],
];

function isAllowedRoute(method: string, path: string): boolean {
  return ALLOWED_ROUTES.some(([allowedMethod, pattern]) => {
    return method === allowedMethod && pattern.test(path);
  });
}

function hasAllowedQuery(path: string, searchParams: URLSearchParams): boolean {
  const entries = Array.from(searchParams.entries());
  if (entries.length === 0) return true;
  if (path !== "/cart-items" || entries.length !== 1) return false;

  const [key] = entries[0];
  return key === "status";
}

function jsonError(detail: string, status: number): NextResponse {
  return NextResponse.json(
    { detail },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

function unauthenticatedResponse(): NextResponse {
  const response = jsonError("Not authenticated.", 401);
  clearSessionCookie(response);
  return response;
}

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { path: segments } = await context.params;
  const path = `/${segments.join("/")}`;

  if (!isAllowedRoute(request.method, path)) {
    return jsonError("The requested API route is not available.", 404);
  }
  if (!hasAllowedQuery(path, request.nextUrl.searchParams)) {
    return jsonError("The supplied query parameters are not allowed.", 400);
  }

  const token = await readSessionToken();
  if (!token) return unauthenticatedResponse();
  const expiresAt = tokenExpiresAt(token);
  if (!expiresAt || isExpired(expiresAt)) return unauthenticatedResponse();

  const headers = new Headers({ Accept: "application/json" });
  const idempotencyKey = request.headers.get("Idempotency-Key");
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);

  let body: string | undefined;
  if (request.body !== null) {
    const requestBody = await request.text();
    if (requestBody) {
      const contentType = request.headers.get("content-type") ?? "";
      if (!contentType.toLowerCase().includes("application/json")) {
        return jsonError("Only application/json request bodies are accepted.", 415);
      }
      body = requestBody;
      headers.set("Content-Type", "application/json");
    }
  }

  const search = request.nextUrl.searchParams.toString();
  const backendPath = search ? `${path}?${search}` : path;

  try {
    const upstream = await backendFetch(backendPath, {
      method: request.method,
      headers,
      body,
      token,
    });
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    const responseContentType = upstream.headers.get("content-type");
    if (responseContentType) responseHeaders.set("Content-Type", responseContentType);

    const responseBody = upstream.status === 204 ? null : await upstream.arrayBuffer();
    const response = new NextResponse(responseBody, {
      status: upstream.status,
      headers: responseHeaders,
    });
    if (upstream.status === 401) clearSessionCookie(response);
    return response;
  } catch (error) {
    return errorResponse(error);
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
