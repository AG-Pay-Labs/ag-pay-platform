"use client";

import type { ReactNode } from "react";
import { ThemeProvider } from "next-themes";

import { AuthProvider } from "@/providers/auth-provider";
import { QueryProvider } from "@/providers/query-provider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem>
      <QueryProvider>
        <AuthProvider>{children}</AuthProvider>
      </QueryProvider>
    </ThemeProvider>
  );
}
