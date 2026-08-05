# AG Platform

The application monorepo for the AG Pay agent-wallet prototype:

- `apps/api`: FastAPI, SQLAlchemy async, Alembic, PostgreSQL, and Redis;
- `apps/web`: Next.js App Router, TypeScript, Tailwind CSS, shadcn, and TanStack Query.

PostgreSQL, Redis, and pgAdmin are managed by the parent base repository's
`docker-compose.yml`. New agents default to supervised autonomy: every purchase
proposal requires human review. Owners can configure a narrower per-agent
review rule, but a rule-based approval only changes internal cart state. AG Pay
does not currently charge a card or execute a payment.

## Start local dependencies

From the parent `ag-pay` repository:

```bash
make init-env
make infra-up
```

The local services bind PostgreSQL to `127.0.0.1:5432`, Redis to
`127.0.0.1:6379`, and pgAdmin to `http://127.0.0.1:5050` by default.

## Run the API

From this repository:

```bash
make api-install
cp .env.example .env
make api-migrate
make api-run
```

The API is available at `http://127.0.0.1:8000`; its interactive OpenAPI UI is
at `http://127.0.0.1:8000/docs`.

## Run the web app

Keep the API running and use another terminal from this repository:

```bash
make web-install
cp apps/web/.env.example apps/web/.env.local
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
prototype card dialog accepts only fake/sandbox provider references and safe
metadata such as brand, last four digits, expiry, and billing details. A live
integration requires provider-hosted card collection and server-side metadata
verification.

Human approval, or an eligible server-side policy decision, records the
selected assigned method. The agent then performs an external/sandbox checkout
and reports successful completion; the platform does not charge or pay the
merchant. The `never` review mode means proposals do not wait for a person when
an active assigned method exists. It is not an unlimited real-card permission
or issuer authorization.

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
