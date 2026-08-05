import { cn } from "@/lib/utils"

type MoneyProps = {
  amount: number | string
  currency: string
  locale?: string
  className?: string
  signDisplay?: Intl.NumberFormatOptions["signDisplay"]
}

export function formatMoney(
  amount: number | string,
  currency: string,
  locale = "en-GB",
  signDisplay: Intl.NumberFormatOptions["signDisplay"] = "auto",
) {
  const numericAmount = typeof amount === "number" ? amount : Number(amount)

  if (!Number.isFinite(numericAmount)) return `${amount} ${currency.toUpperCase()}`

  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: currency.toUpperCase(),
      currencyDisplay: "narrowSymbol",
      signDisplay,
    }).format(numericAmount)
  } catch {
    return `${numericAmount.toFixed(2)} ${currency.toUpperCase()}`
  }
}

export function Money({
  amount,
  currency,
  locale = "en-GB",
  signDisplay = "auto",
  className,
}: MoneyProps) {
  const accessibleAmount = `${amount} ${currency.toUpperCase()}`

  return (
    <span
      className={cn("font-medium tabular-nums", className)}
      aria-label={accessibleAmount}
    >
      {formatMoney(amount, currency, locale, signDisplay)}
    </span>
  )
}
