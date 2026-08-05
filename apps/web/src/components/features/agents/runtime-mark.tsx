import Image from "next/image";

import type { AgentRead } from "@/lib/api-types";
import { cn } from "@/lib/utils";

export type RuntimeIdentity = "openclaw" | "hermes";

export function runtimeIdentity(agent: AgentRead): RuntimeIdentity {
  const fingerprint = [
    agent.name,
    agent.description,
    agent.instance_id,
    agent.software_version,
    ...agent.capabilities,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  return fingerprint.includes("hermes") ? "hermes" : "openclaw";
}

export function runtimeLabel(identity: RuntimeIdentity) {
  return identity === "hermes" ? "Hermes" : "OpenClaw";
}

export function RuntimeMark({
  identity,
  className,
}: {
  identity: RuntimeIdentity;
  className?: string;
}) {
  const asset =
    identity === "hermes"
      ? { src: "/agents/hermes-agent.png", alt: "Hermes Agent" }
      : { src: "/agents/openclaw.png", alt: "OpenClaw" };

  return (
    <span
      className={cn(
        "relative inline-flex size-20 shrink-0 items-center justify-center overflow-hidden rounded-[1.4rem] border border-black/5 bg-white shadow-[0_18px_45px_-24px_rgba(49,46,129,0.8)] ring-1 ring-indigo-950/5 dark:border-white/10 dark:ring-white/10",
        className,
      )}
      title={`${asset.alt} official logo`}
    >
      <Image
        src={asset.src}
        alt={asset.alt}
        fill
        sizes="96px"
        className={cn("object-contain", identity === "openclaw" && "p-2.5")}
      />
    </span>
  );
}
