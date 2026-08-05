"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2, Plus, Radio, ShieldCheck } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type { AgentCreate, AgentCreated, AgentRead } from "@/lib/api-types";
import { queryKeys } from "@/hooks/use-api-data";
import { formatDateTime } from "@/utils/format";

export function ConnectAgentDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<AgentCreated | null>(null);
  const [connected, setConnected] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !created || connected) return;

    const timer = window.setInterval(async () => {
      try {
        const current = await apiRequest<AgentRead>(`/agents/${created.id}`);
        if (current.connection_state === "online") {
          setConnected(true);
          await queryClient.invalidateQueries({ queryKey: queryKeys.agents });
          toast.success(`${current.name} is connected`);
        }
      } catch {
        // The normal query surface will expose persistent errors; polling stays quiet.
      }
    }, 3_000);

    return () => window.clearInterval(timer);
  }, [connected, created, open, queryClient]);

  function reset() {
    setCreated(null);
    setConnected(false);
    setCopied(false);
    setError(null);
  }

  async function handleCreate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);
    const payload: AgentCreate = {
      name: String(form.get("name") ?? "").trim(),
      description: String(form.get("description") ?? "").trim() || null,
    };

    try {
      const agent = await apiRequest<AgentCreated>("/agents", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setCreated(agent);
      await queryClient.invalidateQueries({ queryKey: queryKeys.agents });
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not create the agent."));
    } finally {
      setSubmitting(false);
    }
  }

  async function copyToken() {
    if (!created) return;
    await navigator.clipboard.writeText(created.pairing_token);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2_000);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="lg" className="h-10 px-4">
          <Plus />
          Connect agent
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        {!created ? (
          <>
            <DialogHeader>
              <DialogTitle>Connect an agent</DialogTitle>
              <DialogDescription>
                Create a record, then give the one-time pairing token to your OpenClaw-like
                runtime.
              </DialogDescription>
            </DialogHeader>
            <form id="create-agent" onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="agent-name">Agent name</Label>
                <Input
                  id="agent-name"
                  name="name"
                  placeholder="Research shopper"
                  minLength={1}
                  maxLength={120}
                  required
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="agent-description">Description</Label>
                <Textarea
                  id="agent-description"
                  name="description"
                  placeholder="What this agent does and where it runs"
                  maxLength={2_000}
                  rows={4}
                />
              </div>
              {error ? (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              ) : null}
            </form>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" form="create-agent" disabled={submitting}>
                {submitting ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
                Create pairing token
              </Button>
            </DialogFooter>
          </>
        ) : connected ? (
          <ConnectedStep name={created.name} onDone={() => setOpen(false)} />
        ) : (
          <>
            <DialogHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                <Radio className="size-5" />
              </div>
              <DialogTitle>Pair {created.name}</DialogTitle>
              <DialogDescription>
                Paste this token into the intended agent runtime. It is shown only here.
              </DialogDescription>
            </DialogHeader>

            <div className="rounded-xl border bg-muted/50 p-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  One-time pairing token
                </span>
                <Button type="button" variant="outline" size="sm" onClick={copyToken}>
                  {copied ? <Check /> : <Copy />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <code className="block break-all font-mono text-sm font-medium">
                {created.pairing_token}
              </code>
            </div>

            <div className="flex items-center gap-3 rounded-lg border p-3">
              <Loader2 className="size-4 animate-spin text-amber-600" />
              <div>
                <p className="text-sm font-medium">Waiting for agent handshake</p>
                <p className="text-xs text-muted-foreground">
                  Token expires {formatDateTime(created.pairing_expires_at)}
                </p>
              </div>
            </div>

            <p className="text-xs leading-relaxed text-muted-foreground">
              Closing this window discards the plaintext token. You can generate a new token
              later, but doing so disconnects an already paired installation.
            </p>

            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                I’ll finish later
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ConnectedStep({ name, onDone }: { name: string; onDone: () => void }) {
  return (
    <>
      <DialogHeader>
        <div className="mb-2 flex size-11 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
          <Check className="size-5" />
        </div>
        <DialogTitle>{name} is connected</DialogTitle>
        <DialogDescription>
          The handshake completed successfully. Heartbeats will keep its presence current.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button onClick={onDone}>Done</Button>
      </DialogFooter>
    </>
  );
}

