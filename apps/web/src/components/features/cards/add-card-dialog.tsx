"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CreditCard, FlaskConical, Loader2, ShieldCheck } from "lucide-react";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { apiRequest, getErrorMessage } from "@/lib/api-client";
import type {
  BillingProfileType,
  DirectCardPaymentMethodCreate,
  PaymentMethodCreate,
  PaymentMethodRead,
} from "@/lib/api-types";
import { queryKeys } from "@/hooks/use-api-data";

function value(form: FormData, name: string) {
  return String(form.get(name) ?? "").trim();
}

function optional(form: FormData, name: string) {
  return value(form, name) || null;
}

type SetupMode = "direct" | "sandbox" | "link" | "reference";

export function AddCardDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [setupMode, setSetupMode] = useState<SetupMode>("sandbox");
  const [profileType, setProfileType] =
    useState<BillingProfileType>("personal");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);

    const address = {
      line1: value(form, "line1"),
      line2: optional(form, "line2"),
      city: value(form, "city"),
      region: optional(form, "region"),
      postal_code: value(form, "postal_code"),
      country: value(form, "country").toUpperCase(),
    };

    const shared = {
      email: value(form, "email"),
      phone: optional(form, "phone"),
      address,
    };

    const billingDetails =
      profileType === "personal"
        ? {
            type: "personal" as const,
            full_name: value(form, "full_name"),
            ...shared,
          }
        : {
            type: "business" as const,
            legal_name: value(form, "legal_name"),
            vat_number: value(form, "vat_number"),
            registration_number: optional(form, "registration_number"),
            contact_name: value(form, "contact_name"),
            ...shared,
          };

    try {
      if (setupMode === "direct") {
        const panInput = event.currentTarget.elements.namedItem("card_number");
        const payload: DirectCardPaymentMethodCreate = {
          display_name: value(form, "display_name"),
          card_number: value(form, "card_number").replace(/[\s-]/g, ""),
          expiry_month: Number(value(form, "expiry_month")),
          expiry_year: Number(value(form, "expiry_year")),
          billing_details: billingDetails,
        };
        const serializedPayload = JSON.stringify(payload);
        payload.card_number = "";
        form.delete("card_number");
        const request = apiRequest<PaymentMethodRead>(
          "/payment-methods/direct-card",
          {
            method: "POST",
            body: serializedPayload,
          }
        );
        if (panInput instanceof HTMLInputElement) panInput.value = "";
        await request;
      } else {
        const paymentMethodFields =
          setupMode === "sandbox"
            ? {
                display_name: "Stripe sandbox Visa",
                provider: "prototype-vault",
                provider_payment_method_id: "pm_stripe_demo_success",
                card_brand: "Visa",
                card_last4: "4242",
                expiry_month: 12,
                expiry_year: 2034,
              }
            : {
                display_name:
                  setupMode === "link"
                    ? "Stripe Link wallet"
                    : value(form, "display_name"),
                provider:
                  setupMode === "link"
                    ? "stripe_link"
                    : value(form, "provider"),
                provider_payment_method_id: value(form, "provider_reference"),
                card_brand: value(form, "card_brand"),
                card_last4: value(form, "card_last4"),
                expiry_month: Number(value(form, "expiry_month")),
                expiry_year: Number(value(form, "expiry_year")),
              };
        const payload: PaymentMethodCreate = {
          ...paymentMethodFields,
          billing_details: billingDetails,
        };
        await apiRequest<PaymentMethodRead>("/payment-methods", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.cards });
      toast.success(
        setupMode === "direct"
          ? "Direct card added"
          : setupMode === "link"
          ? "Stripe Link wallet added"
          : "Payment method added"
      );
      setOpen(false);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not add the payment method."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="lg" className="h-10 px-4">
          <CreditCard />
          Add payment method
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add a payment method</DialogTitle>
          <DialogDescription>
            Store a card directly for local research, add the ready-to-use
            Stripe sandbox card, connect a Stripe Link wallet, or register an
            existing Stripe Issuing virtual card.
          </DialogDescription>
        </DialogHeader>

        <form
          id="add-payment-method"
          onSubmit={handleSubmit}
          className="space-y-6"
        >
          <RadioGroup
            value={setupMode}
            onValueChange={(next) => setSetupMode(next as SetupMode)}
            className="grid gap-3 sm:grid-cols-2"
          >
            <SetupChoice
              value="direct"
              title="Direct card"
              description="Local research with encrypted card storage"
            />
            <SetupChoice
              value="sandbox"
              title="Stripe sandbox card"
              description="Recommended for a complete test checkout"
            />
            <SetupChoice
              value="link"
              title="Stripe Link"
              description="US-only CLI prototype with Link approval"
            />
            <SetupChoice
              value="reference"
              title="Stripe Issuing"
              description="For an existing Stripe Issuing virtual card"
            />
          </RadioGroup>

          {setupMode === "direct" ? (
            <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" />
              <p>
                <strong>Local research only.</strong> AG Pay stores the card
                number encrypted. The CVC is not stored with the card; you will
                enter it while approving each managed checkout, and it is held
                only briefly for that checkout. Card credentials are never sent
                to the agent.
              </p>
            </div>
          ) : setupMode === "sandbox" ? (
            <div className="flex gap-3 rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm text-violet-950 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-100">
              <FlaskConical className="mt-0.5 size-4 shrink-0" />
              <p>
                This adds Stripe&apos;s successful sandbox Visa ending in 4242.
                Assign it to an agent, approve a{" "}
                <span className="font-mono">stripe-hosted</span> checkout, and
                the worker will complete the payment with no real money
                movement.
              </p>
            </div>
          ) : setupMode === "link" ? (
            <div className="flex gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" />
              <div className="space-y-2">
                <p>
                  <strong>US Link accounts only.</strong> Follow Step 6 in{" "}
                  <span className="font-mono">
                    docs/stripe-link-agent-payments.md
                  </span>{" "}
                  to create the private owner-scoped auth file. Both commands
                  must use that same file:
                </p>
                <div className="space-y-1 overflow-x-auto rounded-md bg-blue-950/5 p-2 font-mono text-xs dark:bg-white/5">
                  <p className="whitespace-nowrap">
                    link-cli auth login --auth{" "}
                    {"<auth-directory>/<owner UUID>.json"}
                  </p>
                  <p className="whitespace-nowrap">
                    link-cli payment-methods list --auth{" "}
                    {"<auth-directory>/<owner UUID>.json"} --format json
                  </p>
                </div>
                <p>
                  Do not use Link&apos;s default global session or paste the auth
                  file or its contents into AG Pay. Paste only the command&apos;s
                  opaque{" "}
                  <span className="font-mono">csmrpd_...</span> ID below—never
                  a card number, CVC, or generated one-time credential.
                </p>
                <p>
                  This prototype has two approvals: first in AG Pay, then in
                  Link. Keep Link spend requests in test mode for the first
                  end-to-end run; test credentials do not charge the underlying
                  card.
                </p>
              </div>
            </div>
          ) : (
            <div className="flex gap-3 rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-100">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" />
              <p>
                Enter only a Stripe Issuing virtual-card reference beginning
                with{` `}
                <span className="font-mono">ic_</span>. Never enter a full card
                number or CVC.
              </p>
            </div>
          )}

          {setupMode === "direct" ? (
            <section className="space-y-3">
              <div>
                <h3 className="font-medium">Card details</h3>
                <p className="text-xs text-muted-foreground">
                  The API derives the brand and last four digits. AG Pay never
                  returns the full card number after this request.
                </p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <Field
                    label="Display name"
                    name="display_name"
                    placeholder="Personal Visa"
                    maxLength={120}
                  />
                </div>
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="direct_card_pan">Card number</Label>
                  <Input
                    id="direct_card_pan"
                    name="card_number"
                    type="text"
                    inputMode="numeric"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    placeholder="1234 5678 9012 3456"
                    minLength={12}
                    maxLength={23}
                    pattern="[0-9 -]{12,23}"
                    title="Enter 12 to 19 card digits; spaces and hyphens are allowed"
                    aria-describedby="direct-card-pan-help"
                    required
                  />
                  <p
                    id="direct-card-pan-help"
                    className="text-xs text-muted-foreground"
                  >
                    Enter 12 to 19 digits. This field is cleared as soon as the
                    enrollment request is sent.
                  </p>
                </div>
                <Field
                  label="Expiry month"
                  name="expiry_month"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={12}
                  placeholder="12"
                  autoComplete="off"
                />
                <Field
                  label="Expiry year"
                  name="expiry_year"
                  type="number"
                  inputMode="numeric"
                  min={new Date().getFullYear()}
                  max={2200}
                  placeholder="2030"
                  autoComplete="off"
                />
              </div>
            </section>
          ) : setupMode === "link" ? (
            <section className="space-y-3">
              <div>
                <h3 className="font-medium">Link wallet reference</h3>
                <p className="text-xs text-muted-foreground">
                  Copy the ID and safe display metadata from the Link CLI
                  payment-method list. AG Pay never asks for the underlying
                  card number or CVC.
                </p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="link_provider_reference">
                    Link payment method ID
                  </Label>
                  <Input
                    id="link_provider_reference"
                    name="provider_reference"
                    placeholder="csmrpd_..."
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                    minLength={9}
                    maxLength={255}
                    pattern="csmrpd_[A-Za-z0-9]+"
                    title="Enter the opaque Link payment method ID beginning with csmrpd_"
                  />
                </div>
                <Field
                  label="Card brand (safe metadata)"
                  name="card_brand"
                  placeholder="Visa"
                  maxLength={32}
                />
                <Field
                  label="Last four digits (safe metadata)"
                  name="card_last4"
                  placeholder="4242"
                  inputMode="numeric"
                  pattern="[0-9]{4}"
                  maxLength={4}
                />
                <Field
                  label="Expiry month (safe metadata)"
                  name="expiry_month"
                  type="number"
                  min={1}
                  max={12}
                  placeholder="12"
                />
                <Field
                  label="Expiry year (safe metadata)"
                  name="expiry_year"
                  type="number"
                  min={new Date().getFullYear()}
                  max={2200}
                  placeholder="2030"
                />
              </div>
            </section>
          ) : setupMode === "reference" ? (
            <section className="space-y-3">
              <div>
                <h3 className="font-medium">Safe card details</h3>
                <p className="text-xs text-muted-foreground">
                  These fields are safe display metadata returned by a payment
                  provider.
                </p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="Display name"
                  name="display_name"
                  placeholder="Operations Visa"
                />
                <Field
                  label="Provider"
                  name="provider"
                  placeholder="stripe_issuing"
                />
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="provider_reference">
                    Provider payment-method reference
                  </Label>
                  <Input
                    id="provider_reference"
                    name="provider_reference"
                    placeholder="ic_... or pm_..."
                    autoComplete="off"
                    required
                    minLength={3}
                  />
                </div>
                <Field
                  label="Card brand"
                  name="card_brand"
                  placeholder="Visa"
                />
                <Field
                  label="Last four digits"
                  name="card_last4"
                  placeholder="4242"
                  inputMode="numeric"
                  pattern="[0-9]{4}"
                  maxLength={4}
                />
                <Field
                  label="Expiry month"
                  name="expiry_month"
                  type="number"
                  min={1}
                  max={12}
                  placeholder="12"
                />
                <Field
                  label="Expiry year"
                  name="expiry_year"
                  type="number"
                  min={new Date().getFullYear()}
                  max={2200}
                  placeholder="2030"
                />
              </div>
            </section>
          ) : null}

          <section className="space-y-4 border-t pt-5">
            <div>
              <h3 className="font-medium">Billing profile</h3>
              <p className="text-xs text-muted-foreground">
                Choose the legal owner of this payment method.
              </p>
            </div>
            <RadioGroup
              value={profileType}
              onValueChange={(next) =>
                setProfileType(next as BillingProfileType)
              }
              className="grid gap-3 sm:grid-cols-2"
            >
              <ProfileChoice
                value="personal"
                title="Personal"
                description="An individual cardholder"
              />
              <ProfileChoice
                value="business"
                title="Business"
                description="A legal entity with VAT details"
              />
            </RadioGroup>

            <div className="grid gap-4 sm:grid-cols-2">
              {profileType === "personal" ? (
                <Field
                  label="Full legal name"
                  name="full_name"
                  placeholder="Ada Lovelace"
                />
              ) : (
                <>
                  <Field
                    label="Legal business name"
                    name="legal_name"
                    placeholder="Analytical Labs SL"
                  />
                  <Field
                    label="VAT number"
                    name="vat_number"
                    placeholder="ESB12345678"
                  />
                  <Field
                    label="Registration number"
                    name="registration_number"
                    placeholder="Optional"
                    required={false}
                  />
                  <Field
                    label="Billing contact"
                    name="contact_name"
                    placeholder="Ada Lovelace"
                  />
                </>
              )}
              <Field
                label="Billing email"
                name="email"
                type="email"
                placeholder="billing@example.com"
              />
              <Field
                label="Phone"
                name="phone"
                type="tel"
                placeholder="Optional"
                required={false}
              />
              <div className="sm:col-span-2">
                <Field
                  label="Address line 1"
                  name="line1"
                  placeholder="1 Market Street"
                />
              </div>
              <div className="sm:col-span-2">
                <Field
                  label="Address line 2"
                  name="line2"
                  placeholder="Optional"
                  required={false}
                />
              </div>
              <Field label="City" name="city" placeholder="Madrid" />
              <Field
                label="Region"
                name="region"
                placeholder="Madrid"
                required={false}
              />
              <Field
                label="Postal code"
                name="postal_code"
                placeholder="28001"
              />
              <Field
                label="Country code"
                name="country"
                placeholder="ES"
                minLength={2}
                maxLength={2}
              />
            </div>
          </section>

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </form>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button type="submit" form="add-payment-method" disabled={submitting}>
            {submitting ? (
              <Loader2 className="animate-spin" />
            ) : (
              <ShieldCheck />
            )}
            {setupMode === "direct"
              ? "Store direct card"
              : setupMode === "sandbox"
                ? "Add Stripe sandbox card"
                : setupMode === "link"
                ? "Add Stripe Link wallet"
                : "Add payment method"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SetupChoice({
  value,
  title,
  description,
}: {
  value: SetupMode;
  title: string;
  description: string;
}) {
  return (
    <Label
      htmlFor={`setup-${value}`}
      className="flex cursor-pointer items-start gap-3 rounded-lg border bg-card p-3 has-data-[state=checked]:border-primary has-data-[state=checked]:ring-2 has-data-[state=checked]:ring-primary/15"
    >
      <RadioGroupItem id={`setup-${value}`} value={value} className="mt-0.5" />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-xs font-normal text-muted-foreground">
          {description}
        </span>
      </span>
    </Label>
  );
}

function ProfileChoice({
  value,
  title,
  description,
}: {
  value: BillingProfileType;
  title: string;
  description: string;
}) {
  return (
    <Label
      htmlFor={`profile-${value}`}
      className="flex cursor-pointer items-start gap-3 rounded-lg border bg-card p-3 has-data-[state=checked]:border-primary has-data-[state=checked]:ring-2 has-data-[state=checked]:ring-primary/15"
    >
      <RadioGroupItem
        id={`profile-${value}`}
        value={value}
        className="mt-0.5"
      />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-xs font-normal text-muted-foreground">
          {description}
        </span>
      </span>
    </Label>
  );
}

function Field({
  label,
  name,
  required = true,
  ...props
}: React.ComponentProps<typeof Input> & {
  label: string;
  name: string;
  required?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={name}>{label}</Label>
      <Input id={name} name={name} required={required} {...props} />
    </div>
  );
}
