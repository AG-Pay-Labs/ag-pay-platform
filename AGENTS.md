# AG Pay Platform development guide

## Current scope

The implemented applications are the FastAPI backend in `apps/api` and the
Next.js management UI/BFF in `apps/web`. Keep the backend package on Python
3.12 and preserve the FastAPI + SQLAlchemy async + Alembic stack unless an
architectural change is explicitly approved. Preserve the web's Next.js App
Router + TypeScript + Tailwind CSS + shadcn structure unless a change is
explicitly approved.

## Required checks

From this repository, run:

```bash
make lint
make test
make api-migrate
make web-build
```

For schema changes, add an Alembic revision and verify `alembic upgrade head`,
`alembic downgrade base`, a second upgrade, and `alembic check` against
PostgreSQL.

## Backend invariants

- Hash human passwords with Argon2; hash opaque pairing/agent tokens; encrypt
  merchant passwords and expose them only through the re-authenticated reveal
  route.
- Human routes must filter by `owner_id`. Agent routes must take agent and owner
  identity only from the authenticated agent credential.
- Validate the agent/payment-method assignment when managed checkout is queued,
  immediately before submission, and again when success is recorded.
- Lock rows around financial state transitions and keep uniqueness constraints
  that prevent duplicate purchases/subscriptions.
- PostgreSQL is the source of truth. Redis publication is best effort until a
  transactional outbox is implemented; never claim it is a durable audit log.
  Checkout execution rows and the cursor-based agent event feed are durable
  PostgreSQL state.
- Update base-repository docs when the implemented API or state model changes.

## Web invariants

- Keep the FastAPI human bearer token in the server-managed HttpOnly cookie;
  never expose it through browser storage, rendered markup, client-visible
  environment variables, URLs, or logs.
- Route browser API calls through the method/path-allowlisted Next.js BFF. Do
  not add agent handshake, heartbeat, proposal, or purchase-completion routes
  to the human proxy.
- FastAPI remains authoritative for authentication, tenant scope, assignment,
  approval, and completion rules. Client-side guards are UX, not security.
- Keep auth, proxy, and merchant-credential responses non-cacheable. Requiring
  the current platform password for credential reveal must remain explicit.
- UI copy must distinguish legacy/manual proposals from configured managed
  checkout. Never claim an unconfirmed payment, refund, or merchant
  subscription cancellation; a managed checkout is complete only after the
  worker verifies the issuer authorization and records the purchase.
- Preserve accessible keyboard/focus behavior, loading/empty/error states, and
  responsive management journeys. Run a browser smoke test after material UI
  changes.
