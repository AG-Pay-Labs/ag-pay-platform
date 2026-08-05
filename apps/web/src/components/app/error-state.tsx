"use client"

import { AlertCircleIcon, RefreshCwIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type ErrorStateProps = {
  title?: string
  description?: React.ReactNode
  retry?: () => void
  retryLabel?: string
  className?: string
  compact?: boolean
}

export function ErrorState({
  title = "Something went wrong",
  description = "We couldn’t load this content. Try again in a moment.",
  retry,
  retryLabel = "Try again",
  className,
  compact = false,
}: ErrorStateProps) {
  return (
    <section
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-rose-200 bg-rose-50/60 px-6 text-center dark:border-rose-900 dark:bg-rose-950/20",
        compact ? "min-h-40 py-7" : "min-h-64 py-10",
        className,
      )}
    >
      <span className="mb-3 flex size-10 items-center justify-center rounded-xl bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300">
        <AlertCircleIcon className="size-5" aria-hidden="true" />
      </span>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      {description ? (
        <div className="mt-1.5 max-w-md text-sm leading-6 text-pretty text-muted-foreground">
          {description}
        </div>
      ) : null}
      {retry ? (
        <Button variant="outline" className="mt-5" onClick={retry}>
          <RefreshCwIcon data-icon="inline-start" aria-hidden="true" />
          {retryLabel}
        </Button>
      ) : null}
    </section>
  )
}

