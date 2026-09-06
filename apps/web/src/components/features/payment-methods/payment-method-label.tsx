import { WalletCards } from "lucide-react";

import { SafeCardLabel, StatusBadge } from "@/components/app";
import { Badge } from "@/components/ui/badge";
import type { PaymentMethodRead } from "@/lib/api-types";
import { isCardPaymentMethod, shortAddress } from "@/lib/payment-methods";
import { cn } from "@/lib/utils";
import { chainLabel } from "@/lib/wallets/chains";

export function PaymentMethodLabel({
  method,
  compact = false,
  className,
}: {
  method: PaymentMethodRead;
  compact?: boolean;
  className?: string;
}) {
  if (isCardPaymentMethod(method)) {
    return (
      <SafeCardLabel
        compact={compact}
        displayName={method.display_name}
        brand={method.card_brand}
        last4={method.card_last4}
        expiryMonth={method.expiry_month}
        expiryYear={method.expiry_year}
        status={compact ? undefined : method.status}
        className={className}
      />
    );
  }

  const address = shortAddress(method.address);
  return (
    <div
      className={cn("flex min-w-0 items-center gap-3", className)}
      role="group"
      aria-label={`${method.display_name}, ${method.provider} wallet ${method.address}, ${chainLabel(method.chain_id)}${method.is_testnet ? ", testnet" : ""}, ${method.status}`}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center rounded-lg bg-orange-50 text-orange-700 dark:bg-orange-950/50 dark:text-orange-300",
          compact ? "size-8" : "size-10",
        )}
        aria-hidden="true"
      >
        <WalletCards className={compact ? "size-4" : "size-[18px]"} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">
          {method.display_name}
        </span>
        <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span className="font-mono" title={method.address}>
            {address}
          </span>
          <span aria-hidden="true">·</span>
          <span>{chainLabel(method.chain_id)}</span>
          {method.is_testnet ? (
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              Testnet
            </Badge>
          ) : null}
        </span>
      </span>
      {!compact ? (
        <StatusBadge status={method.status} className="hidden sm:inline-flex" />
      ) : null}
    </div>
  );
}
