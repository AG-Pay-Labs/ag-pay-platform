"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Loader2, Settings2 } from "lucide-react";
import { toast } from "sonner";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { queryKeys } from "@/hooks/use-api-data";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type { SubscriptionRead, SubscriptionStatus } from "@/lib/api-types";

export function ManageSubscriptionDialog({ subscription }: { subscription: SubscriptionRead }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<SubscriptionStatus>(subscription.status);
  const [nextBilling, setNextBilling] = useState(toLocalInput(subscription.next_billing_at));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiRequest<SubscriptionRead>(`/subscriptions/${subscription.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status,
          next_billing_at: nextBilling ? new Date(nextBilling).toISOString() : null,
        }),
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.subscriptions }),
        queryClient.invalidateQueries({ queryKey: queryKeys.purchases }),
      ]);
      toast.success("Subscription record updated");
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not update the subscription record."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setStatus(subscription.status);
          setNextBilling(toLocalInput(subscription.next_billing_at));
          setError(null);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Settings2 /> Manage
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Update subscription record</DialogTitle>
          <DialogDescription>
            Change how AG Pay tracks {subscription.title}. This does not alter billing with the
            merchant.
          </DialogDescription>
        </DialogHeader>

        <form id={`subscription-${subscription.id}`} onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor={`subscription-status-${subscription.id}`}>Tracking status</Label>
            <Select value={status} onValueChange={(value) => setStatus(value as SubscriptionStatus)}>
              <SelectTrigger id={`subscription-status-${subscription.id}`} className="h-9 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="paused">Paused</SelectItem>
                <SelectItem value="cancelled">Cancelled</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`next-billing-${subscription.id}`}>Next expected billing</Label>
            <Input
              id={`next-billing-${subscription.id}`}
              type="datetime-local"
              value={nextBilling}
              onChange={(event) => setNextBilling(event.target.value)}
            />
          </div>
          <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            <CalendarClock className="mt-0.5 size-4 shrink-0" />
            <p>
              To pause or cancel the actual subscription, sign in to the merchant. This setting is
              an internal record only.
            </p>
          </div>
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
          <Button type="submit" form={`subscription-${subscription.id}`} disabled={submitting}>
            {submitting ? <Loader2 className="animate-spin" /> : <Settings2 />}
            Save record
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function toLocalInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
