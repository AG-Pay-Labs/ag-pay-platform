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

A separate `local_direct_card` rail is implemented only for local
development/test research. Its dedicated enrollment endpoint accepts a PAN,
stores only Fernet-encrypted PAN in an owner-scoped PostgreSQL credential row,
and derives the safe brand/last-four metadata returned by normal card APIs. It
never stores CVC: the human supplies CVC with the managed-checkout approval,
FastAPI stages it over an authenticated private Unix socket in worker-owned
memory with a short TTL, and the worker consumes it once immediately before
form fill. This rail requires an explicit operator-configured `direct` adapter
with `payment_form_strategy=browserbase_ai` and an order-reference selector.
Stagehand uses Browserbase `observe` only, before any PAN or CVC is loaded, to
map empty payment/billing controls; validated selectors are frozen, while
native JavaScript value setters and Playwright perform every fill and click.
The model never receives card values and never acts on the page. A visible
merchant success marker and order reference can create the local AG Pay
purchase record, but that merchant-observed result is not issuer authorization,
settlement proof, arbitrary-site support, or a production payment integration.

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

### Local direct-card research rail

This experiment is off by default and fails configuration outside
`development` or `test`. In the untracked platform `.env`, enable checkout and
provide distinct locally generated secrets:

```dotenv
ENVIRONMENT=development
CHECKOUT_ENABLED=true
LOCAL_DIRECT_CARD_ENABLED=true
DIRECT_CARD_ENCRYPTION_KEY=generate-a-dedicated-fernet-key
LOCAL_DIRECT_CARD_BROKER_TOKEN=generate-a-separate-random-token
LOCAL_DIRECT_CARD_SOCKET_PATH=/tmp/agpay-direct-card/cvc.sock
LOCAL_DIRECT_CARD_CVC_TTL_SECONDS=300
LOCAL_DIRECT_CARD_SOCKET_TIMEOUT_SECONDS=2
CHECKOUT_FORM_ANALYSIS_MODEL=google/gemini-2.5-flash
CHECKOUT_FORM_ANALYSIS_TIMEOUT_SECONDS=120
BROWSERBASE_API_KEY=your_browserbase_key
BROWSERBASE_PROJECT_ID=your_browserbase_project_id
CHECKOUT_ADAPTERS={"research_store":{"allowed_origins":["https://merchant.example.test"],"payment_origins":["https://merchant.example.test"],"checkout_mode":"direct","payment_form_strategy":"browserbase_ai","product_title_selector":"[data-test=product-title]","quantity_selector":"[data-test=quantity]","total_selector":"[data-test=total]","success_selector":"[data-test=success]","decline_selector":"[data-test=declined]","action_required_selector":"[data-test=action-required]","order_reference_selector":"[data-test=order-id]","receipt_url_selector":"[data-test=receipt]"}}
```

Generate the two values with the commands documented in `.env.example`; do not
reuse the JWT or merchant-credential key. Replace the illustrative public HTTPS
origin and selectors with a controlled research checkout. Run migrations, the
API, web app, and `make checkout-worker`; the worker owns a 0700-style private
parent directory and `0600` Unix socket, and therefore must be running before a
direct-card approval can stage CVC. Add the
card from **Cards**, assign it to an agent, and approve only a
new managed proposal carrying the exact configured adapter and checkout URL.
The approval dialog asks for CVC for that execution only. A missing/expired CVC
fails safely and requires a new proposal/approval; it is never recovered from
PostgreSQL, Redis, logs, or a file.

For a controlled no-charge browser test, start the built-in fixture in another
terminal:

```bash
make direct-card-fixture-run
```

It listens only on `127.0.0.1:8101`. Its `/checkout` page contains realistic
card, split-expiry, billing, and submit controls, but its submit handler prevents
navigation/network submission, clears the form, and reveals
`#research-success` plus a generated `#research-order-reference`. Its CSP also
sets `connect-src 'none'` and `form-action 'none'`; there is no processor and no
charge. Use only Luhn-valid synthetic/test card values—never a live card.

Browserbase cannot reach loopback, so expose only port `8101` through an
operator-chosen HTTPS tunnel and configure its exact origin (no wildcard):

```dotenv
CHECKOUT_ADAPTERS={"direct-card-fixture":{"allowed_origins":["https://YOUR-FIXTURE-HOST"],"payment_origins":["https://YOUR-FIXTURE-HOST"],"checkout_mode":"direct","payment_form_strategy":"browserbase_ai","product_title_selector":"#research-product-title","quantity_selector":"#research-quantity","total_selector":"#research-total","success_selector":"#research-success:not([hidden])","order_reference_selector":"#research-order-reference"}}
```

Use `direct-card-fixture` and
`https://YOUR-FIXTURE-HOST/checkout` in a new one-time proposal for exactly one
**AG Pay direct-card research fixture** at `EUR 25.00`, then approve with a
synthetic/test direct card and test CVC. A concrete public fixture is PAN
`4242 4242 4242 4242`, any future expiry such as `12/2034`, and CVC `123`;
these values are test data, never a live card. Do not tunnel the AG Pay API,
PostgreSQL, Redis, or OpenClaw. This change has not run a live Browserbase E2E:
that requires the operator's Browserbase credentials and chosen HTTPS tunnel.

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
virtual cards, agent/card assignment, personal/business sandbox/provider entry,
the feature-gated local direct-card enrollment and approval-time CVC flow,
per-agent approval rules at `/rules`, queues for
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

The normal provider-card dialog accepts opaque fake/sandbox, Link, or Stripe
Issuing references plus safe metadata. The separate, feature-gated local
research dialog accepts PAN but never CVC; PAN is encrypted before PostgreSQL
persistence and is excluded from every response and event. CVC is accepted only
with approval of a managed proposal using a stored local card and exists only
transiently in request handling and then worker memory. PIN and 3-D Secure
secrets are never accepted. Production still requires provider-hosted card
onboarding rather than this local storage path.

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
only after matching issuer authorization. For `local_direct_card`, Stagehand
observe maps empty form controls before secrets are loaded, deterministic
JavaScript/Playwright performs injection, encrypted PAN remains at rest, and CVC
is consumed from short-lived worker memory. Neither value is returned, logged,
published to Redis, or sent to an LLM. Local direct-card success reflects only
the configured merchant marker/order reference, not an issuer authorization.
Agents learn terminal managed-checkout
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
API, managed-checkout boundaries, and operating guidance.
