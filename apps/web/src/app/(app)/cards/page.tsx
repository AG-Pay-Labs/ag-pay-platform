"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  CreditCard,
  Loader2,
  Mail,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import { toast } from "sonner";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/app";
import { AddCardDialog } from "@/components/features/cards/add-card-dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type { PaymentMethodRead } from "@/lib/api-types";
import { cn } from "@/lib/utils";
import { queryKeys, usePaymentMethods } from "@/hooks/use-api-data";
import { formatDateTime } from "@/utils/format";

const CARD_PALETTES = [
  "from-slate-950 via-indigo-950 to-violet-700",
  "from-indigo-950 via-indigo-800 to-violet-500",
  "from-violet-950 via-indigo-900 to-blue-600",
] as const;

export default function CardsPage() {
  const cards = usePaymentMethods();

  return (
    <>
      <PageHeader
        eyebrow="Payment permissions"
        title="Cards"
        description="Store a card for local direct checkout research, test Stripe's sandbox, connect Stripe Link, or manage provider-tokenized cards."
        actions={<AddCardDialog />}
      />

      {cards.isLoading ? <LoadingState variant="cards" rows={4} /> : null}
      {cards.error ? (
        <ErrorState
          description="Payment methods could not be loaded."
          retry={() => cards.refetch()}
        />
      ) : null}
      {!cards.isLoading && !cards.error && cards.data?.length === 0 ? (
        <EmptyState
          icon={CreditCard}
          title="No payment methods"
          description="Store a direct card or add the Stripe sandbox card, assign it to an agent, and use it for a supervised checkout."
          action={<AddCardDialog />}
        />
      ) : null}

      {cards.data?.length ? (
        <div className="mx-auto grid w-full max-w-[76rem] gap-y-4 md:grid-cols-[repeat(2,minmax(0,22rem))] md:justify-between xl:grid-cols-[repeat(3,minmax(0,22rem))]">
          {cards.data.map((card) => (
            <PaymentMethodCard key={card.id} card={card} />
          ))}
        </div>
      ) : null}
    </>
  );
}

function PaymentMethodCard({ card }: { card: PaymentMethodRead }) {
  return (
    <article
      className={cn(
        "flex h-full min-w-0 flex-col rounded-2xl border bg-card p-2.5 shadow-sm transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-md motion-reduce:transform-none motion-reduce:transition-none",
        card.status === "disabled" && "opacity-70 grayscale-[.22]"
      )}
    >
      <VirtualCard card={card} />

      <div className="flex flex-1 flex-col px-1.5 pt-3 pb-1">
        <div className="flex items-start gap-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700 ring-1 ring-indigo-100 dark:bg-indigo-950/60 dark:text-indigo-300 dark:ring-indigo-900">
            {card.billing_profile_type === "business" ? (
              <Building2 className="size-3.5" />
            ) : (
              <UserRound className="size-3.5" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate text-sm font-semibold">
                {billingName(card)}
              </p>
              <StatusBadge status={card.status} className="shrink-0" />
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {card.billing_profile_type === "business"
                ? `Business billing · VAT ${
                    card.billing_details.type === "business"
                      ? card.billing_details.vat_number
                      : "—"
                  }`
                : "Personal billing profile"}
            </p>
          </div>
        </div>

        <dl className="mt-3 mb-3 grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-t pt-3 text-xs">
          <div className="min-w-0">
            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <Mail className="size-3.5" /> Billing email
            </dt>
            <dd className="mt-1 truncate font-medium">
              {card.billing_details.email}
            </dd>
          </div>
          <div className="min-w-0 text-right">
            <dt className="text-muted-foreground">Added</dt>
            <dd className="mt-1 whitespace-nowrap font-medium">
              {formatDateTime(card.created_at)}
            </dd>
          </div>
        </dl>

        <div className="mt-auto flex items-center justify-between gap-2 border-t pt-2.5">
          <p className="min-w-0 text-[11px] leading-4 text-muted-foreground">
            Assign this method from an agent’s details.
          </p>
          {card.status === "active" ? <DisableCardDialog card={card} /> : null}
        </div>
      </div>
    </article>
  );
}

function VirtualCard({ card }: { card: PaymentMethodRead }) {
  const palette = CARD_PALETTES[paletteIndex(card.id)];
  const lastFour = safeLastFour(card.card_last4);

  return (
    <div
      className={cn(
        "relative isolate aspect-[1.586/1] w-full overflow-hidden rounded-[1.15rem] bg-gradient-to-br p-4 text-white shadow-[0_18px_40px_-26px_rgba(30,27,75,0.95)]",
        palette
      )}
      aria-label={`${card.display_name}, ${card.card_brand}, ending in ${lastFour}`}
    >
      <span className="absolute -top-20 -right-16 -z-10 size-64 rounded-full border border-white/10 bg-white/10" />
      <span className="absolute -right-24 -bottom-32 -z-10 size-72 rounded-full border-[36px] border-fuchsia-200/10" />
      <span className="absolute inset-0 -z-10 bg-[linear-gradient(120deg,transparent_20%,rgba(255,255,255,.08)_49%,transparent_72%)]" />

      <div className="flex h-full flex-col">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-[13px] font-semibold tracking-wide">
              {card.display_name}
            </p>
            <p className="mt-0.5 truncate text-[9px] font-medium tracking-[0.15em] text-white/65 uppercase">
              {card.provider === "local_direct_card"
                ? "Encrypted direct card"
                : `${card.provider} · provider reference`}
            </p>
          </div>
          <div className="shrink-0 text-right">
            <p className="text-[10px] font-bold tracking-[0.17em]">AG PAY</p>
            <p className="mt-0.5 text-[8px] tracking-[0.13em] text-white/60 uppercase">
              {card.status}
            </p>
          </div>
        </div>

        <div className="mt-[clamp(1rem,3vw,1.6rem)] flex items-center gap-2.5">
          <CardChip />
          <ContactlessMark className="size-6 text-white/75" />
        </div>

        <p className="mt-2.5 truncate font-mono text-[clamp(.9rem,1.6vw,1.05rem)] leading-none font-medium tracking-[0.1em] text-white drop-shadow-sm">
          •••• •••• •••• {lastFour}
        </p>

        <div className="mt-auto grid grid-cols-[minmax(0,1fr)_auto_auto] items-end gap-3">
          <div className="min-w-0">
            <p className="text-[7px] tracking-[0.15em] text-white/55 uppercase">
              Cardholder
            </p>
            <p className="mt-0.5 truncate text-[10px] font-semibold tracking-wide uppercase">
              {cardholderName(card)}
            </p>
          </div>
          <div className="shrink-0">
            <p className="text-[7px] tracking-[0.15em] text-white/55 uppercase">
              Expires
            </p>
            <p className="mt-0.5 text-[10px] font-semibold tabular-nums">
              {String(card.expiry_month).padStart(2, "0")}/
              {String(card.expiry_year).slice(-2)}
            </p>
          </div>
          <p className="max-w-16 truncate text-right text-xs font-bold tracking-[0.1em] uppercase italic">
            {card.card_brand}
          </p>
        </div>
      </div>
    </div>
  );
}

function CardChip() {
  return (
    <span
      className="relative block h-7 w-10 overflow-hidden rounded-md border border-amber-100/70 bg-gradient-to-br from-amber-100 via-yellow-300 to-amber-500 shadow-sm"
      aria-hidden="true"
    >
      <span className="absolute inset-y-0 left-1/2 w-px bg-amber-800/30" />
      <span className="absolute inset-x-0 top-1/2 h-px bg-amber-800/30" />
      <span className="absolute top-1/2 left-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-amber-800/30" />
    </span>
  );
}

function ContactlessMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M10 11.5a6.4 6.4 0 0 1 0 9"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <path
        d="M15 7.2a12.4 12.4 0 0 1 0 17.6"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <path
        d="M20 3.3a18 18 0 0 1 0 25.4"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function safeLastFour(value: string) {
  return /^\d{4}$/.test(value) ? value : "••••";
}

function paletteIndex(id: string) {
  return (
    Array.from(id).reduce(
      (total, character) => total + character.charCodeAt(0),
      0
    ) % CARD_PALETTES.length
  );
}

function cardholderName(card: PaymentMethodRead) {
  return card.billing_details.type === "business"
    ? card.billing_details.contact_name
    : card.billing_details.full_name;
}

function billingName(card: PaymentMethodRead) {
  return card.billing_details.type === "business"
    ? card.billing_details.legal_name
    : card.billing_details.full_name;
}

function DisableCardDialog({ card }: { card: PaymentMethodRead }) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function disable() {
    setSubmitting(true);
    try {
      await apiRequest<void>(`/payment-methods/${card.id}`, {
        method: "DELETE",
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.cards }),
        queryClient.invalidateQueries({ queryKey: ["agents"] }),
      ]);
      toast.success(`${card.display_name} disabled`);
    } catch (caught) {
      toast.error(
        getErrorMessage(caught, "Could not disable this payment method.")
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive">
          Disable
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Disable {card.display_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes all agent assignments immediately. Any approved item
            waiting for an agent may no longer complete. Historical purchase
            attribution is retained, and the current API cannot re-enable this
            method.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep active</AlertDialogCancel>
          <AlertDialogAction
            onClick={disable}
            disabled={submitting}
            className="bg-destructive text-white hover:bg-destructive/90"
          >
            {submitting ? (
              <Loader2 className="animate-spin" />
            ) : (
              <ShieldAlert />
            )}
            Disable method
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
