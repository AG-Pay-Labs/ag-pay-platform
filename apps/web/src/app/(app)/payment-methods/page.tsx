"use client";

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  Copy,
  CreditCard,
  Loader2,
  ShieldAlert,
  WalletCards,
} from "lucide-react";
import { toast } from "sonner";

import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/app";
import { AddCardDialog } from "@/components/features/cards/add-card-dialog";
import { ConnectWalletDialog } from "@/components/features/payment-methods/connect-wallet-dialog";
import { PaymentMethodLabel } from "@/components/features/payment-methods/payment-method-label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { queryKeys, usePaymentMethods } from "@/hooks/use-api-data";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type {
  CardPaymentMethodRead,
  PaymentMethodRead,
  WalletPaymentMethodRead,
} from "@/lib/api-types";
import {
  isCardPaymentMethod,
  isWalletPaymentMethod,
  shortAddress,
} from "@/lib/payment-methods";
import { chainLabel } from "@/lib/wallets/chains";
import { formatDateTime } from "@/utils/format";

type PaymentMethodTab = "cards" | "wallets";

export default function PaymentMethodsPage() {
  return (
    <Suspense fallback={<LoadingState variant="cards" rows={4} />}>
      <PaymentMethodsContent />
    </Suspense>
  );
}

function PaymentMethodsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const methods = usePaymentMethods();
  const requestedTab = searchParams.get("tab");
  const tab: PaymentMethodTab = requestedTab === "wallets" ? "wallets" : "cards";
  const cards = useMemo(
    () => (methods.data ?? []).filter(isCardPaymentMethod),
    [methods.data],
  );
  const wallets = useMemo(
    () => (methods.data ?? []).filter(isWalletPaymentMethod),
    [methods.data],
  );

  function selectTab(next: string) {
    const normalized: PaymentMethodTab = next === "wallets" ? "wallets" : "cards";
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", normalized);
    router.replace(`/payment-methods?${params.toString()}`, { scroll: false });
  }

  return (
    <>
      <PageHeader
        eyebrow="Payment permissions"
        title="Payment methods"
        description="Manage the cards and verified wallets that agents may use after policy checks and human approval."
        actions={tab === "wallets" ? <ConnectWalletDialog /> : <AddCardDialog />}
      />

      <Tabs value={tab} onValueChange={selectTab} className="flex-col gap-4">
        <TabsList className="h-10 w-full justify-start rounded-xl p-1 sm:w-fit">
          <TabsTrigger value="cards" className="h-8 gap-2 px-4">
            <CreditCard /> Cards
            <Count value={cards.length} />
          </TabsTrigger>
          <TabsTrigger value="wallets" className="h-8 gap-2 px-4">
            <WalletCards /> Wallets
            <Count value={wallets.length} />
          </TabsTrigger>
        </TabsList>

        {methods.isLoading ? <LoadingState variant="cards" rows={4} /> : null}
        {methods.error ? (
          <ErrorState
            description="Payment methods could not be loaded."
            retry={() => methods.refetch()}
          />
        ) : null}

        {!methods.isLoading && !methods.error ? (
          <>
            <TabsContent value="cards">
              <CardsPanel cards={cards} />
            </TabsContent>
            <TabsContent value="wallets">
              <WalletsPanel wallets={wallets} />
            </TabsContent>
          </>
        ) : null}
      </Tabs>
    </>
  );
}

function Count({ value }: { value: number }) {
  return (
    <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-background/70 px-1.5 py-0.5 text-[10px] font-bold tabular-nums">
      {value}
    </span>
  );
}

function CardsPanel({ cards }: { cards: CardPaymentMethodRead[] }) {
  if (cards.length === 0) {
    return (
      <EmptyState
        icon={CreditCard}
        title="No cards"
        description="Add a card, assign it to an agent, and use it for a supervised compatible checkout."
        action={<AddCardDialog />}
      />
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {cards.map((card) => (
        <Card key={card.id} className={card.status === "disabled" ? "opacity-70" : ""}>
          <CardHeader className="pb-3">
            <PaymentMethodLabel method={card} />
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <InfoRow label="Billing profile" value={billingName(card)} />
            <InfoRow label="Provider" value={providerLabel(card.provider)} />
            <InfoRow label="Added" value={formatDateTime(card.created_at)} />
          </CardContent>
          <MethodFooter method={card} />
        </Card>
      ))}
    </div>
  );
}

function WalletsPanel({ wallets }: { wallets: WalletPaymentMethodRead[] }) {
  if (wallets.length === 0) {
    return (
      <EmptyState
        icon={WalletCards}
        title="No wallets"
        description="Connect MetaMask with a signed ownership challenge. Mainnet and configured testnets are supported."
        action={<ConnectWalletDialog />}
      />
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {wallets.map((wallet) => (
        <Card key={wallet.id} className={wallet.status === "disabled" ? "opacity-70" : ""}>
          <CardHeader className="pb-3">
            <PaymentMethodLabel method={wallet} />
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Address</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 max-w-[70%] gap-1.5 px-2 font-mono text-xs"
                title={wallet.address}
                onClick={() => void copyAddress(wallet.address)}
              >
                <span className="truncate">{shortAddress(wallet.address)}</span>
                <Copy className="size-3.5" />
              </Button>
            </div>
            <InfoRow
              label="Network"
              value={`${chainLabel(wallet.chain_id)} · ${wallet.network}`}
              monospace
            />
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Environment</span>
              {wallet.is_testnet ? (
                <Badge variant="outline">Testnet</Badge>
              ) : (
                <Badge variant="secondary">Mainnet</Badge>
              )}
            </div>
            <InfoRow label="Added" value={formatDateTime(wallet.created_at)} />
          </CardContent>
          <MethodFooter method={wallet} />
        </Card>
      ))}
    </div>
  );
}

function MethodFooter({ method }: { method: PaymentMethodRead }) {
  return (
    <CardFooter className="mt-auto flex items-center justify-between gap-3 border-t pt-4">
      <p className="text-xs text-muted-foreground">Assign from an agent’s details.</p>
      {method.status === "active" ? <DisablePaymentMethodDialog method={method} /> : null}
    </CardFooter>
  );
}

function InfoRow({
  label,
  value,
  monospace = false,
}: {
  label: string;
  value: string;
  monospace?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={monospace ? "break-all text-right font-mono text-xs" : "text-right font-medium"}>
        {value}
      </span>
    </div>
  );
}

function DisablePaymentMethodDialog({ method }: { method: PaymentMethodRead }) {
  const queryClient = useQueryClient();
  const [submitting, setSubmitting] = useState(false);

  async function disable() {
    setSubmitting(true);
    try {
      await apiRequest<void>(`/payment-methods/${method.id}`, { method: "DELETE" });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.paymentMethods }),
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
      ]);
      toast.success(`${method.display_name} disabled`);
    } catch (caught) {
      toast.error(getErrorMessage(caught, "Could not disable this payment method."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive">
          Disable
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Disable {method.display_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This immediately removes every agent assignment. Historical purchase attribution is retained, and this method cannot currently be re-enabled.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep active</AlertDialogCancel>
          <AlertDialogAction
            onClick={disable}
            disabled={submitting}
            className="bg-destructive text-white hover:bg-destructive/90"
          >
            {submitting ? <Loader2 className="animate-spin" /> : <ShieldAlert />}
            Disable method
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function billingName(card: CardPaymentMethodRead) {
  return card.billing_details.type === "business"
    ? card.billing_details.legal_name
    : card.billing_details.full_name;
}

function providerLabel(provider: string) {
  return provider.replaceAll("_", " ").replaceAll("-", " ");
}

async function copyAddress(address: string) {
  try {
    await navigator.clipboard.writeText(address);
    toast.success("Wallet address copied");
  } catch {
    toast.error("Could not copy the wallet address.");
  }
}
