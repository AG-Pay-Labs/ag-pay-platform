"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2, WalletCards } from "lucide-react";

import { AppShell } from "@/components/app";
import { useCartItems } from "@/hooks/use-api-data";
import { useAuth } from "@/providers/auth-provider";

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
  }, [router, status]);

  if (status !== "authenticated") {
    return (
      <div className="flex min-h-svh items-center justify-center bg-muted/30">
        <div className="flex flex-col items-center gap-4 text-center">
          <span className="flex size-11 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm">
            <WalletCards className="size-5" />
          </span>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Securing your workspace
          </div>
        </div>
      </div>
    );
  }

  return <AuthenticatedShell>{children}</AuthenticatedShell>;
}

function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  const cart = useCartItems();
  const pendingApprovals =
    cart.data?.filter((item) => item.status === "proposed").length ?? 0;

  return <AppShell pendingApprovals={pendingApprovals}>{children}</AppShell>;
}

