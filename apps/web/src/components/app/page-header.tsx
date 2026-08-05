import Link from "next/link"
import { ArrowLeftIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type PageHeaderProps = {
  title: React.ReactNode
  description?: React.ReactNode
  eyebrow?: React.ReactNode
  actions?: React.ReactNode
  back?: {
    href: string
    label: string
  }
  className?: string
}

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  back,
  className,
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        "mb-6 flex flex-col gap-4 sm:mb-8 sm:flex-row sm:items-start sm:justify-between",
        className,
      )}
    >
      <div className="min-w-0 max-w-3xl">
        {back ? (
          <Button variant="ghost" size="sm" asChild className="-ml-2 mb-2">
            <Link href={back.href}>
              <ArrowLeftIcon data-icon="inline-start" aria-hidden="true" />
              {back.label}
            </Link>
          </Button>
        ) : null}
        {eyebrow ? (
          <div className="mb-1 text-xs font-semibold tracking-wide text-indigo-600 uppercase dark:text-indigo-400">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="text-2xl leading-tight font-semibold tracking-tight text-balance text-foreground sm:text-3xl">
          {title}
        </h1>
        {description ? (
          <div className="mt-2 max-w-2xl text-sm leading-6 text-pretty text-muted-foreground sm:text-base">
            {description}
          </div>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 flex-col gap-2 *:w-full sm:flex-row sm:items-center sm:*:w-auto">
          {actions}
        </div>
      ) : null}
    </header>
  )
}

