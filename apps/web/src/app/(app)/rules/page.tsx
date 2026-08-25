"use client";

import { ArrowRight, Bot, ShieldCheck, SlidersHorizontal } from "lucide-react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/app";
import {
  isThresholdMode,
  paymentPolicyLabel,
  RuleSetSheet,
} from "@/components/features/rules/edit-payment-policy-sheet";
import { Badge } from "@/components/ui/badge";
import { useAgents, usePaymentRuleSets } from "@/hooks/use-api-data";
import type { AgentRead, PaymentRuleSetRead } from "@/lib/api-types";
import { formatMoney } from "@/components/app/money";
import { formatDateTime } from "@/utils/format";

export default function RulesPage() {
  const agents = useAgents();
  const ruleSets = usePaymentRuleSets();
  const loading = agents.isLoading || ruleSets.isLoading;
  const error = agents.error ?? ruleSets.error;
  const agentList = agents.data ?? [];
  const assignedAgentIds = new Set(
    (ruleSets.data ?? []).flatMap((ruleSet) => ruleSet.assigned_agent_ids)
  );
  const unassignedCount = agentList.filter(
    (agent) => !assignedAgentIds.has(agent.id)
  ).length;

  return (
    <>
      <PageHeader
        eyebrow="Policy-scoped autonomy"
        title="Approval rule sets"
        description="Create reusable approval policies and assign each one to the agents that should follow it."
        actions={<RuleSetSheet agents={agentList} />}
      />

      {loading ? (
        <LoadingState variant="cards" rows={4} label="Loading rule sets" />
      ) : null}
      {error ? (
        <ErrorState
          title="Could not load rule sets"
          description="Check that the API is running, then try again."
          retry={() => void Promise.all([agents.refetch(), ruleSets.refetch()])}
        />
      ) : null}
      {!loading && !error && ruleSets.data?.length === 0 ? (
        <EmptyState
          icon={SlidersHorizontal}
          title="Create your first rule set"
          description="Define when approval is required, then assign the rule set to one or more agents."
          action={<RuleSetSheet agents={agentList} />}
        />
      ) : null}

      {!loading && !error && ruleSets.data?.length ? (
        <div className="overflow-hidden rounded-2xl border bg-card shadow-sm">
          <div className="divide-y">
            {ruleSets.data.map((ruleSet) => (
              <RuleSetRow
                key={ruleSet.id}
                ruleSet={ruleSet}
                agents={agentList}
              />
            ))}
          </div>
          {unassignedCount ? (
            <div className="flex items-center gap-2 border-t bg-muted/25 px-4 py-3 text-xs text-muted-foreground sm:px-5">
              <ShieldCheck className="size-3.5" aria-hidden="true" />
              {unassignedCount}{" "}
              {unassignedCount === 1 ? "agent uses" : "agents use"} the safe
              default: always require approval.
            </div>
          ) : null}
        </div>
      ) : null}

      {!loading && !error && agentList.length === 0 ? (
        <div className="mt-4 flex items-center gap-3 rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
          <Bot className="size-4" aria-hidden="true" />
          Add an agent before assigning rule sets.
        </div>
      ) : null}
    </>
  );
}

function RuleSetRow({
  ruleSet,
  agents,
}: {
  ruleSet: PaymentRuleSetRead;
  agents: AgentRead[];
}) {
  const assignedAgents = agents.filter((agent) =>
    ruleSet.assigned_agent_ids.includes(agent.id)
  );

  return (
    <RuleSetSheet
      ruleSet={ruleSet}
      agents={agents}
      trigger={
        <button
          type="button"
          className="group flex w-full items-center gap-3 px-4 py-4 text-left outline-none transition-colors hover:bg-muted/35 focus-visible:bg-muted/50 sm:gap-4 sm:px-5"
          aria-label={`Edit ${ruleSet.name}`}
        >
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300">
            <SlidersHorizontal className="size-4" aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">
                {ruleSet.name}
              </span>
              <Badge variant="outline" className="font-normal">
                {paymentPolicyLabel(ruleSet.mode)}
              </Badge>
            </span>
            <span className="mt-1 block truncate text-xs text-muted-foreground">
              {ruleSetSummary(ruleSet)} · {assignedAgentSummary(assignedAgents)}
            </span>
          </span>
          <span className="hidden shrink-0 text-right sm:block">
            <span className="block text-xs font-medium text-foreground">
              {ruleSet.assigned_agent_ids.length}{" "}
              {ruleSet.assigned_agent_ids.length === 1 ? "agent" : "agents"}
            </span>
            <span className="mt-0.5 block text-[11px] text-muted-foreground">
              Updated {formatDateTime(ruleSet.updated_at)}
            </span>
          </span>
          <ArrowRight
            className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
            aria-hidden="true"
          />
        </button>
      }
    />
  );
}

function ruleSetSummary(ruleSet: PaymentRuleSetRead): string {
  if (!isThresholdMode(ruleSet.mode)) {
    if (ruleSet.mode === "always") return "Every purchase requires approval";
    if (ruleSet.mode === "subscriptions_only")
      return "Subscriptions require approval";
    return "Purchases are approved automatically";
  }
  if (!ruleSet.threshold_amount || !ruleSet.threshold_currency) {
    return "Approval threshold configured";
  }
  return `Approval above ${formatMoney(
    ruleSet.threshold_amount,
    ruleSet.threshold_currency
  )}`;
}

function assignedAgentSummary(agents: AgentRead[]): string {
  if (agents.length === 0) return "No agents assigned";
  if (agents.length <= 2) return agents.map((agent) => agent.name).join(", ");
  return `${agents[0]?.name}, ${agents[1]?.name} +${agents.length - 2}`;
}
