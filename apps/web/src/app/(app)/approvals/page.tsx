"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Bot,
  CalendarClock,
  CheckCircle2,
  ExternalLink,
  Search,
  ShieldCheck,
  ShoppingBasket,
} from "lucide-react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  Money,
  PageHeader,
  ResponsiveEntityList,
  StatusBadge,
} from "@/components/app";
import {
  ApproveDialog,
  CancelProposalDialog,
  RevealCredentialDialog,
} from "@/components/features/approvals/approval-actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgents, useCartItems } from "@/hooks/use-api-data";
import type { CartItemRead, CartItemStatus } from "@/lib/api-types";
import { formatDateTime, hostname, relativeTime } from "@/utils/format";

type Queue = "review" | "approved" | "history";

const QUEUE_STATUSES: Record<Queue, CartItemStatus[]> = {
  review: ["proposed"],
  approved: ["approved"],
  history: ["purchased", "cancelled"],
};

export default function ApprovalsPage() {
  return (
    <Suspense fallback={<LoadingState rows={5} />}>
      <ApprovalsContent />
    </Suspense>
  );
}

function ApprovalsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const cart = useCartItems();
  const agents = useAgents();
  const [queue, setQueue] = useState<Queue>("review");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const effectiveSelectedId = selectedId ?? searchParams.get("item");

  const counts = useMemo(() => {
    const items = cart.data ?? [];
    return {
      review: items.filter((item) => item.status === "proposed").length,
      approved: items.filter((item) => item.status === "approved").length,
      history: items.filter((item) => ["purchased", "cancelled"].includes(item.status)).length,
    };
  }, [cart.data]);

  const items = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return (cart.data ?? []).filter((item) => {
      if (!QUEUE_STATUSES[queue].includes(item.status)) return false;
      if (!normalized) return true;
      const agent = agents.data?.find((candidate) => candidate.id === item.agent_id);
      return [item.title, item.merchant, item.description, item.reason, agent?.name]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(normalized));
    });
  }, [agents.data, cart.data, queue, search]);

  const selected = cart.data?.find((item) => item.id === effectiveSelectedId) ?? null;
  const selectedAgent = agents.data?.find((agent) => agent.id === selected?.agent_id);
  const loading = cart.isLoading || agents.isLoading;
  const error = cart.error ?? agents.error;

  function agentFor(item: CartItemRead) {
    return agents.data?.find((agent) => agent.id === item.agent_id);
  }

  return (
    <>
      <PageHeader
        eyebrow="Human approval"
        title="Purchase approvals"
        description="Review what agents want to buy. Approval selects an assigned card and authorizes the agent to complete checkout externally."
      />

      <Card>
        <CardContent className="p-4 sm:p-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <Tabs value={queue} onValueChange={(value) => setQueue(value as Queue)}>
              <TabsList className="h-auto w-full justify-start overflow-x-auto sm:w-auto">
                <TabsTrigger value="review">
                  Needs review
                  {counts.review ? (
                    <span className="ml-1 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-900 dark:bg-amber-950 dark:text-amber-200">
                      {counts.review}
                    </span>
                  ) : null}
                </TabsTrigger>
                <TabsTrigger value="approved">Approved · {counts.approved}</TabsTrigger>
                <TabsTrigger value="history">History · {counts.history}</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="relative w-full lg:max-w-xs">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search proposals"
                className="pl-9"
                aria-label="Search purchase proposals"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="mt-5">
        {loading ? (
          <LoadingState rows={5} />
        ) : error ? (
          <ErrorState
            title="Could not load approvals"
            description="Check that the API is running and try again."
            retry={() => void Promise.all([cart.refetch(), agents.refetch()])}
          />
        ) : (
          <ResponsiveEntityList
            items={items}
            getKey={(item) => item.id}
            caption={`${queue} purchase proposals`}
            emptyState={
              <EmptyState
                icon={queue === "review" ? CheckCircle2 : ShoppingBasket}
                title={search ? "No matching proposals" : emptyTitle(queue)}
                description={
                  search
                    ? "Try a different title, merchant, or agent name."
                    : emptyDescription(queue)
                }
              />
            }
            columns={[
              {
                id: "proposal",
                header: "Proposal",
                cell: (item) => (
                  <div className="max-w-md">
                    <button
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      className="block max-w-full truncate text-left text-sm font-medium hover:text-primary hover:underline"
                    >
                      {item.title}
                    </button>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {item.merchant ?? hostname(item.product_url)} · Qty {item.quantity}
                    </p>
                  </div>
                ),
              },
              {
                id: "agent",
                header: "Agent",
                cell: (item) => (
                  <span className="inline-flex items-center gap-2 text-sm">
                    <Bot className="size-4 text-muted-foreground" />
                    {agentFor(item)?.name ?? "Unknown agent"}
                  </span>
                ),
              },
              {
                id: "submitted",
                header: "Submitted",
                cell: (item) => (
                  <span className="text-sm text-muted-foreground">
                    {relativeTime(item.created_at)}
                  </span>
                ),
              },
              {
                id: "status",
                header: "Status",
                cell: (item) => <StatusBadge status={item.status} />,
              },
              {
                id: "amount",
                header: "Amount",
                align: "right",
                cell: (item) => (
                  <div>
                    <Money amount={item.total_amount} currency={item.currency} />
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.billing_period ?? "one-time"}
                    </p>
                  </div>
                ),
              },
              {
                id: "action",
                header: <span className="sr-only">Actions</span>,
                align: "right",
                cell: (item) => (
                  <Button variant="outline" size="sm" onClick={() => setSelectedId(item.id)}>
                    Review
                  </Button>
                ),
              },
            ]}
            renderMobile={(item) => (
              <button
                type="button"
                onClick={() => setSelectedId(item.id)}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{item.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {agentFor(item)?.name ?? "Unknown agent"} · {relativeTime(item.created_at)}
                    </p>
                  </div>
                  <StatusBadge status={item.status} />
                </div>
                <div className="mt-4 flex items-end justify-between gap-3">
                  <span className="text-xs text-muted-foreground">
                    {item.merchant ?? hostname(item.product_url)}
                  </span>
                  <Money amount={item.total_amount} currency={item.currency} />
                </div>
              </button>
            )}
          />
        )}
      </div>

      <Sheet
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedId(null);
            if (searchParams.has("item")) router.replace("/approvals", { scroll: false });
          }
        }}
      >
        <SheetContent className="w-full overflow-y-auto p-0 sm:max-w-xl">
          {selected ? (
            <>
              <SheetHeader className="border-b p-6 text-left">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={selected.status} />
                  {selected.billing_period ? (
                    <StatusBadge
                      status="approved"
                      label={`Recurring · ${selected.billing_period}`}
                      showDot={false}
                    />
                  ) : null}
                </div>
                <SheetTitle className="mt-2 pr-8 text-2xl">{selected.title}</SheetTitle>
                <SheetDescription>
                  Proposed by {selectedAgent?.name ?? "an unknown agent"} {relativeTime(selected.created_at)}
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-6 p-6">
                <div className="flex items-end justify-between gap-4 rounded-xl bg-muted/60 p-4">
                  <div>
                    <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      Proposed total
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {selected.quantity} × {selected.currency.toUpperCase()} {selected.unit_price}
                    </p>
                  </div>
                  <Money
                    amount={selected.total_amount}
                    currency={selected.currency}
                    className="text-2xl"
                  />
                </div>

                <DetailSection title="What the agent is buying">
                  <p className="text-sm leading-6">{selected.description}</p>
                  <Button variant="outline" size="sm" asChild>
                    <Link href={selected.product_url} target="_blank" rel="noreferrer">
                      Open product page <ExternalLink />
                    </Link>
                  </Button>
                </DetailSection>

                <DetailSection title="Why this purchase">
                  <p className="rounded-lg border-l-2 border-indigo-400 bg-indigo-50/70 p-3 text-sm leading-6 text-indigo-950 dark:bg-indigo-950/30 dark:text-indigo-100">
                    {selected.reason}
                  </p>
                </DetailSection>

                <DetailSection title="Purchase context">
                  <dl className="grid gap-4 text-sm sm:grid-cols-2">
                    <Detail label="Merchant" value={selected.merchant ?? hostname(selected.product_url)} />
                    <Detail label="Agent" value={selectedAgent?.name ?? "Unknown agent"} />
                    <Detail label="Merchant account" value={selected.account_email} />
                    <Detail label="Submitted" value={formatDateTime(selected.created_at)} />
                  </dl>
                  <RevealCredentialDialog item={selected} />
                </DetailSection>

                {selected.decision_note ? (
                  <DetailSection title="Decision note">
                    <p className="text-sm leading-6">{selected.decision_note}</p>
                  </DetailSection>
                ) : null}

                <Separator />

                {selected.status === "proposed" ? (
                  <div>
                    <div className="mb-4 flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                      <ShieldCheck className="mt-0.5 size-4 shrink-0" />
                      <p>
                        Review the product, rationale, total, and recurring terms before you decide.
                      </p>
                    </div>
                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                      <CancelProposalDialog item={selected} />
                      <ApproveDialog item={selected} agent={selectedAgent} />
                    </div>
                  </div>
                ) : selected.status === "approved" ? (
                  <div className="flex gap-3 rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
                    <CalendarClock className="mt-0.5 size-5 shrink-0" />
                    <div>
                      <p className="font-medium">Waiting for the agent</p>
                      <p className="mt-1 leading-6">
                        Approved {selected.approved_at ? relativeTime(selected.approved_at) : "recently"}.
                        The agent must complete checkout externally and report the result.
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words font-medium">{value}</dd>
    </div>
  );
}

function emptyTitle(queue: Queue) {
  if (queue === "review") return "You are all caught up";
  if (queue === "approved") return "No purchases waiting on agents";
  return "No decision history yet";
}

function emptyDescription(queue: Queue) {
  if (queue === "review") return "New agent proposals will appear here for human approval.";
  if (queue === "approved") return "Approved proposals remain here until an agent reports completion.";
  return "Purchased and cancelled proposals will appear here.";
}
