"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { x402Client } from "@x402/core/client";
import type {
  PaymentPayload,
  PaymentRequired,
  PaymentRequirements,
} from "@x402/core/types";
import {
  ExactEvmScheme,
  PERMIT2_ADDRESS,
  type ClientEvmSigner,
} from "@x402/evm";
import {
  formatUnits,
  getAddress,
  isAddress,
  type TypedData,
  type TypedDataDomain,
} from "viem";
import { Loader2, PenLine, ShieldCheck } from "lucide-react";
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
import { queryKeys } from "@/hooks/use-api-data";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type {
  CartItemRead,
  EvmAddress,
  X402AuthorizeCreate,
  X402SigningRequestRead,
} from "@/lib/api-types";
import { shortAddress } from "@/lib/payment-methods";
import {
  connectBrowserWallet,
  discoverBrowserWallet,
  publicClientFor,
  switchBrowserWalletChain,
  walletClientFor,
  type BrowserWalletConnectorId,
} from "@/lib/wallets/connectors";

const ERC20_APPROVAL_ABI = [
  {
    type: "function",
    name: "allowance",
    stateMutability: "view",
    inputs: [
      { name: "owner", type: "address" },
      { name: "spender", type: "address" },
    ],
    outputs: [{ name: "amount", type: "uint256" }],
  },
  {
    type: "function",
    name: "approve",
    stateMutability: "nonpayable",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ name: "success", type: "bool" }],
  },
] as const;

type Progress =
  | "idle"
  | "connecting"
  | "switching"
  | "allowance"
  | "signing"
  | "submitting";

export function X402AuthorizeDialog({ item }: { item: CartItemRead }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<X402SigningRequestRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<Progress>("idle");
  const [error, setError] = useState<string | null>(null);

  async function loadSigningRequest() {
    setLoading(true);
    setError(null);
    try {
      const next = await apiRequest<X402SigningRequestRead>(
        `/cart-items/${item.id}/x402/signing-request`,
      );
      const requirement = selectExactRequirement(
        next.payment_required,
        next.wallet.network,
      );
      assertMatchesApprovedTerms(requirement, next.payment_required, item);
      setRequest(next);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not load the x402 payment request."));
    } finally {
      setLoading(false);
    }
  }

  async function authorize() {
    if (!request || progress !== "idle") return;
    setError(null);

    try {
      const { payment_required: paymentRequired, wallet } = request;
      const requirement = selectExactRequirement(
        paymentRequired,
        wallet.network,
      );
      assertMatchesApprovedTerms(requirement, paymentRequired, item);
      if (wallet.provider !== "metamask") {
        throw new Error(`The ${wallet.provider} connector is not available yet.`);
      }

      setProgress("connecting");
      const discovered = await discoverBrowserWallet(
        wallet.provider as BrowserWalletConnectorId,
      );
      if (!discovered) {
        throw new Error("MetaMask was not found. Install or unlock it, then try again.");
      }

      let connection = await connectBrowserWallet(discovered);
      if (connection.address.toLowerCase() !== wallet.address.toLowerCase()) {
        throw new Error(
          `Connect ${shortAddress(wallet.address)} in MetaMask to authorize this payment.`,
        );
      }
      if (connection.chainId !== wallet.chain_id) {
        setProgress("switching");
        await switchBrowserWalletChain(discovered.provider, wallet.chain_id);
        connection = await connectBrowserWallet(discovered);
      }
      if (connection.chainId !== wallet.chain_id) {
        throw new Error(`MetaMask did not switch to ${wallet.network}.`);
      }
      if (connection.address.toLowerCase() !== wallet.address.toLowerCase()) {
        throw new Error(
          `Connect ${shortAddress(wallet.address)} in MetaMask to authorize this payment.`,
        );
      }

      const walletClient = walletClientFor(
        discovered.provider,
        connection.address,
        connection.chainId,
      );
      const publicClient = publicClientFor(
        discovered.provider,
        connection.chainId,
      );

      if (requirementTransferMethod(requirement) === "permit2") {
        setProgress("allowance");
        await ensureExactPermit2Allowance({
          requirement,
          address: connection.address,
          walletClient,
          publicClient,
        });
      }

      setProgress("signing");
      const signer: ClientEvmSigner = {
        address: connection.address,
        signTypedData: async ({ domain, types, primaryType, message }) =>
          walletClient.signTypedData({
            account: connection.address,
            domain: domain as TypedDataDomain,
            types: types as TypedData,
            primaryType,
            message,
          }),
      };
      const client = x402Client.fromConfig({
        schemes: [
          {
            network: requirement.network,
            client: new ExactEvmScheme(signer),
            x402Version: 2,
          },
        ],
        spendControls: {
          maxAmountPerPayment: false,
          allowedAssets: [
            {
              network: requirement.network,
              asset: requirement.asset,
              maxAmountPerPayment: requirement.amount,
            },
          ],
        },
        policies: [
          (version, requirements) => {
            if (version !== 2) {
              throw new Error("Only x402 v2 is supported.");
            }
            return requirements.filter((candidate) =>
              sameRequirement(candidate, requirement),
            );
          },
        ],
        paymentRequirementsSelector: (version, requirements) => {
          if (version !== 2) throw new Error("Only x402 v2 is supported.");
          const exact = requirements[0];
          if (!exact) throw new Error("The approved x402 requirement changed.");
          return exact;
        },
      });
      const paymentPayload: PaymentPayload = await client.createPaymentPayload({
        ...paymentRequired,
        accepts: [requirement],
      });

      setProgress("submitting");
      const payload: X402AuthorizeCreate = {
        payment_payload: paymentPayload,
      };
      await apiRequest<CartItemRead>(`/cart-items/${item.id}/x402/authorize`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.cart });
      toast.success("x402 payment authorized");
      setOpen(false);
      setRequest(null);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not authorize this x402 payment."));
    } finally {
      setProgress("idle");
    }
  }

  const requirement = request
    ? selectExactRequirement(request.payment_required, request.wallet.network)
    : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (progress !== "idle") return;
        setOpen(next);
        if (next) void loadSigningRequest();
        else {
          setRequest(null);
          setError(null);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <PenLine /> Sign x402 payment
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Authorize x402 payment</DialogTitle>
          <DialogDescription>
            Review the exact onchain authorization, then sign it in MetaMask.
            AG Pay never receives your private key and will not retry an
            uncertain submission automatically.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading payment terms
          </div>
        ) : requirement && request ? (
          <dl className="grid gap-4 rounded-xl border bg-muted/35 p-4 text-sm sm:grid-cols-2">
            <Term label="Resource" value={resourceLabel(request.payment_required)} />
            <Term
              label="Amount"
              value={requirementAmount(requirement)}
            />
            <Term
              label="Atomic amount"
              value={requirement.amount}
              monospace
            />
            <Term label="Network" value={requirement.network} monospace />
            <Term
              label="Asset"
              value={shortAddress(requirement.asset)}
              title={requirement.asset}
              monospace
            />
            <Term
              label="Recipient"
              value={shortAddress(requirement.payTo)}
              title={requirement.payTo}
              monospace
            />
            <Term
              label="Wallet"
              value={shortAddress(request.wallet.address)}
              title={request.wallet.address}
              monospace
            />
            {requirementTransferMethod(requirement) === "permit2" ? (
              <p className="sm:col-span-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                This token uses Permit2. If its current allowance differs from
                the exact amount, MetaMask will ask for a bounded onchain
                approval for this payment only.
              </p>
            ) : null}
          </dl>
        ) : null}

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={progress !== "idle"}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void authorize()}
            disabled={!request || loading || progress !== "idle"}
          >
            {progress !== "idle" ? (
              <Loader2 className="animate-spin" />
            ) : (
              <ShieldCheck />
            )}
            {progressLabel(progress)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function selectExactRequirement(
  paymentRequired: PaymentRequired,
  network: string,
): PaymentRequirements {
  if (paymentRequired.x402Version !== 2) {
    throw new Error("Only x402 protocol version 2 is supported.");
  }
  const requirement = paymentRequired.accepts.find(
    (candidate) => candidate.scheme === "exact" && candidate.network === network,
  );
  if (!requirement) {
    throw new Error("No exact x402 payment option matches the connected wallet network.");
  }
  if (!/^eip155:\d+$/.test(requirement.network)) {
    throw new Error("Only EVM x402 networks are supported by MetaMask.");
  }
  if (!isAddress(requirement.asset) || !isAddress(requirement.payTo)) {
    throw new Error("The x402 token or recipient address is invalid.");
  }
  const amount = BigInt(requirement.amount);
  if (amount <= BigInt(0)) {
    throw new Error("The x402 payment amount must be positive.");
  }
  return requirement;
}

function sameRequirement(
  left: PaymentRequirements,
  right: PaymentRequirements,
): boolean {
  return (
    left.scheme === right.scheme &&
    left.network === right.network &&
    left.asset.toLowerCase() === right.asset.toLowerCase() &&
    left.amount === right.amount &&
    left.payTo.toLowerCase() === right.payTo.toLowerCase()
  );
}

function assertMatchesApprovedTerms(
  requirement: PaymentRequirements,
  paymentRequired: PaymentRequired,
  item: CartItemRead,
) {
  const approved = item.x402;
  if (!approved) {
    throw new Error("This proposal has no approved x402 payment terms.");
  }
  const transferMethod = requirementTransferMethod(requirement);
  if (
    requirement.network !== approved.network ||
    requirement.asset.toLowerCase() !== approved.asset.toLowerCase() ||
    requirement.amount !== approved.amount_atomic ||
    requirement.payTo.toLowerCase() !== approved.pay_to.toLowerCase() ||
    transferMethod !== approved.transfer_method ||
    paymentRequired.resource.url !== approved.resource_url
  ) {
    throw new Error(
      "The current x402 signing request does not match the terms you approved.",
    );
  }
}

function requirementTransferMethod(
  requirement: PaymentRequirements,
): "eip3009" | "permit2" {
  const method = requirement.extra?.assetTransferMethod ?? "eip3009";
  if (method !== "eip3009" && method !== "permit2") {
    throw new Error(`Unsupported x402 asset transfer method: ${String(method)}.`);
  }
  return method;
}

async function ensureExactPermit2Allowance({
  requirement,
  address,
  walletClient,
  publicClient,
}: {
  requirement: PaymentRequirements;
  address: EvmAddress;
  walletClient: ReturnType<typeof walletClientFor>;
  publicClient: ReturnType<typeof publicClientFor>;
}) {
  const token = getAddress(requirement.asset);
  const requiredAmount = BigInt(requirement.amount);
  const currentAllowance = await publicClient.readContract({
    address: token,
    abi: ERC20_APPROVAL_ABI,
    functionName: "allowance",
    args: [address, PERMIT2_ADDRESS],
  });
  if (currentAllowance === requiredAmount) return;

  // Tokens such as USDT can require resetting a non-zero allowance before a
  // new value. Both approvals are explicit and bounded; no unlimited helper is
  // used.
  if (currentAllowance > BigInt(0)) {
    await sendApproval(BigInt(0), token, address, walletClient, publicClient);
  }
  await sendApproval(requiredAmount, token, address, walletClient, publicClient);
}

async function sendApproval(
  amount: bigint,
  token: EvmAddress,
  address: EvmAddress,
  walletClient: ReturnType<typeof walletClientFor>,
  publicClient: ReturnType<typeof publicClientFor>,
) {
  const hash = await walletClient.writeContract({
    account: address,
    address: token,
    abi: ERC20_APPROVAL_ABI,
    functionName: "approve",
    args: [PERMIT2_ADDRESS, amount],
  });
  const receipt = await publicClient.waitForTransactionReceipt({
    hash,
    confirmations: 1,
    timeout: 60_000,
  });
  if (receipt.status !== "success") {
    throw new Error("The Permit2 token approval failed onchain.");
  }
}

function requirementAmount(requirement: PaymentRequirements): string {
  const decimals = requirement.extra?.decimals;
  const symbol =
    typeof requirement.extra?.symbol === "string"
      ? requirement.extra.symbol
      : typeof requirement.extra?.name === "string"
        ? requirement.extra.name
        : "token units";
  if (
    typeof decimals === "number" &&
    Number.isInteger(decimals) &&
    decimals >= 0 &&
    decimals <= 36
  ) {
    return `${formatUnits(BigInt(requirement.amount), decimals)} ${symbol}`;
  }
  return `${requirement.amount} atomic ${symbol}`;
}

function resourceLabel(paymentRequired: PaymentRequired): string {
  if (paymentRequired.resource.serviceName) {
    return paymentRequired.resource.serviceName;
  }
  try {
    return new URL(paymentRequired.resource.url).hostname;
  } catch {
    return paymentRequired.resource.url;
  }
}

function progressLabel(progress: Progress): string {
  switch (progress) {
    case "connecting":
      return "Connecting MetaMask…";
    case "switching":
      return "Switching network…";
    case "allowance":
      return "Checking allowance…";
    case "signing":
      return "Waiting for signature…";
    case "submitting":
      return "Submitting authorization…";
    case "idle":
      return "Sign & authorize";
  }
}

function Term({
  label,
  value,
  title,
  monospace = false,
}: {
  label: string;
  value: string;
  title?: string;
  monospace?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={`mt-1 truncate font-medium ${monospace ? "font-mono text-xs" : ""}`}
        title={title}
      >
        {value}
      </dd>
    </div>
  );
}
