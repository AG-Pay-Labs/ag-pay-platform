import type { LucideIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

type StatCardTone = "indigo" | "emerald" | "amber" | "zinc"

const TONE_CLASSES: Record<StatCardTone, string> = {
  indigo:
    "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300",
  emerald:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300",
  amber: "bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300",
  zinc: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
}

type StatCardProps = {
  label: string
  value: React.ReactNode
  description?: React.ReactNode
  icon?: LucideIcon
  tone?: StatCardTone
  action?: React.ReactNode
  className?: string
}

export function StatCard({
  label,
  value,
  description,
  icon: Icon,
  tone = "indigo",
  action,
  className,
}: StatCardProps) {
  return (
    <Card className={cn("min-w-0", className)}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
        {Icon ? (
          <span
            className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg", TONE_CLASSES[tone])}
          >
            <Icon className="size-4" aria-hidden="true" />
          </span>
        ) : null}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tracking-tight tabular-nums text-foreground sm:text-3xl">
          {value}
        </div>
        {description || action ? (
          <div className="mt-2 flex min-h-5 items-end justify-between gap-3 text-xs text-muted-foreground">
            <div className="min-w-0 leading-5">{description}</div>
            {action ? <div className="shrink-0">{action}</div> : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

