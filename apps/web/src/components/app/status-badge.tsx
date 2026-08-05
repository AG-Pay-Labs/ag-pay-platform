import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export type StatusTone = "positive" | "warning" | "info" | "neutral" | "negative"

const STATUS_TONES: Record<string, StatusTone> = {
  active: "positive",
  completed: "positive",
  online: "positive",
  purchased: "positive",
  pending: "warning",
  proposed: "warning",
  approved: "info",
  offline: "neutral",
  paused: "neutral",
  cancelled: "negative",
  disabled: "negative",
  failed: "negative",
  refunded: "negative",
  revoked: "negative",
}

const TONE_CLASSES: Record<StatusTone, string> = {
  positive:
    "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-300",
  warning:
    "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/60 dark:text-amber-300",
  info: "border-indigo-200 bg-indigo-50 text-indigo-800 dark:border-indigo-900 dark:bg-indigo-950/60 dark:text-indigo-300",
  neutral:
    "border-zinc-200 bg-zinc-100 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  negative:
    "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/60 dark:text-rose-300",
}

const DOT_CLASSES: Record<StatusTone, string> = {
  positive: "bg-emerald-600 dark:bg-emerald-400",
  warning: "bg-amber-600 dark:bg-amber-400",
  info: "bg-indigo-600 dark:bg-indigo-400",
  neutral: "bg-zinc-500 dark:bg-zinc-400",
  negative: "bg-rose-600 dark:bg-rose-400",
}

function humanizeStatus(status: string) {
  return status
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

type StatusBadgeProps = {
  status: string
  label?: string
  tone?: StatusTone
  showDot?: boolean
  className?: string
}

export function StatusBadge({
  status,
  label,
  tone,
  showDot = true,
  className,
}: StatusBadgeProps) {
  const normalizedStatus = status.trim().toLowerCase()
  const resolvedTone = tone ?? STATUS_TONES[normalizedStatus] ?? "neutral"

  return (
    <Badge
      variant="outline"
      className={cn("gap-1.5 font-medium", TONE_CLASSES[resolvedTone], className)}
    >
      {showDot ? (
        <span
          className={cn("size-1.5 rounded-full", DOT_CLASSES[resolvedTone])}
          aria-hidden="true"
        />
      ) : null}
      {label ?? humanizeStatus(normalizedStatus)}
    </Badge>
  )
}

