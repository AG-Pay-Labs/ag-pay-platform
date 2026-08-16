<p align="center">
  <img src="apps/web/public/brand/agpay-mark.png" width="112" alt="AG Pay logo" />
</p>

# AG Pay Platform

The application monorepo for the AG Pay agent-wallet prototype:

- `apps/api`: FastAPI, SQLAlchemy async, Alembic, PostgreSQL, and Redis;
- `apps/web`: Next.js App Router, TypeScript, Tailwind CSS, shadcn, and TanStack Query.

PostgreSQL, Redis, and pgAdmin are managed by the parent base repository's
`docker-compose.yml`. New agents default to supervised autonomy: every purchase
proposal requires human review. Owners can configure a narrower per-agent
review rule. Proposals with an explicit per-request checkout adapter and checkout
URL can be queued for the managed checkout worker after approval; proposals
created by direct API clients or earlier integrations without both fields keep
the legacy approval-only/external-completion behavior. Managed
checkout is disabled by default. The production-shaped rail supports configured
merchant origins and Stripe Issuing card references but still requires the
documented launch gates; development/test also has an explicit, keyless
`stripe-hosted` Stripe Payments rail. A proposal supplies a concrete merchant-created
`checkout.stripe.com/c/pay/cs_test_...` URL. After approval, the worker opens that
exact URL, verifies its displayed product and total, fills Stripe's hosted page
through Browserbase with a public Stripe test-card fixture, and accepts success
only from the allowlisted `letyouragentspay.com` result page after that server
verifies the same Checkout Session. The worker neither creates a substitute
Checkout Session nor loads the merchant's Stripe credentials. This remains a
development-only Playground integration, not an arbitrary production-merchant rail.
Managed recurring checkout is intentionally rejected until merchant interval
verification is implemented.

For the complete multi-repository setup, including the AG Pay OpenClaw plugin
and playground, follow the [base quick start](https://github.com/AG-Pay-Labs/ag-pay#quick-start).

## Start local dependencies

From the parent `ag-pay` repository:

```bash
make init-env
make infra-check
make infra-up
make infra-ps
```

The local services bind PostgreSQL to `127.0.0.1:5432`, Redis to
`127.0.0.1:6379`, and pgAdmin to `http://127.0.0.1:5050` by default.

## Run the API

From this repository:

```bash
make api-install
test -f .env || cp .env.example .env
make api-migrate
make api-run
```

The API is available at `http://127.0.0.1:8000`; its interactive OpenAPI UI is
at `http://127.0.0.1:8000/docs`.

Managed checkout stays off with the example configuration. For a deliberate
Stripe test-mode run, configure checkout and Browserbase values in the untracked
`.env`, then start a third terminal:

```bash
make checkout-worker
```

Never put those secrets in the web or OpenClaw environments. The base
repository's `docs/managed-checkout.md` contains the adapter schema, security
boundary, and end-to-end sandbox test procedure.

For the hosted Playground success proof, set these values in the
untracked `.env` while keeping all existing application/database values:

```dotenv
ENVIRONMENT=development
CHECKOUT_ENABLED=true
CHECKOUT_DEMO_ENABLED=true
CHECKOUT_HOSTED_DEMO_ENABLED=true
BROWSERBASE_API_KEY=your_browserbase_key
BROWSERBASE_PROJECT_ID=your_browserbase_project_id
```

Run migrations, the API, web app, and `make checkout-worker` in separate
terminals. Create the human account and agent, then seed the safe methods:

```bash
make seed-checkout-demo SEED_USERNAME=your-login-email@example.com
```

The built-in adapter key is `stripe-hosted`; do not add or override it in
`CHECKOUT_ADAPTERS`. Each proposal must carry the complete merchant-created test
Checkout URL, for example `https://checkout.stripe.com/c/pay/cs_test_...#...`.
The Stripe origin alone is rejected, and the URL is never replaced by the worker.
The rail requires both demo flags and a development/test environment, but no
Stripe secret or publishable key. The older direct-adapter fixture remains
available through `make demo-merchant-run` when adapter development specifically
needs it; that legacy rail still requires `STRIPE_DEMO_SECRET_KEY=sk_test_...`.
The keyless rail can prove only a server-verified successful redirect. A decline,
3DS challenge, missing redirect, timeout, or conflicting result after submission
is persisted as `outcome_unknown`; it is never retried or claimed as a
failed/action-required Stripe outcome without provider evidence. For this
built-in hosted sandbox rail only, the authenticated owner can call
`POST /api/v1/cart-items/{cart_item_id}/checkout/reconcile`. The API sends the
frozen `cs_test_...` identifier to the fixed landing verification endpoint and
requires an exact paid-session, order-reference, normalized offer-name, amount,
and currency match. A verified result atomically records the Purchase and a new
durable success event. The reconciliation path never opens Browserbase or
submits payment again; missing or conflicting proof leaves the execution
unknown. Issuing, Stripe Link, and other merchant adapters retain their separate
manual/provider-specific reconciliation requirements.

The shared `.env` is only a local-development convenience. FastAPI uses a
settings model that has no Browserbase or Stripe secret fields; production must
inject those provider variables only into the checkout-worker process.

## Run the web app

Keep the API running and use another terminal from this repository:

```bash
make web-install
test -f apps/web/.env.local || cp apps/web/.env.example apps/web/.env.local
make web-run
```

Open `http://127.0.0.1:3000`. `AGPAY_API_URL` is read only by the Next.js
server and defaults to `http://localhost:8000`. Do not expose it as a
`NEXT_PUBLIC_` variable.

The management UI includes registration/login, an overview, compact
OpenClaw/Hermes runtime cards with detail sheets, realistic but safely masked
virtual cards, agent/card assignment, personal/business sandbox
payment-method entry, per-agent approval rules at `/rules`, queues for
proposed, approved, needs-attention, and historical items, ordered checkout
status timelines, active Browserbase session inspection, terminal outcome
toasts, merchant-credential reveal after password confirmation, purchases, and
locally tracked subscriptions.

To populate an existing account with repeatable local demo data:

```bash
make seed-demo SEED_USERNAME=your-existing-username
```

The seeder creates seven named OpenClaw/Hermes agents, assigns each one the same
fake Qonto Mastercard metadata ending in `2048`, and creates ten completed
purchases. The completed set includes four monthly USD service subscriptions
priced at $20 or more. It also creates three pending proposals and one approved
item waiting for external completion. It never inserts raw card credentials and
recognizes its own fake provider references when rerun, so it does not duplicate
the demo purchases.

## Web authentication boundary

The browser sends human requests to same-origin Next.js routes. The Next.js BFF
stores the short-lived FastAPI JWT in an HttpOnly, `SameSite=Lax` cookie, marks
it `Secure` in production, and adds it to allowlisted backend calls
server-side. Responses are non-cacheable, including credential reveal. Agent
handshake, heartbeat, proposal, and purchase-completion endpoints are not
proxied through the human web surface.

Web sign-out clears the cookie; FastAPI does not yet revoke the underlying JWT.
FastAPI remains responsible for authentication, tenant scoping, and all state
transition rules.

## Card and payment boundary

Never enter or add fields for raw PAN, CVC, PIN, or 3-D Secure secrets. The
prototype card dialog accepts opaque fake/sandbox or Stripe test-mode Issuing
references plus safe metadata such as brand, last four digits, expiry, and
billing details. The managed worker verifies an Issuing reference and its
provider-owned tenant binding immediately before use; the dialog itself does
not verify it. A production integration still requires provider-hosted card
onboarding.

Human approval, or an eligible server-side policy decision for a legacy
external-completion proposal, records the selected assigned method. A proposal
carrying an explicit checkout adapter and checkout URL always waits for human
approval, regardless of the agent's payment rule, and then creates one durable execution:
the worker validates the frozen merchant
configuration, assignment, and allowed origins; requires the merchant product
title, quantity, amount, and an explicit standalone ISO currency code to match
the approval; retrieves a
Stripe Issuing card only into process memory; fills it through deterministic
Browserbase automation with recording and logging disabled; and records success
only after matching issuer authorization. Raw PAN/CVC values are never stored,
returned, logged, or sent to an LLM. Agents learn terminal managed-checkout
outcomes through their tenant-scoped cursor event feed and cannot self-report
completion for those requests. The built-in development-only `stripe-hosted`
rail hardcodes Browserbase recording and session logging on because it uses
only public Stripe test-card fixtures. It records success only when the landing
server's pinned verification marker binds the approved amount and currency, the
receipt query, and the submitted fixed URL to the same `cs_test_...` session;
missing or conflicting evidence becomes `outcome_unknown`. Issuing and
configured-merchant sessions keep recording and logging off. A managed-checkout card must not be used by an
unrelated external flow while an execution is in progress; dedicated virtual
cards per execution are the safest deployment model. A card is quarantined
after an action-required or outcome-unknown execution and cannot be queued or
reused by another execution until that execution is reconciled. The owner route
above can release the built-in hosted sandbox fixture card only after exact
landing-server proof. Issuing, Stripe Link, and other unresolved executions must
still be reconciled through their provider-specific operator process; do not
reuse those cards while the outcome remains unknown.

The `never` review mode means eligible legacy/external-completion proposals do
not wait for a person when an active assigned method exists. It never
auto-approves executable managed checkout and is not an unlimited spending
permission.

## Checks

Run the combined backend/web checks from this repository:

```bash
make lint
make test
make api-migrate
make web-build
```

See the base repository's `docs/` directory for product scope, architecture,
API, payment/compliance boundaries, and operating guidance.
