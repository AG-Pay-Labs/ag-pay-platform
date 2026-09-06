import { base, baseSepolia, type Chain } from "viem/chains";

import type { EvmNetwork } from "@/lib/api-types";

/**
 * Browser-supported chains are deliberately explicit. Adding another backend
 * network requires adding its viem definition here before the UI can ask a
 * wallet to switch to it.
 */
export const BROWSER_WALLET_CHAINS = [base, baseSepolia] as const;

export function chainForId(chainId: number): Chain | null {
  return BROWSER_WALLET_CHAINS.find((chain) => chain.id === chainId) ?? null;
}

export function networkForChainId(chainId: number): EvmNetwork {
  return `eip155:${chainId}`;
}

export function chainIdForNetwork(network: string): number | null {
  const match = /^eip155:(\d+)$/.exec(network);
  if (!match) return null;
  const chainId = Number(match[1]);
  return Number.isSafeInteger(chainId) && chainId > 0 ? chainId : null;
}

export function chainLabel(chainId: number): string {
  return chainForId(chainId)?.name ?? `EVM chain ${chainId}`;
}
