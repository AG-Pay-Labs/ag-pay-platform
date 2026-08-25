"use client";

import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, ShieldCheck, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
  PaymentRuleSetRead,
  PaymentRuleSetWrite,
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
    description: "Every purchase waits for your review.",
  },
  {
    mode: "subscriptions_only",
    label: "Subscriptions only",
    description: "One-time purchases can be approved automatically.",
  },
  {
    mode: "above_amount",
    label: "Above an amount",
    description: "Purchases above your threshold require approval.",
  },
  {
    mode: "subscriptions_or_above_amount",
    label: "Subscriptions or above an amount",
    description: "Review subscriptions and purchases above your threshold.",
  },
  {
    mode: "never",
    label: "Never require approval",
    description: "Eligible purchases are approved automatically.",
  },
];

export function isThresholdMode(mode: PaymentApprovalMode): boolean {
  return mode === "above_amount" || mode === "subscriptions_or_above_amount";
}

export function paymentPolicyLabel(mode: PaymentApprovalMode): string {
  return (
    POLICY_OPTIONS.find((option) => option.mode === mode)?.label ??
    "Approval rule"
  );
}

type RuleSetSheetProps = {
  agents: AgentRead[];
  ruleSet?: PaymentRuleSetRead | null;
  trigger?: ReactNode;
};

export function RuleSetSheet({
  agents,
  ruleSet = null,
  trigger,
}: RuleSetSheetProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(ruleSet?.name ?? "");
  const [mode, setMode] = useState<PaymentApprovalMode>(
    ruleSet?.mode ?? "always"
  );
  const [thresholdAmount, setThresholdAmount] = useState(
    ruleSet?.threshold_amount ?? "20.00"
  );
  const [thresholdCurrency, setThresholdCurrency] = useState(
    ruleSet?.threshold_currency ?? "USD"
  );
  const [agentIds, setAgentIds] = useState<Set<string>>(
    new Set(ruleSet?.assigned_agent_ids ?? [])
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleOpenChange(next: boolean) {
    if (next) {
      setName(ruleSet?.name ?? "");
      setMode(ruleSet?.mode ?? "always");
      setThresholdAmount(ruleSet?.threshold_amount ?? "20.00");
      setThresholdCurrency(ruleSet?.threshold_currency ?? "USD");
      setAgentIds(new Set(ruleSet?.assigned_agent_ids ?? []));
      setError(null);
    }
    setOpen(next);
  }

  function toggleAgent(agentId: string, checked: boolean) {
    setAgentIds((current) => {
      const next = new Set(current);
      if (checked) next.add(agentId);
      else next.delete(agentId);
      return next;
    });
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const normalizedName = name.trim();
    const usesThreshold = isThresholdMode(mode);
    const amount = thresholdAmount.trim();
    const currency = thresholdCurrency.trim().toUpperCase();

    if (!normalizedName) {
      setError("Enter a name for this rule set.");
      return;
    }
    if (
      usesThreshold &&
      (!/^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$/.test(amount) ||
        Number(amount) < 0)
    ) {
      setError(
        "Enter a non-negative threshold with no more than two decimal places."
      );
      return;
    }
    if (usesThreshold && !/^[A-Z]{3}$/.test(currency)) {
      setError("Enter a three-letter currency code, such as USD or EUR.");
      return;
    }

    const payload: PaymentRuleSetWrite = {
      name: normalizedName,
      mode,
      threshold_amount: usesThreshold ? amount : null,
      threshold_currency: usesThreshold ? currency : null,
      agent_ids: Array.from(agentIds),
    };
    const path = ruleSet
      ? `/payment-rule-sets/${ruleSet.id}`
      : "/payment-rule-sets";

    setSubmitting(true);
    try {
      await apiRequest<PaymentRuleSetRead>(path, {
        method: ruleSet ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.paymentRuleSets,
      });
      toast.success(ruleSet ? "Rule set updated" : "Rule set created");
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not save this rule set."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>
        {trigger ?? (
          <Button>
            <Plus aria-hidden="true" />
            Add rule set
          </Button>
        )}
      </SheetTrigger>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-[30rem]">
        <SheetHeader className="border-b px-6 py-6 pr-14">
          <SheetTitle className="text-xl font-semibold tracking-tight">
            {ruleSet ? "Edit rule set" : "Add rule set"}
          </SheetTitle>
          <SheetDescription>
            Configure one approval policy and assign it to any number of agents.
          </SheetDescription>
        </SheetHeader>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
          <div className="flex-1 space-y-6 overflow-y-auto px-6 py-6">
            <div className="space-y-1.5">
              <Label htmlFor={`rule-set-name-${ruleSet?.id ?? "new"}`}>
                Name
              </Label>
              <Input
                id={`rule-set-name-${ruleSet?.id ?? "new"}`}
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={80}
                placeholder="Standard purchases"
                autoFocus
                required
              />
            </div>

            <fieldset>
              <legend className="mb-3 text-sm font-medium">
                Require approval
              </legend>
              <RadioGroup
                value={mode}
                onValueChange={(value) => setMode(value as PaymentApprovalMode)}
                aria-label="Payment approval rule"
                className="gap-2"
              >
                {POLICY_OPTIONS.map((option) => (
                  <Label
                    key={option.mode}
                    htmlFor={`rule-set-${ruleSet?.id ?? "new"}-${option.mode}`}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-xl border bg-card p-3 transition-colors hover:bg-muted/40",
                      mode === option.mode &&
                        "border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/25"
                    )}
                  >
                    <RadioGroupItem
                      id={`rule-set-${ruleSet?.id ?? "new"}-${option.mode}`}
                      value={option.mode}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-foreground">
                        {option.label}
                      </span>
                      <span className="mt-0.5 block text-xs leading-5 font-normal text-muted-foreground">
                        {option.description}
                      </span>
                    </span>
                  </Label>
                ))}
              </RadioGroup>
            </fieldset>

            {isThresholdMode(mode) ? (
              <fieldset className="rounded-xl border bg-muted/30 p-4">
                <legend className="px-1 text-sm font-medium">
                  Approval threshold
                </legend>
                <div className="mt-3 grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor={`threshold-amount-${ruleSet?.id ?? "new"}`}>
                      Amount
                    </Label>
                    <Input
                      id={`threshold-amount-${ruleSet?.id ?? "new"}`}
                      value={thresholdAmount}
                      onChange={(event) =>
                        setThresholdAmount(event.target.value)
                      }
                      type="number"
                      inputMode="decimal"
                      min="0"
                      max="9999999999999999.99"
                      step="0.01"
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label
                      htmlFor={`threshold-currency-${ruleSet?.id ?? "new"}`}
                    >
                      Currency
                    </Label>
                    <Input
                      id={`threshold-currency-${ruleSet?.id ?? "new"}`}
                      value={thresholdCurrency}
                      onChange={(event) =>
                        setThresholdCurrency(event.target.value.toUpperCase())
                      }
                      minLength={3}
                      maxLength={3}
                      pattern="[A-Za-z]{3}"
                      required
                    />
                  </div>
                </div>
                <div className="mt-3 flex gap-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
                  <TriangleAlert
                    className="mt-0.5 size-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  Currency mismatches always require review.
                </div>
              </fieldset>
            ) : null}

            <fieldset>
              <legend className="text-sm font-medium">Assigned agents</legend>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                Selecting an agent moves it from its current rule set.
                Unassigned agents always require approval.
              </p>
              <div className="mt-3 divide-y rounded-xl border">
                {agents.length ? (
                  agents.map((agent) => (
                    <Label
                      key={agent.id}
                      htmlFor={`rule-set-agent-${ruleSet?.id ?? "new"}-${
                        agent.id
                      }`}
                      className="flex cursor-pointer items-center gap-3 px-3 py-3 hover:bg-muted/40"
                    >
                      <Checkbox
                        id={`rule-set-agent-${ruleSet?.id ?? "new"}-${
                          agent.id
                        }`}
                        checked={agentIds.has(agent.id)}
                        onCheckedChange={(checked) =>
                          toggleAgent(agent.id, checked === true)
                        }
                      />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {agent.name}
                      </span>
                      <span className="text-xs text-muted-foreground capitalize">
                        {agent.connection_state}
                      </span>
                    </Label>
                  ))
                ) : (
                  <p className="p-4 text-sm text-muted-foreground">
                    No agents are available yet.
                  </p>
                )}
              </div>
            </fieldset>

            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
          </div>

          <SheetFooter className="border-t bg-background px-6 py-4 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <ShieldCheck aria-hidden="true" />
              )}
              {ruleSet ? "Save changes" : "Create rule set"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
