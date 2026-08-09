"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Loader2, ShieldCheck, X } from "lucide-react";
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
  CartItemRead,
  CredentialReveal,
} from "@/lib/api-types";
import { queryKeys, useAgentPaymentMethods } from "@/hooks/use-api-data";

export function ApproveDialog({ item, agent }: { item: CartItemRead; agent?: AgentRead }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selectedCard, setSelectedCard] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assigned = useAgentPaymentMethods(item.agent_id, open);
  const activeCards = useMemo(
    () => (assigned.data ?? []).filter((card) => card.status === "active"),
    [assigned.data],
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCard) {
      setError("Select an assigned payment method.");
      return;
    }
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);

    try {
      await apiRequest<CartItemRead>(`/cart-items/${item.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          payment_method_id: selectedCard,
          note: String(form.get("note") ?? "").trim() || null,
        }),
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.cart });
      toast.success(
        item.checkout_adapter
          ? "Purchase approved; secure checkout queued"
          : "Purchase approved for legacy external completion",
      );
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not approve this proposal."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
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
            {item.checkout_adapter ? (
              <>
                This authorizes AG Pay to run the configured checkout after approval. Payment
                credentials stay inside the trusted executor and are never sent to the agent.
              </>
            ) : (
              <>
                This authorizes {agent?.name ?? "the agent"} to complete the item through the
                legacy external flow and report its result.
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
            ) : activeCards.length === 0 ? (
              <div className="rounded-lg border border-dashed p-4 text-sm">
                <p className="font-medium">No active card is assigned</p>
                <p className="mt-1 text-muted-foreground">
                  Assign a card to {agent?.name ?? "this agent"} before approving.
                </p>
                <Button variant="outline" size="sm" className="mt-3" asChild>
                  <Link href="/agents">Manage agent cards</Link>
                </Button>
              </div>
            ) : (
              <RadioGroup value={selectedCard} onValueChange={setSelectedCard} className="space-y-2">
                {activeCards.map((card) => (
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
          <Button variant="outline" onClick={() => setOpen(false)}>
            Back
          </Button>
          <Button
            type="submit"
            form={`approve-${item.id}`}
            disabled={submitting || activeCards.length === 0}
          >
            {submitting ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            Approve · <Money amount={item.total_amount} currency={item.currency} />
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
          <form id={`reveal-${item.id}`} onSubmit={reveal} className="space-y-3">
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
