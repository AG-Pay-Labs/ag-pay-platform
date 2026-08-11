# AG Pay web

The human management interface for the AG Pay agent-wallet prototype. It is a
Next.js App Router application built with TypeScript, Tailwind CSS, shadcn,
Radix primitives, and TanStack Query.

Implemented areas:

- registration, login, sign-out, and guarded application routes;
- workspace overview and first-run setup guidance;
- agent pairing, connection health, revocation, and card assignment;
- personal/business billing profiles with safe sandbox payment references;
- purchase approval and cancellation queues;
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

Never add inputs for raw PAN, CVC, PIN, or 3-D Secure secrets. The current card
form accepts only opaque provider references and safe display metadata.
For managed checkout, approval queues the trusted AG Pay executor only when a
server-configured merchant adapter and compatible provider reference are
available. The UI follows that execution from queued or running through its
verified terminal outcome. Legacy approval instead authorizes the agent to
complete checkout externally and report the result. Merchant subscription
cancellation remains a separate provider operation in both flows.

## Checks

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

See the monorepo README and the base repository `docs/` directory for the full
local setup, API contract, architecture, and product constraints.
