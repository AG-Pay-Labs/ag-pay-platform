"use client";

import {
  QueryClient,
  QueryClientProvider,
  type QueryClientConfig,
} from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api-client";

const queryClientConfig: QueryClientConfig = {
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: true,
      retry(failureCount, error) {
        return error instanceof ApiError && error.status >= 500 && failureCount < 1;
      },
    },
    mutations: {
      // Mutation results can contain sensitive one-time data such as revealed
      // merchant credentials, so discard inactive mutation results immediately.
      gcTime: 0,
      retry: false,
    },
  },
};

export function createQueryClient(): QueryClient {
  return new QueryClient(queryClientConfig);
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
