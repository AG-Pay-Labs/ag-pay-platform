"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import type { CartItemRead, CheckoutExecutionStatus } from "@/lib/api-types";

const TERMINAL_EXECUTION_STATUSES = new Set<CheckoutExecutionStatus>([
  "succeeded",
  "failed",
  "action_required",
  "outcome_unknown",
]);

type OutcomeNotice = {
  kind: "success" | "error" | "warning";
  title: string;
  description: string;
};

export function CheckoutOutcomeNotifier({ items }: { items: CartItemRead[] | undefined }) {
  const router = useRouter();
  const initialized = useRef(false);
  const seenOutcomes = useRef(new Set<string>());

  useEffect(() => {
    if (!items) return;

    for (const item of items) {
      const execution = item.execution;
      if (!execution || !TERMINAL_EXECUTION_STATUSES.has(execution.status)) continue;

      const outcomeKey = checkoutOutcomeKey(item);
      if (initialized.current && !seenOutcomes.current.has(outcomeKey)) {
        const notice = checkoutOutcomeNotice(item);
        const options = {
          id: `checkout-outcome-${outcomeKey}`,
          description: notice.description,
          duration: 12_000,
          action: {
            label: "Review",
            onClick: () => router.push(`/approvals?item=${encodeURIComponent(item.id)}`),
          },
        };

        if (notice.kind === "success") toast.success(notice.title, options);
        else if (notice.kind === "error") toast.error(notice.title, options);
        else toast.warning(notice.title, options);
      }

      seenOutcomes.current.add(outcomeKey);
    }

    initialized.current = true;
  }, [items, router]);

  return null;
}

function checkoutOutcomeKey(item: CartItemRead): string {
  const execution = item.execution;
  if (!execution) return item.id;

  const transition = [...execution.status_history]
    .reverse()
    .find((candidate) => candidate.status === execution.status);
  const occurredAt = transition?.occurred_at ?? execution.completed_at ?? execution.updated_at;
  return `${execution.id}-${execution.status}-${occurredAt}`;
}

function checkoutOutcomeNotice(item: CartItemRead): OutcomeNotice {
  const execution = item.execution;
  if (!execution) {
    return {
      kind: "warning",
      title: "Checkout status changed",
      description: item.title,
    };
  }

  const detail = execution.error_message
    ? `${item.title}: ${execution.error_message}`
    : item.title;

  switch (execution.status) {
    case "succeeded":
      return {
        kind: "success",
        title: "Checkout confirmed",
        description: `${item.title} was verified and added to purchase history.`,
      };
    case "failed":
      return {
        kind: "error",
        title: "Checkout failed",
        description: execution.error_message
          ? detail
          : `${item.title}: no successful purchase was recorded.`,
      };
    case "action_required":
      return {
        kind: "warning",
        title: "Checkout needs your action",
        description: execution.error_message
          ? detail
          : `${item.title}: the merchant requested an interactive step.`,
      };
    case "outcome_unknown":
      return {
        kind: "warning",
        title: "Checkout outcome is uncertain",
        description: execution.error_message
          ? detail
          : `${item.title}: reconcile this payment before attempting it again.`,
      };
    case "queued":
    case "running":
      return {
        kind: "warning",
        title: "Checkout status changed",
        description: item.title,
      };
  }
}
