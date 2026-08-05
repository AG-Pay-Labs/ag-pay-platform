"use client";

import { useMemo, useState } from "react";
import { Bot, CalendarClock, ExternalLink, RefreshCw, Search } from "lucide-react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  Money,
  PageHeader,
  ResponsiveEntityList,
  StatusBadge,
} from "@/components/app";
import { ManageSubscriptionDialog } from "@/components/features/subscriptions/manage-subscription-dialog";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAgents, usePurchases, useSubscriptions } from "@/hooks/use-api-data";
import type { SubscriptionRead, SubscriptionStatus } from "@/lib/api-types";
import { formatDate, hostname } from "@/utils/format";

type StatusFilter = "all" | SubscriptionStatus;

export default function SubscriptionsPage() {
  const subscriptions = useSubscriptions();
  const agents = useAgents();
  const purchases = usePurchases();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  const items = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return (subscriptions.data ?? []).filter((subscription) => {
      if (status !== "all" && subscription.status !== status) return false;
      if (!normalized) return true;
      const agent = agents.data?.find((candidate) => candidate.id === subscription.agent_id);
      const purchase = purchases.data?.find(
        (candidate) => candidate.id === subscription.purchase_id,
      );
      return [subscription.title, agent?.name, purchase ? hostname(purchase.product_url) : ""]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(normalized));
    });
  }, [agents.data, purchases.data, search, status, subscriptions.data]);

  const loading = subscriptions.isLoading || agents.isLoading || purchases.isLoading;
  const error = subscriptions.error ?? agents.error ?? purchases.error;

  function agentFor(subscription: SubscriptionRead) {
    return agents.data?.find((agent) => agent.id === subscription.agent_id);
  }

  function merchantFor(subscription: SubscriptionRead) {
    const purchase = purchases.data?.find(
      (candidate) => candidate.id === subscription.purchase_id,
    );
    return purchase ? hostname(purchase.product_url) : "Merchant unavailable";
  }

  return (
    <>
      <PageHeader
        eyebrow="Recurring commitments"
        title="Subscriptions"
        description="Track recurring purchases reported by agents. Status changes here do not pause or cancel billing at the merchant."
      />

      <div className="mb-5 flex gap-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
        <CalendarClock className="mt-0.5 size-5 shrink-0" />
        <p className="leading-6">
          This is a local tracking view. Use the merchant account to change the actual plan,
          renewal, or cancellation.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:p-5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search subscriptions or agents"
              className="pl-9"
              aria-label="Search subscriptions"
            />
          </div>
          <Select value={status} onValueChange={(value) => setStatus(value as StatusFilter)}>
            <SelectTrigger className="h-9 w-full sm:w-44" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="paused">Paused</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <div className="mt-5">
        {loading ? (
          <LoadingState rows={5} />
        ) : error ? (
          <ErrorState
            title="Could not load subscriptions"
            description="Check that the API is running and try again."
            retry={() =>
              void Promise.all([subscriptions.refetch(), agents.refetch(), purchases.refetch()])
            }
          />
        ) : (
          <ResponsiveEntityList
            items={items}
            getKey={(subscription) => subscription.id}
            caption="Recurring subscriptions"
            emptyState={
              <EmptyState
                icon={RefreshCw}
                title={search || status !== "all" ? "No matching subscriptions" : "No subscriptions tracked"}
                description={
                  search || status !== "all"
                    ? "Change the search or status filter."
                    : "A recurring purchase appears here after an agent reports external completion."
                }
              />
            }
            columns={[
              {
                id: "subscription",
                header: "Subscription",
                cell: (subscription) => (
                  <div className="max-w-sm">
                    <p className="truncate text-sm font-medium">{subscription.title}</p>
                    <p className="mt-1 inline-flex items-center gap-1 truncate text-xs text-muted-foreground">
                      {merchantFor(subscription)} <ExternalLink className="size-3" />
                    </p>
                  </div>
                ),
              },
              {
                id: "agent",
                header: "Agent",
                cell: (subscription) => (
                  <span className="inline-flex items-center gap-2 text-sm">
                    <Bot className="size-4 text-muted-foreground" />
                    {agentFor(subscription)?.name ?? "Unknown agent"}
                  </span>
                ),
              },
              {
                id: "renewal",
                header: "Next expected billing",
                cell: (subscription) => (
                  <div>
                    <p className="text-sm">{formatDate(subscription.next_billing_at)}</p>
                    <p className="mt-1 text-xs text-muted-foreground capitalize">
                      {subscription.billing_period}
                    </p>
                  </div>
                ),
              },
              {
                id: "status",
                header: "Tracking status",
                cell: (subscription) => <StatusBadge status={subscription.status} />,
              },
              {
                id: "amount",
                header: "Amount",
                align: "right",
                cell: (subscription) => (
                  <div>
                    <Money amount={subscription.amount} currency={subscription.currency} />
                    <p className="mt-1 text-xs text-muted-foreground">/{periodUnit(subscription.billing_period)}</p>
                  </div>
                ),
              },
              {
                id: "action",
                header: <span className="sr-only">Actions</span>,
                align: "right",
                cell: (subscription) => (
                  <ManageSubscriptionDialog subscription={subscription} />
                ),
              },
            ]}
            renderMobile={(subscription) => (
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{subscription.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {agentFor(subscription)?.name ?? "Unknown agent"} · {merchantFor(subscription)}
                    </p>
                  </div>
                  <StatusBadge status={subscription.status} />
                </div>
                <div className="mt-4 flex items-end justify-between gap-3 border-t pt-3">
                  <div>
                    <p className="text-xs text-muted-foreground">Next expected billing</p>
                    <p className="mt-1 text-sm">{formatDate(subscription.next_billing_at)}</p>
                  </div>
                  <div className="text-right">
                    <Money amount={subscription.amount} currency={subscription.currency} />
                    <p className="text-xs text-muted-foreground">/{periodUnit(subscription.billing_period)}</p>
                  </div>
                </div>
                <div className="mt-4">
                  <ManageSubscriptionDialog subscription={subscription} />
                </div>
              </div>
            )}
          />
        )}
      </div>
    </>
  );
}

function periodUnit(period: string) {
  return period === "monthly" ? "month" : "year";
}
