"use client";

import { useQuery } from "@tanstack/react-query";

import { apiRequest } from "@/lib/api-client";
import type {
  AgentRead,
  CartItemRead,
  PaymentPolicyRead,
  PaymentMethodRead,
  PurchaseRead,
  SubscriptionRead,
} from "@/lib/api-types";

export const queryKeys = {
  agents: ["agents"] as const,
  agentCards: (agentId: string) => ["agents", agentId, "payment-methods"] as const,
  cards: ["payment-methods"] as const,
  cart: ["cart-items"] as const,
  purchases: ["purchases"] as const,
  subscriptions: ["subscriptions"] as const,
  paymentPolicies: ["payment-policies"] as const,
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
    queryKey: queryKeys.cards,
    queryFn: () => apiRequest<PaymentMethodRead[]>("/payment-methods"),
  });
}

export function useAgentPaymentMethods(agentId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentCards(agentId ?? "none"),
    queryFn: () =>
      apiRequest<PaymentMethodRead[]>(`/agents/${agentId}/payment-methods`),
    enabled: Boolean(agentId) && enabled,
  });
}

export function useCartItems() {
  return useQuery({
    queryKey: queryKeys.cart,
    queryFn: () => apiRequest<CartItemRead[]>("/cart-items"),
    refetchInterval: 20_000,
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

export function usePaymentPolicies() {
  return useQuery({
    queryKey: queryKeys.paymentPolicies,
    queryFn: () => apiRequest<PaymentPolicyRead[]>("/payment-policies"),
  });
}
