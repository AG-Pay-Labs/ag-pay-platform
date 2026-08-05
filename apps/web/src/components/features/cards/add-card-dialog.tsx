"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CreditCard, Loader2, ShieldCheck } from "lucide-react";
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
import type { BillingProfileType, PaymentMethodCreate, PaymentMethodRead } from "@/lib/api-types";
import { queryKeys } from "@/hooks/use-api-data";

function value(form: FormData, name: string) {
  return String(form.get(name) ?? "").trim();
}

function optional(form: FormData, name: string) {
  return value(form, name) || null;
}

export function AddCardDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [profileType, setProfileType] = useState<BillingProfileType>("personal");
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

    const payload: PaymentMethodCreate = {
      display_name: value(form, "display_name"),
      provider: value(form, "provider"),
      provider_payment_method_id: value(form, "provider_reference"),
      card_brand: value(form, "card_brand"),
      card_last4: value(form, "card_last4"),
      expiry_month: Number(value(form, "expiry_month")),
      expiry_year: Number(value(form, "expiry_year")),
      billing_details:
        profileType === "personal"
          ? {
              type: "personal",
              full_name: value(form, "full_name"),
              ...shared,
            }
          : {
              type: "business",
              legal_name: value(form, "legal_name"),
              vat_number: value(form, "vat_number"),
              registration_number: optional(form, "registration_number"),
              contact_name: value(form, "contact_name"),
              ...shared,
            },
    };

    try {
      await apiRequest<PaymentMethodRead>("/payment-methods", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.cards });
      toast.success("Payment method added");
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
          <DialogTitle>Add a sandbox payment method</DialogTitle>
          <DialogDescription>
            Add a provider-tokenized reference and safe card metadata for this prototype.
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-3 rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-100">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" />
          <p>
            Never enter a full card number or CVC. AG Pay currently stores sandbox/provider
            references and does not charge a card.
          </p>
        </div>

        <form id="add-payment-method" onSubmit={handleSubmit} className="space-y-6">
          <section className="space-y-3">
            <div>
              <h3 className="font-medium">Safe card details</h3>
              <p className="text-xs text-muted-foreground">
                These fields are safe display metadata returned by a payment provider.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Display name" name="display_name" placeholder="Operations Visa" />
              <Field label="Provider" name="provider" placeholder="sandbox" />
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="provider_reference">Provider payment-method reference</Label>
                <Input
                  id="provider_reference"
                  name="provider_reference"
                  placeholder="pm_sandbox_..."
                  autoComplete="off"
                  required
                  minLength={3}
                />
              </div>
              <Field label="Card brand" name="card_brand" placeholder="Visa" />
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

          <section className="space-y-4 border-t pt-5">
            <div>
              <h3 className="font-medium">Billing profile</h3>
              <p className="text-xs text-muted-foreground">
                Choose the legal owner of this payment method.
              </p>
            </div>
            <RadioGroup
              value={profileType}
              onValueChange={(next) => setProfileType(next as BillingProfileType)}
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
                <Field label="Full legal name" name="full_name" placeholder="Ada Lovelace" />
              ) : (
                <>
                  <Field label="Legal business name" name="legal_name" placeholder="Analytical Labs SL" />
                  <Field label="VAT number" name="vat_number" placeholder="ESB12345678" />
                  <Field label="Registration number" name="registration_number" placeholder="Optional" required={false} />
                  <Field label="Billing contact" name="contact_name" placeholder="Ada Lovelace" />
                </>
              )}
              <Field label="Billing email" name="email" type="email" placeholder="billing@example.com" />
              <Field label="Phone" name="phone" type="tel" placeholder="Optional" required={false} />
              <div className="sm:col-span-2">
                <Field label="Address line 1" name="line1" placeholder="1 Market Street" />
              </div>
              <div className="sm:col-span-2">
                <Field label="Address line 2" name="line2" placeholder="Optional" required={false} />
              </div>
              <Field label="City" name="city" placeholder="Madrid" />
              <Field label="Region" name="region" placeholder="Madrid" required={false} />
              <Field label="Postal code" name="postal_code" placeholder="28001" />
              <Field label="Country code" name="country" placeholder="ES" minLength={2} maxLength={2} />
            </div>
          </section>

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </form>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button type="submit" form="add-payment-method" disabled={submitting}>
            {submitting ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            Add safe payment method
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
      <RadioGroupItem id={`profile-${value}`} value={value} className="mt-0.5" />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-xs font-normal text-muted-foreground">{description}</span>
      </span>
    </Label>
  );
}

function Field({
  label,
  name,
  required = true,
  ...props
}: React.ComponentProps<typeof Input> & { label: string; name: string; required?: boolean }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={name}>{label}</Label>
      <Input id={name} name={name} required={required} {...props} />
    </div>
  );
}
