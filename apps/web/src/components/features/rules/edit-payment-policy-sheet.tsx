"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CreditCard, Loader2, Pencil, ShieldCheck, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { queryKeys } from "@/hooks/use-api-data";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type {
  AgentRead,
  PaymentApprovalMode,
  PaymentPolicyRead,
  PaymentPolicyUpdate,
} from "@/lib/api-types";
import { cn } from "@/lib/utils";

const POLICY_OPTIONS: Array<{
  mode: PaymentApprovalMode;
  label: string;
  description: string;
}> = [
  {
    mode: "always",
    label: "Always require approval",
    description: "Every purchase waits for your review before the agent can continue.",
  },
  {
    mode: "subscriptions_only",
    label: "Subscriptions only",
    description: "Subscriptions require approval; one-time purchases can be approved automatically.",
  },
  {
    mode: "above_amount",
    label: "Above an amount",
    description: "For example, purchases over $20 require approval.",
  },
  {
    mode: "subscriptions_or_above_amount",
    label: "Subscriptions or above an amount",
    description: "Subscriptions and purchases over $20 require approval.",
  },
  {
    mode: "never",
    label: "Never require approval",
    description: "Eligible purchases can be approved automatically without human review.",
  },
];

export function isThresholdMode(mode: PaymentApprovalMode): boolean {
  return mode === "above_amount" || mode === "subscriptions_or_above_amount";
}

export function paymentPolicyLabel(mode: PaymentApprovalMode): string {
  return POLICY_OPTIONS.find((option) => option.mode === mode)?.label ?? "Approval rule";
}

type EditPaymentPolicySheetProps = {
  agent: AgentRead;
  policy: PaymentPolicyRead | null;
};

export function EditPaymentPolicySheet({ agent, policy }: EditPaymentPolicySheetProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<PaymentApprovalMode>(policy?.mode ?? "always");
  const [thresholdAmount, setThresholdAmount] = useState(policy?.threshold_amount ?? "20.00");
  const [thresholdCurrency, setThresholdCurrency] = useState(
    policy?.threshold_currency ?? "USD",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleOpenChange(next: boolean) {
    if (next) {
      setMode(policy?.mode ?? "always");
      setThresholdAmount(policy?.threshold_amount ?? "20.00");
      setThresholdCurrency(policy?.threshold_currency ?? "USD");
      setError(null);
    }
    setOpen(next);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const usesThreshold = isThresholdMode(mode);
    const amount = thresholdAmount.trim();
    const currency = thresholdCurrency.trim().toUpperCase();

    if (
      usesThreshold &&
      (!/^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$/.test(amount) || Number(amount) < 0)
    ) {
      setError("Enter a non-negative threshold with no more than two decimal places.");
      return;
    }
    if (usesThreshold && !/^[A-Z]{3}$/.test(currency)) {
      setError("Enter a three-letter currency code, such as USD or EUR.");
      return;
    }

    const payload: PaymentPolicyUpdate = {
      mode,
      threshold_amount: usesThreshold ? amount : null,
      threshold_currency: usesThreshold ? currency : null,
    };

    setSubmitting(true);
    try {
      const updated = await apiRequest<PaymentPolicyRead>(
        `/agents/${agent.id}/payment-policy`,
        {
          method: "PATCH",
          body: JSON.stringify(payload),
        },
      );
      queryClient.setQueryData<PaymentPolicyRead[]>(queryKeys.paymentPolicies, (current) => {
        if (!current) return [updated];
        const exists = current.some((candidate) => candidate.agent_id === updated.agent_id);
        return exists
          ? current.map((candidate) =>
              candidate.agent_id === updated.agent_id ? updated : candidate,
            )
          : [updated, ...current];
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.paymentPolicies });
      toast.success(`Approval rule updated for ${agent.name}`);
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not update this approval rule."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm">
          <Pencil aria-hidden="true" />
          Edit rule
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full gap-0 sm:max-w-lg">
        <SheetHeader className="border-b px-5 py-5 pr-14">
          <SheetTitle>Edit approval rule</SheetTitle>
          <SheetDescription>
            Decide when legacy external-completion purchases proposed by {agent.name} must wait for
            you. Managed browser checkout always requires your explicit approval.
          </SheetDescription>
        </SheetHeader>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
          <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
            <fieldset>
              <legend className="mb-3 text-sm font-medium">Require approval</legend>
              <RadioGroup
                value={mode}
                onValueChange={(value) => setMode(value as PaymentApprovalMode)}
                aria-label="Payment approval rule"
                className="gap-2.5"
              >
                {POLICY_OPTIONS.map((option) => (
                  <Label
                    key={option.mode}
                    htmlFor={`policy-${agent.id}-${option.mode}`}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-xl border bg-card p-3.5 transition-colors hover:bg-muted/40",
                      mode === option.mode && "border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/25",
                    )}
                  >
                    <RadioGroupItem
                      id={`policy-${agent.id}-${option.mode}`}
                      value={option.mode}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-foreground">
                        {option.label}
                      </span>
                      <span className="mt-1 block text-xs leading-5 font-normal text-muted-foreground">
                        {option.description}
                      </span>
                    </span>
                  </Label>
                ))}
              </RadioGroup>
            </fieldset>

            {isThresholdMode(mode) ? (
              <fieldset className="rounded-xl border bg-muted/30 p-4">
                <legend className="px-1 text-sm font-medium">Approval threshold</legend>
                <p id={`threshold-help-${agent.id}`} className="mb-4 text-xs leading-5 text-muted-foreground">
                  Purchases strictly above this amount require approval. A purchase in another
                  currency is always sent for approval.
                </p>
                <div className="grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor={`threshold-amount-${agent.id}`}>Amount</Label>
                    <Input
                      id={`threshold-amount-${agent.id}`}
                      value={thresholdAmount}
                      onChange={(event) => setThresholdAmount(event.target.value)}
                      type="number"
                      inputMode="decimal"
                      min="0"
                      max="9999999999999999.99"
                      step="0.01"
                      placeholder="20.00"
                      aria-describedby={`threshold-help-${agent.id}`}
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`threshold-currency-${agent.id}`}>Currency</Label>
                    <Input
                      id={`threshold-currency-${agent.id}`}
                      value={thresholdCurrency}
                      onChange={(event) => setThresholdCurrency(event.target.value.toUpperCase())}
                      inputMode="text"
                      minLength={3}
                      maxLength={3}
                      pattern="[A-Za-z]{3}"
                      placeholder="USD"
                      autoCapitalize="characters"
                      autoComplete="off"
                      aria-describedby={`threshold-help-${agent.id}`}
                      required
                    />
                  </div>
                </div>
              </fieldset>
            ) : null}

            <div className="space-y-3">
              <div className="flex gap-3 rounded-xl border border-indigo-200 bg-indigo-50 p-3.5 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
                <CreditCard className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <p className="leading-5">
                  These rules can auto-approve only legacy external-completion proposals, using
                  an active method assigned to the agent. Managed browser checkout always waits
                  for your explicit approval.
                </p>
              </div>
              {isThresholdMode(mode) ? (
                <div className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3.5 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <p className="leading-5">
                    Currency mismatches are sent for human approval; AG Pay does not convert
                    currencies when evaluating a threshold.
                  </p>
                </div>
              ) : null}
            </div>

            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
          </div>

          <SheetFooter className="border-t bg-background px-5 py-4 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <ShieldCheck aria-hidden="true" />
              )}
              {submitting ? "Saving…" : "Save rule"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
