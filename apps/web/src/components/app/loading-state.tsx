import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

type LoadingStateProps = {
  label?: string
  rows?: number
  variant?: "list" | "cards" | "detail" | "inline"
  className?: string
}

function SkeletonRow() {
  return (
    <div className="flex items-center gap-3 border-b border-border px-4 py-4 last:border-b-0">
      <Skeleton className="size-9 shrink-0 rounded-lg" />
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-3.5 w-1/3" />
        <Skeleton className="h-3 w-1/2" />
      </div>
      <Skeleton className="hidden h-6 w-20 sm:block" />
    </div>
  )
}

export function LoadingState({
  label = "Loading content",
  rows = 4,
  variant = "list",
  className,
}: LoadingStateProps) {
  const safeRows = Math.max(1, Math.min(12, Math.floor(rows)))

  if (variant === "inline") {
    return (
      <div
        role="status"
        aria-live="polite"
        className={cn("flex items-center gap-3", className)}
      >
        <Skeleton className="size-8 rounded-lg" />
        <div className="flex-1 space-y-1.5">
          <Skeleton className="h-3.5 w-32" />
          <Skeleton className="h-3 w-48 max-w-full" />
        </div>
        <span className="sr-only">{label}</span>
      </div>
    )
  }

  if (variant === "cards") {
    return (
      <div
        role="status"
        aria-live="polite"
        className={cn("grid gap-4 sm:grid-cols-2 xl:grid-cols-4", className)}
      >
        {Array.from({ length: safeRows }, (_, index) => (
          <Card key={index} aria-hidden="true">
            <CardHeader>
              <Skeleton className="h-3.5 w-24" />
            </CardHeader>
            <CardContent className="space-y-3">
              <Skeleton className="h-8 w-20" />
              <Skeleton className="h-3 w-32" />
            </CardContent>
          </Card>
        ))}
        <span className="sr-only">{label}</span>
      </div>
    )
  }

  if (variant === "detail") {
    return (
      <div role="status" aria-live="polite" className={cn("space-y-5", className)}>
        <div className="space-y-3">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
        <Card>
          <CardContent className="space-y-5">
            {Array.from({ length: safeRows }, (_, index) => (
              <div key={index} className="space-y-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-4 w-full" />
              </div>
            ))}
          </CardContent>
        </Card>
        <span className="sr-only">{label}</span>
      </div>
    )
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("overflow-hidden rounded-xl border border-border bg-card", className)}
    >
      {Array.from({ length: safeRows }, (_, index) => (
        <SkeletonRow key={index} />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  )
}

