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

import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/app";
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
        description="Manage provider-tokenized payment references and the personal or business billing profiles attached to them."
        actions={<AddCardDialog />}
      />

      <div className="mb-6 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        <ShieldAlert className="mt-0.5 size-4 shrink-0" />
        <p>
          Never enter raw card credentials. Managed checkout supports only server-configured
          provider references; sensitive values are retrieved inside the trusted executor and are
          never returned to the browser or agent.
        </p>
      </div>

      {cards.isLoading ? <LoadingState variant="cards" rows={4} /> : null}
      {cards.error ? (
        <ErrorState description="Payment methods could not be loaded." retry={() => cards.refetch()} />
      ) : null}
      {!cards.isLoading && !cards.error && cards.data?.length === 0 ? (
        <EmptyState
          icon={CreditCard}
          title="No payment methods"
          description="Add a configured provider reference and safe card metadata, then assign it to one or more agents."
          action={<AddCardDialog />}
        />
      ) : null}

      {cards.data?.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),24rem))] gap-6">
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
        "w-full min-w-0 max-w-96 rounded-2xl border bg-card p-3 shadow-sm transition-shadow hover:shadow-md",
        card.status === "disabled" && "opacity-70 grayscale-[.22]",
      )}
    >
      <VirtualCard card={card} />

      <div className="px-1 pt-4 pb-1">
        <div className="flex items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-700 ring-1 ring-indigo-100 dark:bg-indigo-950/60 dark:text-indigo-300 dark:ring-indigo-900">
            {card.billing_profile_type === "business" ? (
              <Building2 className="size-4" />
            ) : (
              <UserRound className="size-4" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate text-sm font-semibold">{billingName(card)}</p>
              <StatusBadge status={card.status} className="shrink-0" />
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {card.billing_profile_type === "business"
                ? `Business billing · VAT ${card.billing_details.type === "business" ? card.billing_details.vat_number : "—"}`
                : "Personal billing profile"}
            </p>
          </div>
        </div>

        <dl className="mt-4 grid gap-3 border-t pt-4 text-xs sm:grid-cols-2">
          <div className="min-w-0">
            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <Mail className="size-3.5" /> Billing email
            </dt>
            <dd className="mt-1 truncate font-medium">{card.billing_details.email}</dd>
          </div>
          <div className="min-w-0 sm:text-right">
            <dt className="text-muted-foreground">Added</dt>
            <dd className="mt-1 font-medium">{formatDateTime(card.created_at)}</dd>
          </div>
        </dl>

        <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3">
          <p className="text-xs text-muted-foreground">Assign this method from an agent’s details.</p>
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
        "relative isolate aspect-[1.586/1] w-full overflow-hidden rounded-[1.35rem] bg-gradient-to-br p-5 text-white shadow-[0_22px_50px_-28px_rgba(30,27,75,0.95)]",
        palette,
      )}
      aria-label={`${card.display_name}, ${card.card_brand}, ending in ${lastFour}`}
    >
      <span className="absolute -top-20 -right-16 -z-10 size-64 rounded-full border border-white/10 bg-white/10" />
      <span className="absolute -right-24 -bottom-32 -z-10 size-72 rounded-full border-[36px] border-fuchsia-200/10" />
      <span className="absolute inset-0 -z-10 bg-[linear-gradient(120deg,transparent_20%,rgba(255,255,255,.08)_49%,transparent_72%)]" />

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-wide">{card.display_name}</p>
          <p className="mt-0.5 truncate text-[10px] font-medium tracking-[0.16em] text-white/65 uppercase">
            {card.provider} · virtual
          </p>
        </div>
        <div className="text-right">
          <p className="text-[11px] font-bold tracking-[0.18em]">AG PAY</p>
          <p className="mt-0.5 text-[9px] tracking-[0.14em] text-white/60 uppercase">
            {card.status}
          </p>
        </div>
      </div>

      <div className="mt-[clamp(1.25rem,5vw,2.4rem)] flex items-center gap-3">
        <CardChip />
        <ContactlessMark className="size-7 text-white/75" />
      </div>

      <div className="absolute inset-x-5 bottom-5">
        <div className="flex items-end justify-between gap-4">
          <div className="min-w-0">
            <p className="font-mono text-[clamp(1rem,3.5vw,1.3rem)] leading-none font-medium tracking-[0.12em] text-white drop-shadow-sm">
              •••• •••• •••• {lastFour}
            </p>
            <div className="mt-4 flex min-w-0 items-end gap-6">
              <div className="min-w-0">
                <p className="text-[8px] tracking-[0.16em] text-white/55 uppercase">Cardholder</p>
                <p className="mt-0.5 truncate text-[11px] font-semibold tracking-wide uppercase">
                  {cardholderName(card)}
                </p>
              </div>
              <div className="shrink-0">
                <p className="text-[8px] tracking-[0.16em] text-white/55 uppercase">Expires</p>
                <p className="mt-0.5 text-[11px] font-semibold tabular-nums">
                  {String(card.expiry_month).padStart(2, "0")}/{String(card.expiry_year).slice(-2)}
                </p>
              </div>
            </div>
          </div>
          <p className="shrink-0 text-sm font-bold tracking-[0.12em] uppercase italic">
            {card.card_brand}
          </p>
        </div>
      </div>
    </div>
  );
}

function CardChip() {
  return (
    <span className="relative block h-8 w-11 overflow-hidden rounded-md border border-amber-100/70 bg-gradient-to-br from-amber-100 via-yellow-300 to-amber-500 shadow-sm" aria-hidden="true">
      <span className="absolute inset-y-0 left-1/2 w-px bg-amber-800/30" />
      <span className="absolute inset-x-0 top-1/2 h-px bg-amber-800/30" />
      <span className="absolute top-1/2 left-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-amber-800/30" />
    </span>
  );
}

function ContactlessMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none" aria-hidden="true">
      <path d="M10 11.5a6.4 6.4 0 0 1 0 9" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M15 7.2a12.4 12.4 0 0 1 0 17.6" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M20 3.3a18 18 0 0 1 0 25.4" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

function safeLastFour(value: string) {
  return /^\d{4}$/.test(value) ? value : "••••";
}

function paletteIndex(id: string) {
  return Array.from(id).reduce((total, character) => total + character.charCodeAt(0), 0) % CARD_PALETTES.length;
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
      await apiRequest<void>(`/payment-methods/${card.id}`, { method: "DELETE" });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.cards }),
        queryClient.invalidateQueries({ queryKey: ["agents"] }),
      ]);
      toast.success(`${card.display_name} disabled`);
    } catch (caught) {
      toast.error(getErrorMessage(caught, "Could not disable this payment method."));
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
            This removes all agent assignments immediately. Any approved item waiting for an
            agent may no longer complete. Historical purchase attribution is retained, and the
            current API cannot re-enable this method.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep active</AlertDialogCancel>
          <AlertDialogAction
            onClick={disable}
            disabled={submitting}
            className="bg-destructive text-white hover:bg-destructive/90"
          >
            {submitting ? <Loader2 className="animate-spin" /> : <ShieldAlert />}
            Disable method
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
