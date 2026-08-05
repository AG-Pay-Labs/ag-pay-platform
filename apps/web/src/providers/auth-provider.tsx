"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";

import {
  API_UNAUTHORIZED_EVENT,
  ApiError,
  authRequest,
} from "@/lib/api-client";
import type { AuthSession, UserRead } from "@/lib/api-types";

export const AUTH_SESSION_QUERY_KEY = ["auth", "session"] as const;

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  user: UserRead | null;
  session: AuthSession | null;
  status: AuthStatus;
  loading: boolean;
  isAuthenticated: boolean;
  error: Error | null;
  login(username: string, password: string): Promise<AuthSession>;
  register(username: string, password: string): Promise<AuthSession>;
  logout(): Promise<void>;
  refreshSession(): Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function loadSession(): Promise<AuthSession | null> {
  try {
    return await authRequest<AuthSession>("/session");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const sessionQuery = useQuery({
    queryKey: AUTH_SESSION_QUERY_KEY,
    queryFn: loadSession,
  });

  const clearProtectedData = useCallback(() => {
    queryClient.removeQueries({
      predicate: (query) => query.queryKey[0] !== "auth",
    });
  }, [queryClient]);

  const markUnauthenticated = useCallback(() => {
    queryClient.setQueryData<AuthSession | null>(AUTH_SESSION_QUERY_KEY, null);
    clearProtectedData();
  }, [clearProtectedData, queryClient]);

  useEffect(() => {
    window.addEventListener(API_UNAUTHORIZED_EVENT, markUnauthenticated);
    return () => window.removeEventListener(API_UNAUTHORIZED_EVENT, markUnauthenticated);
  }, [markUnauthenticated]);

  useEffect(() => {
    const expiresAt = sessionQuery.data?.expires_at;
    if (!expiresAt) return;

    const remaining = new Date(expiresAt).getTime() - Date.now();
    if (remaining <= 0) {
      markUnauthenticated();
      return;
    }

    const timeout = window.setTimeout(
      markUnauthenticated,
      Math.min(remaining, 2_147_483_647),
    );
    return () => window.clearTimeout(timeout);
  }, [markUnauthenticated, sessionQuery.data?.expires_at]);

  const establishSession = useCallback(
    async (route: "/login" | "/register", username: string, password: string) => {
      const session = await authRequest<AuthSession>(route, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      clearProtectedData();
      queryClient.setQueryData(AUTH_SESSION_QUERY_KEY, session);
      return session;
    },
    [clearProtectedData, queryClient],
  );

  const login = useCallback(
    (username: string, password: string) => establishSession("/login", username, password),
    [establishSession],
  );

  const register = useCallback(
    (username: string, password: string) =>
      establishSession("/register", username, password),
    [establishSession],
  );

  const logout = useCallback(async () => {
    try {
      await authRequest<void>("/logout", { method: "POST" });
    } finally {
      markUnauthenticated();
    }
  }, [markUnauthenticated]);

  const refreshSession = useCallback(async () => {
    const result = await sessionQuery.refetch();
    if (result.error) throw result.error;
  }, [sessionQuery]);

  const session = sessionQuery.data ?? null;
  const loading = sessionQuery.isPending;
  const status: AuthStatus = loading
    ? "loading"
    : session
      ? "authenticated"
      : "unauthenticated";

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
      session,
      status,
      loading,
      isAuthenticated: status === "authenticated",
      error: sessionQuery.error instanceof Error ? sessionQuery.error : null,
      login,
      register,
      logout,
      refreshSession,
    }),
    [
      loading,
      login,
      logout,
      refreshSession,
      register,
      session,
      sessionQuery.error,
      status,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AppProviders.");
  return context;
}
