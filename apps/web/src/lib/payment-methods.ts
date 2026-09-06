import type {
  CardPaymentMethodRead,
  PaymentMethodRead,
  WalletPaymentMethodRead,
} from "@/lib/api-types";

export function isCardPaymentMethod(
  method: PaymentMethodRead,
): method is CardPaymentMethodRead {
  return method.kind === "card";
}

export function isWalletPaymentMethod(
  method: PaymentMethodRead,
): method is WalletPaymentMethodRead {
  return method.kind === "wallet";
}

export function isCardUnexpired(
  method: CardPaymentMethodRead,
  now = new Date(),
): boolean {
  const expiryUtcMonth = method.expiry_year * 12 + method.expiry_month - 1;
  const currentUtcMonth = now.getUTCFullYear() * 12 + now.getUTCMonth();
  return expiryUtcMonth >= currentUtcMonth;
}

export function shortAddress(address: string): string {
  if (address.length <= 14) return address;
  return `${address.slice(0, 8)}…${address.slice(-6)}`;
}
