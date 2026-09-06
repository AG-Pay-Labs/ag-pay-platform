import {
  createPublicClient,
  createWalletClient,
  custom,
  getAddress,
  numberToHex,
  type EIP1193Provider,
} from "viem";

import type { EvmAddress } from "@/lib/api-types";
import { chainForId } from "@/lib/wallets/chains";

type ProviderWithWalletFlags = EIP1193Provider & {
  isMetaMask?: boolean;
  isBraveWallet?: boolean;
};

interface Eip6963ProviderInfo {
  uuid: string;
  name: string;
  icon: string;
  rdns: string;
}

interface Eip6963ProviderDetail {
  info: Eip6963ProviderInfo;
  provider: EIP1193Provider;
}

export interface DiscoveredBrowserWallet {
  connectorId: BrowserWalletConnectorId;
  name: string;
  icon: string | null;
  provider: EIP1193Provider;
}

export interface BrowserWalletConnector {
  id: string;
  displayName: string;
  installUrl: string;
  matches(
    info: Eip6963ProviderInfo | null,
    provider: ProviderWithWalletFlags,
  ): boolean;
}

/** Add future browser wallets here without changing enrollment or x402 code. */
export const BROWSER_WALLET_CONNECTORS = [
  {
    id: "metamask",
    displayName: "MetaMask",
    installUrl: "https://metamask.io/download/",
    matches(info, provider) {
      if (info?.rdns === "io.metamask" || info?.rdns.startsWith("io.metamask.")) {
        return true;
      }
      return provider.isMetaMask === true && provider.isBraveWallet !== true;
    },
  },
] as const satisfies readonly BrowserWalletConnector[];

export type BrowserWalletConnectorId =
  (typeof BROWSER_WALLET_CONNECTORS)[number]["id"];

function fallbackInjectedProvider(): ProviderWithWalletFlags | null {
  if (typeof window === "undefined") return null;
  return (
    (window as Window & { ethereum?: ProviderWithWalletFlags }).ethereum ?? null
  );
}

export async function discoverBrowserWallet(
  connectorId: BrowserWalletConnectorId,
): Promise<DiscoveredBrowserWallet | null> {
  if (typeof window === "undefined") return null;
  const connector = BROWSER_WALLET_CONNECTORS.find(
    (candidate) => candidate.id === connectorId,
  );
  if (!connector) return null;

  const announced: Eip6963ProviderDetail[] = [];
  const onAnnouncement = (event: Event) => {
    const detail = (event as CustomEvent<Eip6963ProviderDetail>).detail;
    if (detail?.provider && detail.info) announced.push(detail);
  };

  window.addEventListener("eip6963:announceProvider", onAnnouncement);
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  await new Promise((resolve) => window.setTimeout(resolve, 120));
  window.removeEventListener("eip6963:announceProvider", onAnnouncement);

  const match = announced.find(({ info, provider }) =>
    connector.matches(info, provider as ProviderWithWalletFlags),
  );
  if (match) {
    return {
      connectorId,
      name: match.info.name || connector.displayName,
      icon: match.info.icon || null,
      provider: match.provider,
    };
  }

  const fallback = fallbackInjectedProvider();
  if (!fallback || !connector.matches(null, fallback)) return null;
  return {
    connectorId,
    name: connector.displayName,
    icon: null,
    provider: fallback,
  };
}

export async function connectBrowserWallet(
  wallet: DiscoveredBrowserWallet,
): Promise<{ address: EvmAddress; chainId: number }> {
  const accounts = await wallet.provider.request({
    method: "eth_requestAccounts",
  });
  const address = accounts[0];
  if (!address) throw new Error(`${wallet.name} did not return an account.`);
  const chainHex = await wallet.provider.request({ method: "eth_chainId" });
  const chainId = Number.parseInt(chainHex, 16);
  if (!Number.isSafeInteger(chainId) || chainId <= 0) {
    throw new Error(`${wallet.name} returned an invalid network.`);
  }
  return { address: getAddress(address), chainId };
}

export async function switchBrowserWalletChain(
  provider: EIP1193Provider,
  chainId: number,
): Promise<void> {
  const chain = chainForId(chainId);
  if (!chain) throw new Error(`EVM chain ${chainId} is not supported by this build.`);

  try {
    await provider.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: numberToHex(chainId) }],
    });
  } catch (caught) {
    const code =
      typeof caught === "object" && caught !== null && "code" in caught
        ? Number((caught as { code: unknown }).code)
        : null;
    if (code !== 4902) throw caught;

    await provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: numberToHex(chain.id),
          chainName: chain.name,
          nativeCurrency: chain.nativeCurrency,
          rpcUrls: chain.rpcUrls.default.http,
          blockExplorerUrls: chain.blockExplorers?.default
            ? [chain.blockExplorers.default.url]
            : undefined,
        },
      ],
    });
  }
}

export function walletClientFor(
  provider: EIP1193Provider,
  address: EvmAddress,
  chainId: number,
) {
  const chain = chainForId(chainId);
  if (!chain) throw new Error(`EVM chain ${chainId} is not supported by this build.`);
  return createWalletClient({
    account: address,
    chain,
    transport: custom(provider),
  });
}

export function publicClientFor(provider: EIP1193Provider, chainId: number) {
  const chain = chainForId(chainId);
  if (!chain) throw new Error(`EVM chain ${chainId} is not supported by this build.`);
  return createPublicClient({
    chain,
    transport: custom(provider),
  });
}
