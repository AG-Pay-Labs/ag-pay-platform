import { CreditCardIcon } from "lucide-react"

import { cn } from "@/lib/utils"

import { StatusBadge } from "./status-badge"

const BRAND_LABELS: Record<string, string> = {
  amex: "American Express",
  american_express: "American Express",
  mastercard: "Mastercard",
  visa: "Visa",
}

type SafeCardLabelProps = {
  brand: string
  last4: string
  displayName?: string
  expiryMonth?: number
  expiryYear?: number
  status?: string
  compact?: boolean
  className?: string
}

function formatBrand(brand: string) {
  const normalized = brand.trim().toLowerCase()
  return (
    BRAND_LABELS[normalized] ??
    normalized.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
  )
}

function formatExpiry(month: number, year: number) {
  return `${String(month).padStart(2, "0")}/${String(year).slice(-2)}`
}

export function SafeCardLabel({
  brand,
  last4,
  displayName,
  expiryMonth,
  expiryYear,
  status,
  compact = false,
  className,
}: SafeCardLabelProps) {
  const safeLast4 = last4.replace(/\D/g, "").slice(-4).padStart(4, "•")
  const brandLabel = formatBrand(brand)
  const hasExpiry = expiryMonth !== undefined && expiryYear !== undefined
  const accessibleLabel = [
    displayName,
    `${brandLabel} ending in ${safeLast4}`,
    hasExpiry ? `expires ${formatExpiry(expiryMonth, expiryYear)}` : undefined,
    status,
  ]
    .filter(Boolean)
    .join(", ")

  return (
    <div
      className={cn("flex min-w-0 items-center gap-3", className)}
      role="group"
      aria-label={accessibleLabel}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300",
          compact ? "size-8" : "size-10",
        )}
        aria-hidden="true"
      >
        <CreditCardIcon className={compact ? "size-4" : "size-[18px]"} />
      </span>
      <span className="min-w-0 flex-1">
        {displayName ? (
          <span className="block truncate text-sm font-medium text-foreground">
            {displayName}
          </span>
        ) : null}
        <span
          className={cn(
            "flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground",
            displayName || !compact ? "text-xs" : "text-sm",
          )}
        >
          <span aria-hidden="true">
            {brandLabel} <span className="font-mono tracking-wide">•••• {safeLast4}</span>
          </span>
          {hasExpiry ? (
            <span aria-hidden="true">Exp. {formatExpiry(expiryMonth, expiryYear)}</span>
          ) : null}
        </span>
      </span>
      {status ? <StatusBadge status={status} className="hidden sm:inline-flex" /> : null}
    </div>
  )
}
