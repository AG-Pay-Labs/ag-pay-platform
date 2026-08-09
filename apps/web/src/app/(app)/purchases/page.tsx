"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Bot,
  ExternalLink,
  FileText,
  ReceiptText,
  Search,
  ShoppingBag,
} from "lucide-react";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  Money,
  PageHeader,
  ResponsiveEntityList,
  SafeCardLabel,
  StatusBadge,
} from "@/components/app";
import { RevealCredentialDialog } from "@/components/features/approvals/approval-actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  useAgents,
  useCartItems,
  usePaymentMethods,
  usePurchases,
} from "@/hooks/use-api-data";
import type { PurchaseRead, PurchaseStatus } from "@/lib/api-types";
import { formatDateTime, hostname } from "@/utils/format";

type StatusFilter = "all" | PurchaseStatus;

export default function PurchasesPage() {
  const purchases = usePurchases();
  const agents = useAgents();
  const cards = usePaymentMethods();
  const cart = useCartItems();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const items = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return (purchases.data ?? []).filter((purchase) => {
      if (status !== "all" && purchase.status !== status) return false;
      if (!normalized) return true;
      const agent = agents.data?.find((candidate) => candidate.id === purchase.agent_id);
      return [
        purchase.title,
        purchase.description,
        purchase.provider_reference,
        purchase.account_email,
        hostname(purchase.product_url),
        agent?.name,
      ].some((value) => value?.toLowerCase().includes(normalized));
    });
  }, [agents.data, purchases.data, search, status]);

  const selected = purchases.data?.find((purchase) => purchase.id === selectedId) ?? null;
  const selectedAgent = agents.data?.find((agent) => agent.id === selected?.agent_id);
  const selectedCard = cards.data?.find((card) => card.id === selected?.payment_method_id);
  const selectedCartItem = cart.data?.find((item) => item.id === selected?.cart_item_id);
  const loading = purchases.isLoading || agents.isLoading || cards.isLoading;
  const error = purchases.error ?? agents.error ?? cards.error;

  function agentFor(purchase: PurchaseRead) {
    return agents.data?.find((agent) => agent.id === purchase.agent_id);
  }

  return (
    <>
      <PageHeader
        eyebrow="Audit trail"
        title="Purchases"
        description="A record of confirmed checkout outcomes, including purchases completed by the trusted AG Pay executor."
      />

      <Card>
        <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:p-5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search purchases, agents, or references"
              className="pl-9"
              aria-label="Search purchases"
            />
          </div>
          <Select value={status} onValueChange={(value) => setStatus(value as StatusFilter)}>
            <SelectTrigger className="h-9 w-full sm:w-44" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All outcomes</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
              <SelectItem value="refunded">Refunded</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <div className="mt-5">
        {loading ? (
          <LoadingState rows={5} />
        ) : error ? (
          <ErrorState
            title="Could not load purchase history"
            description="Check that the API is running and try again."
            retry={() => void Promise.all([purchases.refetch(), agents.refetch(), cards.refetch()])}
          />
        ) : (
          <ResponsiveEntityList
            items={items}
            getKey={(purchase) => purchase.id}
            caption="Purchase history"
            emptyState={
              <EmptyState
                icon={ShoppingBag}
                title={search || status !== "all" ? "No matching purchases" : "No purchases recorded"}
                description={
                  search || status !== "all"
                    ? "Change the search or outcome filter."
                    : "Approved items appear here after checkout is confirmed."
                }
              />
            }
            columns={[
              {
                id: "purchase",
                header: "Purchase",
                cell: (purchase) => (
                  <div className="max-w-md">
                    <button
                      type="button"
                      onClick={() => setSelectedId(purchase.id)}
                      className="block max-w-full truncate text-left text-sm font-medium hover:text-primary hover:underline"
                    >
                      {purchase.title}
                    </button>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {hostname(purchase.product_url)} · {purchase.provider_reference}
                    </p>
                  </div>
                ),
              },
              {
                id: "agent",
                header: "Agent",
                cell: (purchase) => (
                  <span className="inline-flex items-center gap-2 text-sm">
                    <Bot className="size-4 text-muted-foreground" />
                    {agentFor(purchase)?.name ?? "Unknown agent"}
                  </span>
                ),
              },
              {
                id: "date",
                header: "Purchased",
                cell: (purchase) => (
                  <span className="text-sm text-muted-foreground">
                    {formatDateTime(purchase.purchased_at)}
                  </span>
                ),
              },
              {
                id: "status",
                header: "Outcome",
                cell: (purchase) => <StatusBadge status={purchase.status} />,
              },
              {
                id: "amount",
                header: "Amount",
                align: "right",
                cell: (purchase) => (
                  <Money amount={purchase.amount} currency={purchase.currency} />
                ),
              },
              {
                id: "action",
                header: <span className="sr-only">Actions</span>,
                align: "right",
                cell: (purchase) => (
                  <Button variant="outline" size="sm" onClick={() => setSelectedId(purchase.id)}>
                    Details
                  </Button>
                ),
              },
            ]}
            renderMobile={(purchase) => (
              <button
                type="button"
                onClick={() => setSelectedId(purchase.id)}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{purchase.title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {agentFor(purchase)?.name ?? "Unknown agent"} · {hostname(purchase.product_url)}
                    </p>
                  </div>
                  <StatusBadge status={purchase.status} />
                </div>
                <div className="mt-4 flex items-end justify-between gap-3">
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(purchase.purchased_at)}
                  </span>
                  <Money amount={purchase.amount} currency={purchase.currency} />
                </div>
              </button>
            )}
          />
        )}
      </div>

      <Sheet open={Boolean(selected)} onOpenChange={(open) => !open && setSelectedId(null)}>
        <SheetContent className="w-full overflow-y-auto p-0 sm:max-w-xl">
          {selected ? (
            <>
              <SheetHeader className="border-b p-6 text-left">
                <StatusBadge status={selected.status} className="w-fit" />
                <SheetTitle className="mt-2 pr-8 text-2xl">{selected.title}</SheetTitle>
                <SheetDescription>
                  Recorded {formatDateTime(selected.purchased_at)}
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-6 p-6">
                <div className="flex items-end justify-between gap-4 rounded-xl bg-muted/60 p-4">
                  <div>
                    <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      Recorded total
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Requested by {selectedAgent?.name ?? "unknown agent"}
                    </p>
                  </div>
                  <Money amount={selected.amount} currency={selected.currency} className="text-2xl" />
                </div>

                <section className="space-y-3">
                  <h3 className="text-sm font-semibold">Purchase details</h3>
                  <p className="text-sm leading-6">{selected.description}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <Link href={selected.product_url} target="_blank" rel="noreferrer">
                        Product page <ExternalLink />
                      </Link>
                    </Button>
                    {selected.receipt_url ? (
                      <Button variant="outline" size="sm" asChild>
                        <Link href={selected.receipt_url} target="_blank" rel="noreferrer">
                          Receipt <FileText />
                        </Link>
                      </Button>
                    ) : null}
                  </div>
                </section>

                <Separator />

                <section className="space-y-3">
                  <h3 className="text-sm font-semibold">Payment method</h3>
                  {selectedCard ? (
                    <SafeCardLabel
                      brand={selectedCard.card_brand}
                      last4={selectedCard.card_last4}
                      displayName={selectedCard.display_name}
                      expiryMonth={selectedCard.expiry_month}
                      expiryYear={selectedCard.expiry_year}
                      status={selectedCard.status}
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">Payment method is no longer available.</p>
                  )}
                </section>

                <section className="space-y-3">
                  <h3 className="text-sm font-semibold">Audit information</h3>
                  <dl className="grid gap-4 text-sm sm:grid-cols-2">
                    <Detail label="Agent" value={selectedAgent?.name ?? "Unknown agent"} />
                    <Detail label="Merchant account" value={selected.account_email} />
                    {selected.merchant_order_reference ? (
                      <Detail
                        label="Merchant order"
                        value={selected.merchant_order_reference}
                        monospace
                      />
                    ) : null}
                    <Detail label="Provider reference" value={selected.provider_reference} monospace />
                    <Detail label="Purchase ID" value={selected.id} monospace />
                  </dl>
                  {selectedCartItem ? <RevealCredentialDialog item={selectedCartItem} /> : null}
                </section>

                {selected.subscription ? (
                  <div className="flex gap-3 rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
                    <ReceiptText className="mt-0.5 size-5 shrink-0" />
                    <div>
                      <p className="font-medium">Recurring purchase</p>
                      <p className="mt-1 leading-6">
                        This purchase is tracked as a {selected.subscription.billing_period} subscription.
                      </p>
                      <Button variant="link" className="mt-1 h-auto p-0" asChild>
                        <Link href="/subscriptions">View subscription</Link>
                      </Button>
                    </div>
                  </div>
                ) : null}

                <p className="text-xs leading-5 text-muted-foreground">
                  Managed checkouts are recorded only after merchant and payment-provider verification.
                  Legacy external records may still rely on an agent-supplied result. Refunds and merchant
                  subscription cancellation remain separate provider operations.
                </p>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  );
}

function Detail({
  label,
  value,
  monospace = false,
}: {
  label: string;
  value: string;
  monospace?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`mt-1 break-all font-medium ${monospace ? "font-mono text-xs" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
