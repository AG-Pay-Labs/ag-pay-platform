"use client";

import {
  ArrowRight,
  Bot,
  CalendarDays,
  Clock3,
  Cpu,
  Fingerprint,
  Radio,
  ShieldCheck,
} from "lucide-react";

import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/app";
import {
  AgentCardAssignmentsDialog,
  RevokeAgentDialog,
  RotatePairingDialog,
} from "@/components/features/agents/agent-actions";
import { ConnectAgentDialog } from "@/components/features/agents/connect-agent-dialog";
import {
  RuntimeMark,
  runtimeIdentity,
  runtimeLabel,
} from "@/components/features/agents/runtime-mark";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useAgents } from "@/hooks/use-api-data";
import type { AgentRead } from "@/lib/api-types";
import { formatDateTime, relativeTime } from "@/utils/format";

export default function AgentsPage() {
  const agents = useAgents();

  return (
    <>
      <PageHeader
        eyebrow="Connected runtimes"
        title="Agents"
        description="Pair OpenClaw-like agents, monitor heartbeats, and control which cards each one may use."
        actions={<ConnectAgentDialog />}
      />

      {agents.isLoading ? <LoadingState variant="cards" rows={4} /> : null}
      {agents.error ? (
        <ErrorState description="Agent state could not be loaded." retry={() => agents.refetch()} />
      ) : null}
      {!agents.isLoading && !agents.error && agents.data?.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="Connect your first agent"
          description="Create a one-time pairing token and give it to the intended runtime. The backend never calls arbitrary agent URLs."
          action={<ConnectAgentDialog />}
        />
      ) : null}

      {agents.data?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {agents.data.map((agent) => (
            <AgentSummary key={agent.id} agent={agent} />
          ))}
        </div>
      ) : null}
    </>
  );
}

function AgentSummary({ agent }: { agent: AgentRead }) {
  const identity = runtimeIdentity(agent);

  return (
    <Sheet>
      <SheetTrigger asChild>
        <button
          type="button"
          className="group relative min-w-0 overflow-hidden rounded-2xl border bg-card text-left shadow-sm outline-none transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-lg hover:shadow-indigo-950/5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 dark:hover:border-indigo-800"
          aria-label={`View details for ${agent.name}`}
        >
          <span className="relative flex min-h-40 items-center justify-center overflow-hidden bg-gradient-to-b from-indigo-50/90 via-violet-50/60 to-background p-6 dark:from-indigo-950/35 dark:via-violet-950/20 dark:to-card">
            <span className="absolute top-4 left-4 text-[10px] font-semibold tracking-[0.16em] text-indigo-700/70 uppercase dark:text-indigo-300/70">
              {runtimeLabel(identity)} runtime
            </span>
            <span className="absolute -top-12 -right-10 size-36 rounded-full bg-indigo-300/20 blur-3xl dark:bg-indigo-500/10" />
            <RuntimeMark identity={identity} className="size-24 transition-transform duration-300 group-hover:scale-105" />
          </span>

          <span className="block border-t p-4">
            <span className="flex min-w-0 items-start justify-between gap-3">
              <span className="min-w-0">
                <span className="block truncate text-base font-semibold tracking-tight text-foreground">
                  {agent.name}
                </span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                  {agent.instance_id || "Awaiting runtime pairing"}
                </span>
              </span>
              <StatusBadge
                status={agent.connection_state}
                label={connectionLabel(agent)}
                className="shrink-0"
              />
            </span>
            <span className="mt-4 flex items-center justify-between text-xs font-medium text-indigo-700 dark:text-indigo-300">
              View details
              <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </span>
        </button>
      </SheetTrigger>

      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-[30rem]">
        <SheetHeader className="border-b px-6 py-6 pr-14">
          <div className="flex items-center gap-4">
            <RuntimeMark identity={identity} className="size-16 rounded-2xl" />
            <div className="min-w-0 flex-1">
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <SheetTitle className="truncate text-xl font-semibold tracking-tight">
                  {agent.name}
                </SheetTitle>
                <StatusBadge status={agent.connection_state} label={connectionLabel(agent)} />
              </div>
              <SheetDescription>{runtimeLabel(identity)}-compatible runtime</SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
          <section aria-labelledby={`agent-${agent.id}-health`}>
            <h2 id={`agent-${agent.id}-health`} className="text-xs font-semibold tracking-[0.14em] text-muted-foreground uppercase">
              Runtime health
            </h2>
            <div className="mt-3 flex items-start gap-3 rounded-xl border bg-muted/35 p-4">
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-background ring-1 ring-border">
                {agent.connection_state === "online" ? (
                  <Radio className="size-4 text-emerald-600" />
                ) : (
                  <ShieldCheck className="size-4 text-muted-foreground" />
                )}
              </span>
              <div>
                <p className="font-medium">{healthTitle(agent)}</p>
                <p className="mt-1 text-sm leading-5 text-muted-foreground">{healthDescription(agent)}</p>
              </div>
            </div>
          </section>

          <Separator className="my-6" />

          <section aria-labelledby={`agent-${agent.id}-about`}>
            <h2 id={`agent-${agent.id}-about`} className="text-xs font-semibold tracking-[0.14em] text-muted-foreground uppercase">
              About
            </h2>
            <p className="mt-3 text-sm leading-6 text-foreground">
              {agent.description || "No description has been added for this agent."}
            </p>
          </section>

          <Separator className="my-6" />

          <section aria-labelledby={`agent-${agent.id}-runtime`}>
            <h2 id={`agent-${agent.id}-runtime`} className="text-xs font-semibold tracking-[0.14em] text-muted-foreground uppercase">
              Runtime details
            </h2>
            <dl className="mt-3 divide-y rounded-xl border bg-card px-4">
              <DetailRow
                icon={Clock3}
                label="Last heartbeat"
                value={agent.last_seen_at ? relativeTime(agent.last_seen_at) : "Never"}
                title={formatDateTime(agent.last_seen_at)}
              />
              <DetailRow
                icon={CalendarDays}
                label="Connected"
                value={agent.connected_at ? formatDateTime(agent.connected_at) : "Not connected"}
              />
              <DetailRow icon={Cpu} label="Software version" value={agent.software_version || "Not reported"} />
              <DetailRow
                icon={Fingerprint}
                label="Instance ID"
                value={agent.instance_id || "Awaiting pairing"}
                mono
              />
            </dl>
          </section>

          <Separator className="my-6" />

          <section aria-labelledby={`agent-${agent.id}-capabilities`}>
            <h2 id={`agent-${agent.id}-capabilities`} className="text-xs font-semibold tracking-[0.14em] text-muted-foreground uppercase">
              Capabilities
            </h2>
            <div className="mt-3 flex flex-wrap gap-2">
              {agent.capabilities.length ? (
                agent.capabilities.map((capability) => (
                  <Badge key={capability} variant="secondary" className="max-w-full break-all">
                    {capability}
                  </Badge>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">No capabilities reported.</p>
              )}
            </div>
          </section>
        </div>

        <SheetFooter className="border-t bg-background px-6 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <AgentCardAssignmentsDialog agent={agent} />
            <div className="ml-auto flex items-center gap-1">
              <RotatePairingDialog agent={agent} />
              <RevokeAgentDialog agent={agent} />
            </div>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function DetailRow({
  icon: Icon,
  label,
  value,
  title,
  mono = false,
}: {
  icon: typeof Clock3;
  label: string;
  value: string;
  title?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 py-3.5">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <dt className="min-w-0 flex-1 text-sm text-muted-foreground">{label}</dt>
      <dd className={mono ? "max-w-[58%] truncate font-mono text-xs font-medium" : "max-w-[58%] text-right text-sm font-medium"} title={title}>
        {value}
      </dd>
    </div>
  );
}

function connectionLabel(agent: AgentRead) {
  if (agent.connection_state === "online") return "Connected";
  if (agent.connection_state === "pending") return "Pairing";
  if (agent.connection_state === "offline") return "Offline";
  return "Revoked";
}

function healthTitle(agent: AgentRead) {
  if (agent.connection_state === "online") return "Connection is healthy";
  if (agent.status === "pending") return "Waiting for handshake";
  if (agent.status === "revoked") return "Agent access revoked";
  return "Heartbeat is stale";
}

function healthDescription(agent: AgentRead) {
  if (agent.connection_state === "online") {
    return "Heartbeat is current; this runtime can authenticate and submit purchase proposals.";
  }
  if (agent.status === "pending") {
    return "Use the one-time pairing token in the intended runtime to complete the connection.";
  }
  if (agent.status === "revoked") {
    return "Authentication and pairing material has been permanently cleared.";
  }
  return "The agent remains enrolled, but its latest heartbeat falls outside the online window.";
}
