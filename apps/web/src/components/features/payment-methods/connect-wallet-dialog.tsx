"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, WalletCards } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { queryKeys, useWalletConfig } from "@/hooks/use-api-data";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type {
  WalletChallengeCreate,
  WalletChallengeRead,
  WalletPaymentMethodCreate,
  WalletPaymentMethodRead,
} from "@/lib/api-types";
import { shortAddress } from "@/lib/payment-methods";
import {
  BROWSER_WALLET_CONNECTORS,
  connectBrowserWallet,
  discoverBrowserWallet,
  switchBrowserWalletChain,
  walletClientFor,
  type BrowserWalletConnectorId,
} from "@/lib/wallets/connectors";

export function ConnectWalletDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [connectorId, setConnectorId] = useState<
    BrowserWalletConnectorId | ""
  >("");
  const [network, setNetwork] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const config = useWalletConfig(open);
  const enabledNetworks = (config.data?.networks ?? []).filter(
    (candidate) => candidate.x402_enabled,
  );
  const effectiveNetwork = network || enabledNetworks[0]?.network || "";

  const enabledConnectors = useMemo(() => {
    const providerIds = new Set(
      (config.data?.providers ?? []).map((provider) => provider.id),
    );
    return BROWSER_WALLET_CONNECTORS.filter((connector) =>
      providerIds.has(connector.id),
    );
  }, [config.data?.providers]);
  const effectiveConnectorId = connectorId || enabledConnectors[0]?.id || "";
  const selectedConnector = enabledConnectors.find(
    (connector) => connector.id === effectiveConnectorId,
  );

  function reset() {
    setConnectorId("");
    setNetwork("");
    setError(null);
    setSubmitting(false);
  }

  async function connect(
    connectorId: BrowserWalletConnectorId,
    form: HTMLFormElement,
  ) {
    const connector = BROWSER_WALLET_CONNECTORS.find(
      (candidate) => candidate.id === connectorId,
    );
    if (!connector) {
      setError("Select a supported wallet provider.");
      return;
    }
    const selectedNetwork = config.data?.networks.find(
      (candidate) => candidate.network === effectiveNetwork,
    );
    if (!selectedNetwork) {
      setError("Select a supported network.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const discovered = await discoverBrowserWallet(connectorId);
      if (!discovered) {
        throw new Error(
          `${connector.displayName} was not found. Install or unlock it, then try again.`,
        );
      }

      let connection = await connectBrowserWallet(discovered);
      if (connection.chainId !== selectedNetwork.chain_id) {
        await switchBrowserWalletChain(
          discovered.provider,
          selectedNetwork.chain_id,
        );
        connection = await connectBrowserWallet(discovered);
      }
      if (connection.chainId !== selectedNetwork.chain_id) {
        throw new Error(
          `Switch ${connector.displayName} to ${selectedNetwork.name} to continue.`,
        );
      }

      const challengePayload: WalletChallengeCreate = {
        provider: connectorId,
        address: connection.address,
        network: selectedNetwork.network,
      };
      const challenge = await apiRequest<WalletChallengeRead>(
        "/payment-methods/wallet/challenge",
        {
          method: "POST",
          body: JSON.stringify(challengePayload),
        },
      );
      const walletClient = walletClientFor(
        discovered.provider,
        connection.address,
        connection.chainId,
      );
      const signature = await walletClient.signMessage({
        account: connection.address,
        message: challenge.message,
      });
      const requestedName = String(
        new FormData(form).get("display_name") ?? "",
      ).trim();
      const payload: WalletPaymentMethodCreate = {
        challenge_id: challenge.challenge_id,
        signature,
        display_name:
          requestedName || `${connector.displayName} ${shortAddress(connection.address)}`,
      };
      await apiRequest<WalletPaymentMethodRead>("/payment-methods/wallet", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.paymentMethods,
      });
      toast.success(`${connector.displayName} wallet connected`);
      setOpen(false);
      reset();
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not connect this wallet."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (submitting) return;
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="lg" className="h-10 px-4">
          <WalletCards /> Connect wallet
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect a wallet</DialogTitle>
          <DialogDescription>
            AG Pay stores the verified public address and network. Your private
            keys stay in your wallet, and every x402 payment still requires an
            explicit signature.
          </DialogDescription>
        </DialogHeader>

        <form
          id="connect-wallet"
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            const connector = selectedConnector;
            if (!connector) {
              setError("No supported wallet connector is available.");
              return;
            }
            void connect(connector.id, event.currentTarget);
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="wallet_display_name">Display name</Label>
            <Input
              id="wallet_display_name"
              name="display_name"
              placeholder="Operations wallet"
              maxLength={120}
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="wallet_provider">Wallet provider</Label>
            <Select
              value={effectiveConnectorId}
              onValueChange={(value) =>
                setConnectorId(value as BrowserWalletConnectorId)
              }
            >
              <SelectTrigger id="wallet_provider" className="w-full">
                <SelectValue
                  placeholder={
                    config.isLoading ? "Loading providers…" : "Select wallet"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {enabledConnectors.map((connector) => (
                  <SelectItem key={connector.id} value={connector.id}>
                    {connector.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="wallet_network">Network</Label>
            <Select value={effectiveNetwork} onValueChange={setNetwork}>
              <SelectTrigger id="wallet_network" className="w-full">
                <SelectValue
                  placeholder={
                    config.isLoading ? "Loading networks…" : "Select network"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(config.data?.networks ?? []).map((candidate) => (
                  <SelectItem
                    key={candidate.network}
                    value={candidate.network}
                    disabled={!candidate.x402_enabled}
                  >
                    {candidate.name}
                    {candidate.is_testnet ? " · Testnet" : ""}
                    {!candidate.x402_enabled ? " · x402 unavailable" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {config.error ? (
            <p role="alert" className="text-sm text-destructive">
              {getErrorMessage(config.error, "Wallet networks could not be loaded.")}
            </p>
          ) : null}
          {!config.isLoading && !config.error && enabledNetworks.length === 0 ? (
            <p role="alert" className="text-sm text-destructive">
              No x402 wallet network is enabled for this environment.
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}

          <p className="rounded-lg border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
            MetaMask is the first supported connector. The connection layer is
            provider-based so more browser and mobile wallets can be added later.
          </p>
        </form>

        <DialogFooter className="gap-2 sm:justify-between">
          {selectedConnector ? (
            <Button variant="ghost" size="sm" asChild>
              <a
                href={selectedConnector.installUrl}
                target="_blank"
                rel="noreferrer"
              >
                Get {selectedConnector.displayName} <ExternalLink />
              </a>
            </Button>
          ) : (
            <span />
          )}
          <Button
            type="submit"
            form="connect-wallet"
            disabled={
              submitting ||
              config.isLoading ||
              !effectiveConnectorId ||
              !effectiveNetwork
            }
          >
            {submitting ? <Loader2 className="animate-spin" /> : <WalletCards />}
            {submitting
              ? `Waiting for ${selectedConnector?.displayName ?? "wallet"}…`
              : `Connect ${selectedConnector?.displayName ?? "wallet"}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
