"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Loader2, RefreshCw, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";

import { Money, SafeCardLabel } from "@/components/app";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type {
  AgentRead,
  CartApproval,
  CartItemRead,
  CredentialReveal,
  PaymentMethodRead,
} from "@/lib/api-types";
import { queryKeys, useAgentPaymentMethods } from "@/hooks/use-api-data";

function isPaymentMethodUnexpired(
  paymentMethod: Pick<PaymentMethodRead, "expiry_month" | "expiry_year">,
  now = new Date(),
) {
  const expiryUtcMonth = paymentMethod.expiry_year * 12 + paymentMethod.expiry_month - 1;
  const currentUtcMonth = now.getUTCFullYear() * 12 + now.getUTCMonth();
  return expiryUtcMonth >= currentUtcMonth;
}

export function ApproveDialog({ item, agent }: { item: CartItemRead; agent?: AgentRead }) {
  const queryClient = useQueryClient();
  const hasManagedCheckout = Boolean(item.checkout_adapter && item.checkout_url);
  const [open, setOpen] = useState(false);
  const [selectedCard, setSelectedCard] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assigned = useAgentPaymentMethods(item.agent_id, open);
  const selectableCards = useMemo(
    () => {
      const now = new Date();
      return (assigned.data ?? []).filter(
        (card) =>
          card.status === "active" &&
          isPaymentMethodUnexpired(card, now) &&
          (hasManagedCheckout || card.provider !== "local_direct_card"),
      );
    },
    [assigned.data, hasManagedCheckout],
  );
  const selectedPaymentMethod = selectableCards.find(
    (card) => card.id === selectedCard,
  );
  const requiresCvc =
    hasManagedCheckout && selectedPaymentMethod?.provider === "local_direct_card";

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCard) {
      setError("Select an assigned payment method.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const cvcInput = event.currentTarget.elements.namedItem("cvc");
    let cvc = requiresCvc ? String(form.get("cvc") ?? "").trim() : undefined;
    if (requiresCvc && !/^\d{3,4}$/.test(cvc ?? "")) {
      setError("Enter the 3 or 4 digit CVC for the selected direct card.");
      if (cvcInput instanceof HTMLInputElement) cvcInput.focus();
      return;
    }
    setSubmitting(true);
    setError(null);

    try {
      const payload: CartApproval = {
        payment_method_id: selectedCard,
        note: String(form.get("note") ?? "").trim() || null,
        ...(cvc ? { cvc } : {}),
      };
      const serializedPayload = JSON.stringify(payload);
      delete payload.cvc;
      form.delete("cvc");
      cvc = undefined;
      const request = apiRequest<CartItemRead>(`/cart-items/${item.id}/approve`, {
        method: "POST",
        body: serializedPayload,
      });
      if (cvcInput instanceof HTMLInputElement) cvcInput.value = "";
      const approved = await request;
      await queryClient.invalidateQueries({ queryKey: queryKeys.cart });
      toast.success(
        approved.execution
          ? "Purchase approved; secure checkout queued"
          : "Approval recorded. No payment or checkout was queued.",
      );
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not approve this proposal."));
      if (cvcInput instanceof HTMLInputElement) cvcInput.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (submitting) return;
        setOpen(next);
        if (!next) {
          setSelectedCard("");
          setError(null);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <ShieldCheck /> Approve
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Approve this purchase?</DialogTitle>
          <DialogDescription>
            {hasManagedCheckout ? (
              <>
                This authorizes AG Pay to run the configured checkout after approval. Payment
                credentials stay inside the trusted executor and are never sent to the agent.
              </>
            ) : (
              <>
                This proposal has no managed checkout URL. Approving records your decision and
                selected card, but AG Pay will not make or queue a payment. If you expect AG Pay to
                execute checkout, ask {agent?.name ?? "the agent"} to create a new proposal with
                the exact checkout adapter and URL.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-end justify-between gap-4 rounded-xl bg-muted/50 p-4">
          <div className="min-w-0">
            <p className="truncate font-medium">{item.title}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Quantity {item.quantity}
              {item.billing_period ? ` · Recurs ${item.billing_period}` : " · One-time"}
            </p>
          </div>
          <Money amount={item.total_amount} currency={item.currency} className="text-xl" />
        </div>

        <form id={`approve-${item.id}`} onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label>Assigned payment method</Label>
            {assigned.isLoading ? (
              <div className="flex items-center gap-2 rounded-lg border p-4 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Loading assigned cards
              </div>
            ) : selectableCards.length === 0 ? (
              <div className="rounded-lg border border-dashed p-4 text-sm">
                <p className="font-medium">
                  No compatible active, unexpired card is assigned
                </p>
                <p className="mt-1 text-muted-foreground">
                  {hasManagedCheckout
                    ? `Assign an active, unexpired card to ${agent?.name ?? "this agent"} before approving.`
                    : "Direct cards require managed checkout. Assign an active, unexpired provider-backed card to record this approval."}
                </p>
                <Button variant="outline" size="sm" className="mt-3" asChild>
                  <Link href="/agents">Manage agent cards</Link>
                </Button>
              </div>
            ) : (
              <RadioGroup value={selectedCard} onValueChange={setSelectedCard} className="space-y-2">
                {selectableCards.map((card) => (
                  <Label
                    key={card.id}
                    htmlFor={`approval-card-${card.id}`}
                    className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 has-data-[state=checked]:border-primary has-data-[state=checked]:ring-2 has-data-[state=checked]:ring-primary/15"
                  >
                    <RadioGroupItem id={`approval-card-${card.id}`} value={card.id} />
                    <SafeCardLabel
                      compact
                      displayName={card.display_name}
                      brand={card.card_brand}
                      last4={card.card_last4}
                      expiryMonth={card.expiry_month}
                      expiryYear={card.expiry_year}
                    />
                  </Label>
                ))}
              </RadioGroup>
            )}
          </div>
          {requiresCvc ? (
            <div key={selectedCard} className="space-y-1.5">
              <Label htmlFor={`approval-cvc-${item.id}`}>
                Card security code (CVC)
              </Label>
              <Input
                id={`approval-cvc-${item.id}`}
                name="cvc"
                type="password"
                inputMode="numeric"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                minLength={3}
                maxLength={4}
                pattern="[0-9]{3,4}"
                title="Enter the 3 or 4 digit card security code"
                aria-describedby={`approval-cvc-help-${item.id}`}
                className="font-mono tracking-[0.3em]"
                required
              />
              <p
                id={`approval-cvc-help-${item.id}`}
                className="text-xs leading-5 text-muted-foreground"
              >
                Required for this direct card only. It is sent with this
                approval, held briefly for the queued checkout, and never added
                to the stored card.
              </p>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor={`approval-note-${item.id}`}>Decision note</Label>
            <Textarea
              id={`approval-note-${item.id}`}
              name="note"
              placeholder="Optional context for this decision"
              rows={3}
              maxLength={2_000}
            />
          </div>
          {item.billing_period ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              This proposal creates a {item.billing_period} recurring commitment only after a
              successful checkout is confirmed.
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </form>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={submitting}
          >
            Back
          </Button>
          <Button
            type="submit"
            form={`approve-${item.id}`}
            disabled={submitting || selectableCards.length === 0}
          >
            {submitting ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            {hasManagedCheckout ? (
              <>
                Approve &amp; queue · <Money amount={item.total_amount} currency={item.currency} />
              </>
            ) : (
              "Record approval only"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CancelProposalDialog({ item }: { item: CartItemRead }) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function cancel(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    const form = new FormData(event.currentTarget);
    try {
      await apiRequest<CartItemRead>(`/cart-items/${item.id}/cancel`, {
        method: "POST",
        body: JSON.stringify({ note: String(form.get("note") ?? "").trim() || null }),
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.cart });
      toast.success("Proposal cancelled");
    } catch (caught) {
      toast.error(getErrorMessage(caught, "Could not cancel this proposal."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="outline">
          <X /> Cancel
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <form id={`cancel-${item.id}`} onSubmit={cancel}>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel this proposal?</AlertDialogTitle>
            <AlertDialogDescription>
              {item.title} will become terminal and the agent cannot purchase it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="my-4 space-y-1.5">
            <Label htmlFor={`cancel-note-${item.id}`}>Reason</Label>
            <Textarea
              id={`cancel-note-${item.id}`}
              name="note"
              placeholder="Optional decision note"
              rows={3}
              maxLength={2_000}
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep proposal</AlertDialogCancel>
            <AlertDialogAction
              type="submit"
              disabled={submitting}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {submitting ? <Loader2 className="animate-spin" /> : <X />}
              Cancel proposal
            </AlertDialogAction>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function ReconcilePaymentDialog({ item }: { item: CartItemRead }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (
    item.execution?.status !== "outcome_unknown" ||
    item.checkout_adapter !== "stripe-hosted" ||
    !item.checkout_url
  ) {
    return null;
  }

  async function reconcile(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const updated = await apiRequest<CartItemRead>(
        `/cart-items/${item.id}/checkout/reconcile`,
        { method: "POST" },
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.cart }),
        queryClient.invalidateQueries({ queryKey: queryKeys.purchases }),
      ]);

      if (updated.execution?.status !== "succeeded") {
        setError(
          "The existing payment could not be confirmed. It remains unresolved, and no new payment was submitted.",
        );
        return;
      }
      toast.success("Payment confirmed and purchase recorded");
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not reconcile this payment."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (submitting) return;
        setOpen(next);
        if (!next) setError(null);
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="outline" size="sm">
          <RefreshCw /> Reconcile payment
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <form id={`reconcile-${item.id}`} onSubmit={reconcile}>
          <AlertDialogHeader>
            <AlertDialogTitle>Reconcile this payment?</AlertDialogTitle>
            <AlertDialogDescription>
              AG Pay will check provider and merchant evidence for the existing checkout attempt
              and update its recorded outcome. It will not submit the card, retry checkout, or
              create another payment.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <p className="my-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            If the available evidence is still incomplete, the outcome will remain unresolved and
            the payment method will stay quarantined.
          </p>
          {error ? (
            <p role="alert" className="mb-4 text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel type="button" disabled={submitting}>
              Cancel
            </AlertDialogCancel>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              {submitting ? "Checking existing payment…" : "Reconcile payment"}
            </Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function RevealCredentialDialog({ item }: { item: CartItemRead }) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [credential, setCredential] = useState<CredentialReveal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  function clear() {
    setCredential(null);
    setError(null);
    setCopied(false);
  }

  async function reveal(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      const result = await apiRequest<CredentialReveal>(
        `/cart-items/${item.id}/credential/reveal`,
        {
          method: "POST",
          body: JSON.stringify({ current_password: String(form.get("current_password") ?? "") }),
        },
      );
      setCredential(result);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not reveal the merchant credential."));
    } finally {
      setSubmitting(false);
    }
  }

  async function copyPassword() {
    if (!credential) return;
    await navigator.clipboard.writeText(credential.password);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2_000);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) clear();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <KeyRound /> Reveal merchant login
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Merchant account credential</DialogTitle>
          <DialogDescription>
            This is separate from your AG Pay account. Re-enter your platform password to reveal
            it.
          </DialogDescription>
        </DialogHeader>
        {credential ? (
          <div className="space-y-4">
            <div className="space-y-3 rounded-xl border bg-muted/40 p-4">
              <CredentialRow label="Email" value={credential.email} />
              <CredentialRow label="Password" value={credential.password} secret />
              {credential.login_url ? (
                <CredentialRow label="Login URL" value={credential.login_url} />
              ) : null}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              This secret is kept only in this dialog and is cleared when you close it.
            </p>
          </div>
        ) : (
          <form
            id={`reveal-${item.id}`}
            method="post"
            onSubmit={reveal}
            className="space-y-3"
          >
            <div className="space-y-1.5">
              <Label htmlFor={`current-password-${item.id}`}>Current AG Pay password</Label>
              <Input
                id={`current-password-${item.id}`}
                name="current_password"
                type="password"
                autoComplete="current-password"
                required
                autoFocus
              />
            </div>
            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
          </form>
        )}
        <DialogFooter>
          {credential ? (
            <>
              <Button variant="outline" onClick={copyPassword}>
                {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy password"}
              </Button>
              <Button onClick={() => setOpen(false)}>Close and clear</Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" form={`reveal-${item.id}`} disabled={submitting}>
                {submitting ? <Loader2 className="animate-spin" /> : <KeyRound />}
                Reveal once
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CredentialRow({ label, value, secret = false }: { label: string; value: string; secret?: boolean }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={secret ? "mt-1 break-all font-mono text-sm font-medium" : "mt-1 break-all text-sm font-medium"}>
        {value}
      </p>
    </div>
  );
}
