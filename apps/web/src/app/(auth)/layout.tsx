import { Bot, CheckCircle2, ShieldCheck, WalletCards } from "lucide-react";

import { BrandLockup } from "@/components/app/brand";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-svh bg-background lg:grid-cols-[minmax(0,1fr)_minmax(440px,0.78fr)]">
      <section className="relative hidden overflow-hidden bg-zinc-950 p-10 text-white lg:flex lg:flex-col lg:justify-between xl:p-14">
        <BrandLockup
          inverse
          priority
          subtitle="Agent wallet control plane"
          markClassName="size-11 rounded-[14px]"
        />

        <div className="max-w-xl py-16">
          <p className="mb-4 text-sm font-semibold tracking-wide text-indigo-300 uppercase">
            Supervised autonomy
          </p>
          <h1 className="text-balance text-4xl font-semibold tracking-tight xl:text-5xl">
            Your agents can propose. You stay in control.
          </h1>
          <p className="mt-5 max-w-lg text-lg leading-8 text-zinc-300">
            Connect AI agents, assign payment methods, set precise approval rules, and keep every
            purchase attributable.
          </p>
          <ul className="mt-10 grid gap-4 text-sm text-zinc-300 sm:grid-cols-2">
            <Feature icon={Bot}>Verified agent pairing</Feature>
            <Feature icon={ShieldCheck}>Per-agent approval rules</Feature>
            <Feature icon={CheckCircle2}>Agent and card attribution</Feature>
            <Feature icon={WalletCards}>Recurring commitment tracking</Feature>
          </ul>
        </div>

        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <ShieldCheck className="size-3.5" />
          Sandbox and provider-tokenized references only
        </div>
      </section>
      <section className="flex min-h-svh items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-sm">{children}</div>
      </section>
    </main>
  );
}

function Feature({ icon: Icon, children }: { icon: typeof Bot; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-3">
      <span className="flex size-8 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-indigo-300">
        <Icon className="size-4" />
      </span>
      {children}
    </li>
  );
}
