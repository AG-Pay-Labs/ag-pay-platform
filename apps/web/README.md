# AG Pay web

The human management interface for the AG Pay agent-wallet prototype. It is a
Next.js App Router application built with TypeScript, Tailwind CSS, shadcn,
Radix primitives, and TanStack Query.

Implemented areas:

- registration, login, sign-out, and guarded application routes;
- workspace overview and first-run setup guidance;
- agent pairing, connection health, revocation, and payment-method assignment;
- personal/business billing profiles with direct, sandbox, and provider-backed
  cards;
- card/wallet payment-method tabs, MetaMask ownership connection on Base
  Sepolia or Base, and network-aware wallet assignment;
- purchase approval and cancellation queues;
- x402 approval, MetaMask EIP-712 authorization, execution status, and safe
  protected-resource result handling;
- re-authenticated, ephemeral merchant-credential reveal;
- purchase audit history and local recurring-subscription tracking.

## Run locally

Start PostgreSQL, Redis, and the FastAPI application first. From this directory:

```bash
cp .env.example .env.local
pnpm install
pnpm dev
```

Open <http://localhost:3000>. `AGPAY_API_URL` is server-only and defaults to
`http://localhost:8000`.

## Security and payment boundary

Browser requests use same-origin Next.js routes. The auth BFF stores the
short-lived FastAPI JWT in an HttpOnly, `SameSite=Lax` cookie, while the human
API proxy forwards only an explicit method/path allowlist. Agent-facing routes
are intentionally unavailable through that proxy.

The local research-only direct-card form sends a PAN once to FastAPI through the
allowlisted BFF; it is never returned to the browser or placed in client storage
or query caches. CVC is collected only alongside approval for a compatible
managed direct checkout and is never part of the stored payment method. Provider
references and their display metadata remain supported alongside this local
mode. Never add inputs for PIN or 3-D Secure secrets.

MetaMask connection uses `personal_sign` only to prove ownership of the chosen
address and network; it does not move funds. For an approved x402 request, the
browser derives the exact EVM typed data only from the backend-frozen
`PAYMENT-REQUIRED` object and asks MetaMask to sign it. The signed payload goes
to FastAPI, which revalidates the wallet, network, amount, recipient, asset, and
signature before making the one allowed paid resource request. The browser
never receives or stores a private key. Base Sepolia is the safe default;
whether each network can currently execute x402 is returned by the backend
wallet configuration, and Base mainnet is disabled by default.

For managed checkout, approval queues the trusted AG Pay executor only when a
server-configured merchant adapter and compatible payment method are available.
The UI follows that execution from queued or running through its verified
terminal outcome. Legacy approval instead authorizes the agent to complete
checkout externally and report the result. Merchant subscription cancellation
remains a separate provider operation in both flows.

## Checks

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

See the monorepo README and the base repository `docs/` directory for the full
local setup, API contract, architecture, and product constraints.
