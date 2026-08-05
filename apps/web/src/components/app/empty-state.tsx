import type { LucideIcon } from "lucide-react"
import { InboxIcon } from "lucide-react"

import { cn } from "@/lib/utils"

type EmptyStateProps = {
  title: string
  description?: React.ReactNode
  icon?: LucideIcon
  action?: React.ReactNode
  secondaryAction?: React.ReactNode
  className?: string
  compact?: boolean
}

export function EmptyState({
  title,
  description,
  icon: Icon = InboxIcon,
  action,
  secondaryAction,
  className,
  compact = false,
}: EmptyStateProps) {
  return (
    <section
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card px-6 text-center",
        compact ? "min-h-48 py-8" : "min-h-72 py-12",
        className,
      )}
    >
      <span className="mb-4 flex size-11 items-center justify-center rounded-xl bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300">
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      {description ? (
        <div className="mt-1.5 max-w-md text-sm leading-6 text-pretty text-muted-foreground">
          {description}
        </div>
      ) : null}
      {action || secondaryAction ? (
        <div className="mt-5 flex w-full flex-col justify-center gap-2 sm:w-auto sm:flex-row sm:items-center">
          {action}
          {secondaryAction}
        </div>
      ) : null}
    </section>
  )
}

