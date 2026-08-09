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
review rule. Proposals with an optional configured checkout adapter can be
queued for the managed checkout worker after approval; proposals without that
specification keep the legacy manual/external-completion behavior. Managed
checkout is disabled by default. Production execution supports configured
merchant origins and Stripe Issuing card references; development/test also has
an explicit Stripe Payments demo rail for Browserbase success, decline, and 3DS
scenarios without a real card. Managed recurring checkout is intentionally
rejected until merchant interval verification is implemented.

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
Stripe test-mode run, configure the platform-only checkout, Browserbase, Stripe,
and adapter values in the untracked `.env`, then start a third terminal:

```bash
make checkout-worker
```

Never put those secrets in the web or OpenClaw environments. The base
repository's `docs/managed-checkout.md` contains the adapter schema, security
boundary, and end-to-end sandbox test procedure.
For the visual decline demo, run `make demo-merchant-run` and
`make seed-checkout-demo SEED_USERNAME=...`; follow the literal guide in that
document. The demo rail requires `CHECKOUT_DEMO_ENABLED=true` and an `sk_test_`
key and cannot activate outside development/test queueing.
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
proposed, approved, and historical items, merchant-credential reveal after
password confirmation, purchases, and locally tracked subscriptions.

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
carrying a configured checkout adapter always waits for explicit human approval,
regardless of the agent's payment rule, and then creates one durable execution:
the worker validates the frozen merchant
configuration, assignment, and allowed origins; requires the merchant product
title, quantity, amount, and an explicit standalone ISO currency code to match
the approval; retrieves a
Stripe Issuing card only into process memory; fills it through deterministic
Browserbase automation with recording and logging disabled; and records success
only after matching issuer authorization. Raw PAN/CVC values are never stored,
returned, logged, or sent to an LLM. Agents learn terminal managed-checkout
outcomes through their tenant-scoped cursor event feed and cannot self-report
completion for those requests. A managed-checkout card must not be used by an
unrelated external flow while an execution is in progress; dedicated virtual
cards per execution are the safest deployment model. A card is quarantined
after an action-required or outcome-unknown execution and cannot be queued or
reused by another execution in this MVP; an operator must reconcile it and use
a distinct card.

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
