"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, ShieldCheck, UserPlus } from "lucide-react";

import { BrandLockup } from "@/components/app/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getErrorMessage } from "@/lib/api-client";
import { useAuth } from "@/providers/auth-provider";

export default function RegisterPage() {
  const router = useRouter();
  const { register, status } = useAuth();
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
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("confirmation") ?? "");

    if (password !== confirmation) {
      setError("Passwords do not match.");
      setSubmitting(false);
      return;
    }

    try {
      await register(String(form.get("username") ?? ""), password);
      router.replace("/overview");
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not create the account."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <BrandLockup className="mb-10 lg:hidden" priority />

      <div className="mb-7">
        <p className="mb-2 text-xs font-semibold tracking-wide text-indigo-600 uppercase">
          Start your control plane
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Create your account</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Username and password are all you need for this prototype.
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
            placeholder="alex"
            required
            autoFocus
            className="h-10"
          />
          <p className="text-xs text-muted-foreground">3–64 characters; stored lowercase.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={10}
            maxLength={256}
            required
            className="h-10"
          />
          <p className="text-xs text-muted-foreground">At least 10 characters.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirmation">Confirm password</Label>
          <Input
            id="confirmation"
            name="confirmation"
            type="password"
            autoComplete="new-password"
            minLength={10}
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
          {submitting ? <Loader2 className="animate-spin" /> : <UserPlus />}
          Create account
        </Button>
      </form>

      <div className="mt-5 flex items-start gap-2 rounded-lg bg-muted/60 p-3 text-xs leading-5 text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-indigo-600" />
        Your platform password is separate from merchant credentials created for purchases.
      </div>

      <p className="mt-6 text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-indigo-600 hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
