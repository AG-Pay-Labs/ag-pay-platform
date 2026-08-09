"use client";

import Link from "next/link";
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  CreditCard,
  ExternalLink,
  ReceiptText,
  RefreshCw,
  ShoppingBasket,
} from "lucide-react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  Money,
  PageHeader,
  StatCard,
  StatusBadge,
} from "@/components/app";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  useAgents,
  useCartItems,
  usePaymentMethods,
  usePurchases,
  useSubscriptions,
} from "@/hooks/use-api-data";
import { hostname, relativeTime } from "@/utils/format";

export default function OverviewPage() {
  const agents = useAgents();
  const cards = usePaymentMethods();
  const cart = useCartItems();
  const purchases = usePurchases();
  const subscriptions = useSubscriptions();

  const allLoading = [agents, cards, cart, subscriptions].some((query) => query.isLoading);
  const anyError = [agents, cards, cart, purchases, subscriptions].find((query) => query.error);
  const pending = (cart.data ?? []).filter((item) => item.status === "proposed");
  const onlineAgents = (agents.data ?? []).filter(
    (agent) => agent.connection_state === "online",
  );
  const activeCards = (cards.data ?? []).filter((card) => card.status === "active");
  const activeSubscriptions = (subscriptions.data ?? []).filter(
    (subscription) => subscription.status === "active",
  );
  const firstRun =
    !allLoading &&
    (agents.data?.length ?? 0) === 0 &&
    (cards.data?.length ?? 0) === 0 &&
    (cart.data?.length ?? 0) === 0;

  async function retry() {
    await Promise.all([
      agents.refetch(),
      cards.refetch(),
      cart.refetch(),
      purchases.refetch(),
      subscriptions.refetch(),
    ]);
  }

  return (
    <>
      <PageHeader
        eyebrow="Control plane"
        title="Overview"
        description="Monitor connected agents, decisions that need you, and confirmed checkout outcomes."
        actions={
          <Button variant="outline" onClick={retry} disabled={allLoading}>
            <RefreshCw className={allLoading ? "animate-spin" : ""} />
            Refresh
          </Button>
        }
      />

      {anyError ? (
        <ErrorState
          className="mb-6"
          title="Some workspace data could not be loaded"
          description="The API may be unavailable, or your session may have expired."
          retry={retry}
        />
      ) : null}

      {allLoading ? (
        <LoadingState variant="cards" rows={4} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Needs review"
            value={pending.length}
            description={pending.length ? "Waiting for your decision" : "Inbox is clear"}
            icon={ShoppingBasket}
            tone="amber"
          />
          <StatCard
            label="Agents online"
            value={onlineAgents.length}
            description={`${agents.data?.length ?? 0} total agent${agents.data?.length === 1 ? "" : "s"}`}
            icon={Bot}
            tone="emerald"
          />
          <StatCard
            label="Active cards"
            value={activeCards.length}
            description="Tokenized references"
            icon={CreditCard}
            tone="indigo"
          />
          <StatCard
            label="Active subscriptions"
            value={activeSubscriptions.length}
            description="Tracked locally"
            icon={RefreshCw}
            tone="zinc"
          />
        </div>
      )}

      {firstRun ? <SetupChecklist /> : null}

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)]">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle>Needs your decision</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Proposals outside your automatic approval rules wait here.
              </p>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/approvals">
                View all <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {cart.isLoading ? (
              <LoadingState rows={3} />
            ) : pending.length === 0 ? (
              <EmptyState
                compact
                icon={CheckCircle2}
                title="Nothing waiting"
                description="New proposals from connected agents will appear here."
              />
            ) : (
              <div className="divide-y">
                {pending.slice(0, 4).map((item) => {
                  const agent = agents.data?.find((candidate) => candidate.id === item.agent_id);
                  return (
                    <div key={item.id} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate font-medium">{item.title}</p>
                          {item.billing_period ? (
                            <StatusBadge status="approved" label={item.billing_period} />
                          ) : null}
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {agent?.name ?? "Unknown agent"} · {item.merchant ?? hostname(item.product_url)} ·{" "}
                          {relativeTime(item.created_at)}
                        </p>
                      </div>
                      <div className="flex items-center justify-between gap-3 sm:justify-end">
                        <Money amount={item.total_amount} currency={item.currency} className="text-base" />
                        <Button size="sm" asChild>
                          <Link href={`/approvals?item=${item.id}`}>Review</Link>
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Agent health</CardTitle>
            <p className="text-sm text-muted-foreground">Connection state from recent heartbeats.</p>
          </CardHeader>
          <CardContent>
            {agents.isLoading ? (
              <LoadingState variant="inline" />
            ) : (agents.data?.length ?? 0) === 0 ? (
              <EmptyState
                compact
                icon={Bot}
                title="No agents connected"
                description="Create an agent to begin receiving purchase proposals."
                action={
                  <Button asChild>
                    <Link href="/agents">Connect agent</Link>
                  </Button>
                }
              />
            ) : (
              <div className="space-y-4">
                {agents.data?.slice(0, 5).map((agent, index) => (
                  <div key={agent.id}>
                    {index ? <Separator className="mb-4" /> : null}
                    <div className="flex items-center gap-3">
                      <span className="flex size-9 items-center justify-center rounded-lg bg-muted">
                        <Bot className="size-4 text-muted-foreground" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{agent.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {agent.last_seen_at ? `Seen ${relativeTime(agent.last_seen_at)}` : "Not paired yet"}
                        </p>
                      </div>
                      <StatusBadge status={agent.connection_state} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>Recent purchases</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Successful checkout outcomes recorded by AG Pay.
            </p>
          </div>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/purchases">
              Purchase history <ArrowRight />
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          {purchases.isLoading ? (
            <LoadingState rows={3} />
          ) : (purchases.data?.length ?? 0) === 0 ? (
            <EmptyState
              compact
              icon={ReceiptText}
              title="No purchases recorded"
              description="Approved items appear here after checkout is confirmed."
            />
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {purchases.data?.slice(0, 3).map((purchase) => (
                <div key={purchase.id} className="rounded-lg border p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{purchase.title}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {hostname(purchase.product_url)} · {relativeTime(purchase.purchased_at)}
                      </p>
                    </div>
                    <StatusBadge status={purchase.status} />
                  </div>
                  <div className="mt-5 flex items-end justify-between gap-3">
                    <Money amount={purchase.amount} currency={purchase.currency} className="text-lg" />
                    <Button variant="ghost" size="icon-sm" asChild>
                      <a href={purchase.product_url} target="_blank" rel="noreferrer" aria-label={`Open ${purchase.title} product page`}>
                        <ExternalLink />
                      </a>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function SetupChecklist() {
  const steps = [
    { label: "Connect your first agent", href: "/agents", icon: Bot },
    { label: "Add a provider reference", href: "/cards", icon: CreditCard },
    { label: "Assign the card to the agent", href: "/agents", icon: CheckCircle2 },
    { label: "Wait for the first proposal", href: "/approvals", icon: Clock3 },
  ];

  return (
    <Card className="mt-6 border-indigo-200 bg-indigo-50/50 dark:border-indigo-900 dark:bg-indigo-950/20">
      <CardHeader>
        <CardTitle>Set up your first purchase flow</CardTitle>
        <p className="text-sm text-muted-foreground">
          Four small steps establish the supervised approval loop.
        </p>
      </CardHeader>
      <CardContent>
        <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {steps.map((step, index) => (
            <li key={step.label}>
              <Link href={step.href} className="group flex h-full gap-3 rounded-lg border bg-card p-4 transition-colors hover:border-indigo-300 hover:bg-indigo-50 dark:hover:border-indigo-800 dark:hover:bg-indigo-950/30">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
                  {index + 1}
                </span>
                <span>
                  <step.icon className="mb-2 size-4 text-muted-foreground" />
                  <span className="block text-sm font-medium">{step.label}</span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
