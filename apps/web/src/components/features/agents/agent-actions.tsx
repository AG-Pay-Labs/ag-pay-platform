"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Loader2, ShieldOff, WalletCards } from "lucide-react";
import { toast } from "sonner";

import { PaymentMethodLabel } from "@/components/features/payment-methods/payment-method-label";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type { AgentRead, Message, PairingTokenResponse } from "@/lib/api-types";
import {
  queryKeys,
  useAgentPaymentMethods,
  usePaymentMethods,
} from "@/hooks/use-api-data";
import { formatDateTime } from "@/utils/format";

export function AgentPaymentMethodAssignmentsDialog({ agent }: { agent: AgentRead }) {
  const [open, setOpen] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const methodsQuery = usePaymentMethods();
  const assignedQuery = useAgentPaymentMethods(agent.id, open);
  const queryClient = useQueryClient();
  const assignedIds = useMemo(
    () => new Set((assignedQuery.data ?? []).map((method) => method.id)),
    [assignedQuery.data],
  );
  const activeMethods = (methodsQuery.data ?? []).filter(
    (method) => method.status === "active",
  );

  async function toggle(methodId: string, assign: boolean) {
    setSavingId(methodId);
    try {
      await apiRequest<void>(`/agents/${agent.id}/payment-methods/${methodId}`, {
        method: assign ? "PUT" : "DELETE",
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.agentPaymentMethods(agent.id),
      });
      toast.success(assign ? "Payment method assigned" : "Payment method unassigned");
    } catch (caught) {
      toast.error(getErrorMessage(caught, "Could not update this assignment."));
    } finally {
      setSavingId(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <WalletCards />
          Manage methods
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Payment methods assigned to {agent.name}</DialogTitle>
          <DialogDescription>
            An assigned method may be selected when you approve this agent’s proposals.
          </DialogDescription>
        </DialogHeader>

        {methodsQuery.isLoading || assignedQuery.isLoading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" /> Loading payment methods
          </div>
        ) : activeMethods.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center">
            <WalletCards className="mx-auto mb-3 size-6 text-muted-foreground" />
            <p className="font-medium">No active payment methods</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Add a card or connect a wallet on the Payment methods page first.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {activeMethods.map((method) => {
              const checked = assignedIds.has(method.id);
              const saving = savingId === method.id;
              return (
                <Label
                  key={method.id}
                  htmlFor={`assign-${agent.id}-${method.id}`}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border bg-card p-3 hover:bg-muted/40"
                >
                  {saving ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Checkbox
                      id={`assign-${agent.id}-${method.id}`}
                      checked={checked}
                      onCheckedChange={(next) => toggle(method.id, next === true)}
                    />
                  )}
                  <PaymentMethodLabel method={method} compact className="flex-1" />
                  {checked ? <Badge variant="secondary">Assigned</Badge> : null}
                </Label>
              );
            })}
          </div>
        )}

        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

export function RotatePairingDialog({ agent }: { agent: AgentRead }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<PairingTokenResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [copied, setCopied] = useState(false);

  async function rotate() {
    setSubmitting(true);
    try {
      const token = await apiRequest<PairingTokenResponse>(
        `/agents/${agent.id}/pairing-token`,
        { method: "POST" },
      );
      setResult(token);
      await queryClient.invalidateQueries({ queryKey: queryKeys.agents });
      toast.success("New pairing token generated");
    } catch (caught) {
      toast.error(getErrorMessage(caught, "Could not rotate the pairing token."));
    } finally {
      setSubmitting(false);
    }
  }

  async function copy() {
    if (!result) return;
    await navigator.clipboard.writeText(result.pairing_token);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2_000);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setResult(null);
          setCopied(false);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" disabled={agent.status === "revoked"}>
          <KeyRound /> Re-pair
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{result ? "New pairing token" : `Re-pair ${agent.name}?`}</DialogTitle>
          <DialogDescription>
            {result
              ? "Transfer this one-time secret to the intended runtime. It is shown only here."
              : "Generating a new pairing token immediately invalidates the current agent credential and marks the agent pending."}
          </DialogDescription>
        </DialogHeader>
        {result ? (
          <div className="space-y-3">
            <div className="rounded-lg border bg-muted/50 p-4">
              <code className="block break-all text-sm font-medium">{result.pairing_token}</code>
            </div>
            <p className="text-xs text-muted-foreground">
              Expires {formatDateTime(result.pairing_expires_at)}
            </p>
          </div>
        ) : null}
        <DialogFooter>
          {result ? (
            <>
              <Button variant="outline" onClick={copy}>
                {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy token"}
              </Button>
              <Button onClick={() => setOpen(false)}>Done</Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button onClick={rotate} disabled={submitting}>
                {submitting ? <Loader2 className="animate-spin" /> : <KeyRound />}
                Generate and disconnect
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function RevokeAgentDialog({ agent }: { agent: AgentRead }) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function revoke() {
    setSubmitting(true);
    try {
      await apiRequest<Message>(`/agents/${agent.id}`, { method: "DELETE" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.agents });
      toast.success(`${agent.name} revoked`);
    } catch (caught) {
      toast.error(getErrorMessage(caught, "Could not revoke the agent."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive" disabled={agent.status === "revoked"}>
          <ShieldOff /> Revoke
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Revoke {agent.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently clears its authentication and pairing material. Purchase history
            remains, but the current API cannot restore a revoked agent.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep agent</AlertDialogCancel>
          <AlertDialogAction
            onClick={revoke}
            disabled={submitting}
            className="bg-destructive text-white hover:bg-destructive/90"
          >
            {submitting ? <Loader2 className="animate-spin" /> : <ShieldOff />}
            Revoke agent
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
