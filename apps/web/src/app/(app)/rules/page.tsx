"use client";

import { useMemo } from "react";
import Link from "next/link";
import {
  Bot,
  CalendarClock,
  CircleDollarSign,
  ShieldCheck,
  ShieldOff,
  SlidersHorizontal,
  TriangleAlert,
} from "lucide-react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/app";
import {
  EditPaymentPolicySheet,
  isThresholdMode,
  paymentPolicyLabel,
} from "@/components/features/rules/edit-payment-policy-sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { useAgents, usePaymentPolicies } from "@/hooks/use-api-data";
import type { AgentRead, PaymentApprovalMode, PaymentPolicyRead } from "@/lib/api-types";
import { formatMoney } from "@/components/app/money";
import { formatDateTime } from "@/utils/format";

const POLICY_ICONS = {
  always: ShieldCheck,
  subscriptions_only: CalendarClock,
  above_amount: CircleDollarSign,
  subscriptions_or_above_amount: SlidersHorizontal,
  never: ShieldOff,
} satisfies Record<PaymentApprovalMode, typeof ShieldCheck>;

export default function RulesPage() {
  const agents = useAgents();
  const policies = usePaymentPolicies();
  const policyByAgent = useMemo(
    () => new Map((policies.data ?? []).map((policy) => [policy.agent_id, policy])),
    [policies.data],
  );

  const loading = agents.isLoading || policies.isLoading;
  const error = agents.error ?? policies.error;

  return (
    <>
      <PageHeader
        eyebrow="Supervised autonomy"
        title="Approval rules"
        description="Choose which purchases each agent can approve automatically and which must wait for your review."
      />

      <div className="mb-6 grid gap-3 lg:grid-cols-2">
        <div className="flex gap-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
          <ShieldCheck className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Rules decide the approval step</p>
            <p className="mt-1 leading-5">
              For a proposal with a configured checkout adapter, automatic approval also queues
              the trusted executor. Unsupported or legacy proposals retain external completion.
            </p>
          </div>
        </div>
        <div className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <TriangleAlert className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Automatic approval is legacy-only</p>
            <p className="mt-1 leading-5">
              It uses an active card assigned to the agent for external-completion proposals.
              Managed browser checkout always requires your explicit approval; currency
              mismatches also require review.
            </p>
          </div>
        </div>
      </div>

      {loading ? <LoadingState variant="cards" rows={4} label="Loading approval rules" /> : null}
      {error ? (
        <ErrorState
          title="Could not load approval rules"
          description="Check that the API is running, then try again."
          retry={() => void Promise.all([agents.refetch(), policies.refetch()])}
        />
      ) : null}
      {!loading && !error && agents.data?.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="Connect an agent first"
          description="Approval rules belong to individual agents. Connect an agent, then return here to choose its policy."
          action={
            <Button asChild>
              <Link href="/agents">Go to agents</Link>
            </Button>
          }
        />
      ) : null}

      {!loading && !error && agents.data?.length ? (
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {agents.data.map((agent) => {
            const policy = policyByAgent.get(agent.id) ?? null;
            return <AgentPolicyCard key={agent.id} agent={agent} policy={policy} />;
          })}
        </div>
      ) : null}
    </>
  );
}

function AgentPolicyCard({
  agent,
  policy,
}: {
  agent: AgentRead;
  policy: PaymentPolicyRead | null;
}) {
  const mode = policy?.mode ?? "always";
  const Icon = POLICY_ICONS[mode];

  return (
    <Card className="min-w-0">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300">
            <Bot className="size-5" aria-hidden="true" />
          </span>
          <StatusBadge status={agent.connection_state} />
        </div>
        <div className="mt-2 min-w-0">
          <CardTitle className="truncate text-lg">{agent.name}</CardTitle>
          <p className="mt-1 truncate text-sm text-muted-foreground">
            {agent.description || "No description added."}
          </p>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="rounded-xl border bg-muted/30 p-4">
          <div className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-background text-indigo-700 ring-1 ring-border dark:text-indigo-300">
              <Icon className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-medium">{paymentPolicyLabel(mode)}</p>
                {!policy ? <Badge variant="outline">Safety default</Badge> : null}
              </div>
              <p className="mt-1.5 text-sm leading-5 text-muted-foreground">
                {paymentPolicySummary(mode, policy)}
              </p>
            </div>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <dt className="text-muted-foreground">Subscriptions</dt>
            <dd className="mt-1 font-medium">
              {requiresSubscriptionApproval(mode) ? "Require approval" : "Automatic"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">One-time purchases</dt>
            <dd className="mt-1 font-medium">{oneTimeRule(mode, policy)}</dd>
          </div>
        </dl>

        {isThresholdMode(mode) ? (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200/80 bg-amber-50/70 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            Different currencies always require approval.
          </div>
        ) : null}
        <div className="flex items-start gap-2 rounded-lg border border-indigo-200/80 bg-indigo-50/70 p-3 text-xs leading-5 text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/20 dark:text-indigo-100">
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          Managed browser checkout always waits for your explicit approval.
        </div>
      </CardContent>

      <CardFooter className="justify-between gap-3">
        <p className="min-w-0 truncate text-xs text-muted-foreground">
          {policy ? `Updated ${formatDateTime(policy.updated_at)}` : "Approval required by default"}
        </p>
        <EditPaymentPolicySheet agent={agent} policy={policy} />
      </CardFooter>
    </Card>
  );
}

function thresholdLabel(policy: PaymentPolicyRead | null): string {
  if (!policy?.threshold_amount || !policy.threshold_currency) return "the configured amount";
  return formatMoney(policy.threshold_amount, policy.threshold_currency);
}

function paymentPolicySummary(
  mode: PaymentApprovalMode,
  policy: PaymentPolicyRead | null,
): string {
  const threshold = thresholdLabel(policy);
  switch (mode) {
    case "always":
      return "Every purchase requires your approval.";
    case "subscriptions_only":
      return "Subscriptions require approval; one-time purchases are approved automatically.";
    case "above_amount":
      return `Purchases over ${threshold} require approval.`;
    case "subscriptions_or_above_amount":
      return `Subscriptions and purchases over ${threshold} require approval.`;
    case "never":
      return "Purchases are approved automatically without human review.";
  }
}

function requiresSubscriptionApproval(mode: PaymentApprovalMode): boolean {
  return (
    mode === "always" ||
    mode === "subscriptions_only" ||
    mode === "subscriptions_or_above_amount"
  );
}

function oneTimeRule(mode: PaymentApprovalMode, policy: PaymentPolicyRead | null): string {
  if (mode === "always") return "Require approval";
  if (isThresholdMode(mode)) return `Approval over ${thresholdLabel(policy)}`;
  return "Automatic";
}
