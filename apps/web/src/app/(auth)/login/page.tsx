"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, LogIn } from "lucide-react";

import { BrandLockup } from "@/components/app/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getErrorMessage } from "@/lib/api-client";
import { useAuth } from "@/providers/auth-provider";

export default function LoginPage() {
  const router = useRouter();
  const { login, status } = useAuth();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated") router.replace("/overview");
  }, [router, status]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);

    try {
      await login(
        String(form.get("username") ?? ""),
        String(form.get("password") ?? ""),
      );
      router.replace("/overview");
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not sign in."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <BrandLockup className="mb-10 lg:hidden" priority />

      <div className="mb-7">
        <p className="mb-2 text-xs font-semibold tracking-wide text-indigo-600 uppercase">
          Welcome back
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Sign in to your workspace</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Review agent proposals and manage payment permissions.
        </p>
      </div>

      <form method="post" onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            name="username"
            autoComplete="username"
            minLength={3}
            maxLength={64}
            required
            autoFocus
            className="h-10"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            className="h-10"
          />
        </div>
        {error ? (
          <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">
            {error}
          </p>
        ) : null}
        <Button type="submit" size="lg" className="h-10 w-full" disabled={submitting}>
          {submitting ? <Loader2 className="animate-spin" /> : <LogIn />}
          Sign in
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-muted-foreground">
        New to AG Pay?{" "}
        <Link href="/register" className="font-medium text-indigo-600 hover:underline">
          Create an account
        </Link>
      </p>
      <p className="mt-8 text-center text-xs leading-5 text-muted-foreground">
        This prototype has no password recovery yet. Keep your credentials safe.
      </p>
    </div>
  );
}
