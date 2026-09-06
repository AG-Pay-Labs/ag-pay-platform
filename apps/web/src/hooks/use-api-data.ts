"use client";

import { useQuery } from "@tanstack/react-query";

import { apiRequest } from "@/lib/api-client";
import type {
  AgentRead,
  CartItemRead,
  PaymentRuleSetRead,
  PaymentMethodRead,
  PurchaseRead,
  SubscriptionRead,
  WalletConfigRead,
} from "@/lib/api-types";

export const queryKeys = {
  agents: ["agents"] as const,
  agentPaymentMethods: (agentId: string) =>
    ["agents", agentId, "payment-methods"] as const,
  paymentMethods: ["payment-methods"] as const,
  walletConfig: ["payment-methods", "wallet-config"] as const,
  cart: ["cart-items"] as const,
  purchases: ["purchases"] as const,
  subscriptions: ["subscriptions"] as const,
  paymentRuleSets: ["payment-rule-sets"] as const,
};

export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents,
    queryFn: () => apiRequest<AgentRead[]>("/agents"),
    refetchInterval: 30_000,
  });
}

export function usePaymentMethods() {
  return useQuery({
    queryKey: queryKeys.paymentMethods,
    queryFn: () => apiRequest<PaymentMethodRead[]>("/payment-methods"),
  });
}

export function useWalletConfig(enabled = true) {
  return useQuery({
    queryKey: queryKeys.walletConfig,
    queryFn: () =>
      apiRequest<WalletConfigRead>("/payment-methods/wallet-config"),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useAgentPaymentMethods(agentId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentPaymentMethods(agentId ?? "none"),
    queryFn: () =>
      apiRequest<PaymentMethodRead[]>(`/agents/${agentId}/payment-methods`),
    enabled: Boolean(agentId) && enabled,
  });
}

export function useCartItems() {
  return useQuery({
    queryKey: queryKeys.cart,
    queryFn: () => apiRequest<CartItemRead[]>("/cart-items"),
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) =>
          item.execution?.status === "queued" ||
          item.execution?.status === "running" ||
          item.execution?.status === "authorized" ||
          item.execution?.status === "submitted"
      )
        ? 2_000
        : 20_000,
  });
}

export function usePurchases() {
  return useQuery({
    queryKey: queryKeys.purchases,
    queryFn: () => apiRequest<PurchaseRead[]>("/purchases"),
    refetchInterval: 20_000,
  });
}

export function useSubscriptions() {
  return useQuery({
    queryKey: queryKeys.subscriptions,
    queryFn: () => apiRequest<SubscriptionRead[]>("/subscriptions"),
    refetchInterval: 20_000,
  });
}

export function usePaymentRuleSets() {
  return useQuery({
    queryKey: queryKeys.paymentRuleSets,
    queryFn: () => apiRequest<PaymentRuleSetRead[]>("/payment-rule-sets"),
  });
}
