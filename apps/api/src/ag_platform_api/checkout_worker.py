import asyncio
import json
import logging
import signal
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from ag_platform_api.core.config import CheckoutWorkerSettings, get_worker_settings
from ag_platform_api.db.session import SessionFactory, engine
from ag_platform_api.services.checkout.browserbase import BrowserbaseCheckout, BrowserbaseGateway
from ag_platform_api.services.checkout.cvc_broker import (
    DirectCardCvcStore,
    LocalDirectCardCvcBroker,
)
from ag_platform_api.services.checkout.direct_card import LocalDirectCardGateway
from ag_platform_api.services.checkout.form_mapping import StagehandCheckoutFormMapper
from ag_platform_api.services.checkout.repository import SqlAlchemyCheckoutRepository
from ag_platform_api.services.checkout.stripe_issuing import StripeIssuingGateway
from ag_platform_api.services.checkout.stripe_link import StripeLinkGateway
from ag_platform_api.services.checkout.stripe_payments_demo import (
    StripePaymentsDemoGateway,
    StripeTestCardFixtures,
)
from ag_platform_api.services.checkout.worker import CheckoutWorker


class CheckoutRedisPublisher:
    """Best-effort publication without logging broker exceptions or payload internals."""

    def __init__(self, redis: Redis, stream: str = "agpay:domain-events") -> None:
        self._redis = redis
        self._stream = stream

    async def publish(self, event_type: str, payload: dict[str, object]) -> bool:
        envelope = {
            "type": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload, default=str, separators=(",", ":")),
        }
        try:
            await self._redis.xadd(self._stream, envelope, maxlen=10_000, approximate=True)
        except RedisError:
            return False
        return True


def build_worker(
    settings: CheckoutWorkerSettings,
    redis: Redis,
    *,
    direct_card_cvcs: DirectCardCvcStore | None = None,
) -> tuple[
    CheckoutWorker,
    BrowserbaseGateway,
    StripeIssuingGateway | None,
    StripePaymentsDemoGateway | None,
]:
    if not settings.checkout_enabled:
        raise RuntimeError("Managed checkout is disabled.")
    if settings.browserbase_api_key is None or not settings.browserbase_project_id:
        raise RuntimeError("Browserbase worker configuration is incomplete.")
    if (
        settings.stripe_secret_key is None
        and not settings.checkout_demo_enabled
        and not settings.stripe_link_enabled
        and not settings.local_direct_card_enabled
    ):
        raise RuntimeError("No checkout payment provider is configured.")
    if settings.local_direct_card_enabled and direct_card_cvcs is None:
        raise RuntimeError("The local direct-card CVC broker is not running.")

    # Link validates its executable and auth directory synchronously; fail before
    # allocating any network clients so startup errors cannot leak resources.
    link = (
        StripeLinkGateway(
            cli_path=settings.stripe_link_cli_path,
            expected_cli_version=settings.stripe_link_cli_version,
            auth_directory=settings.stripe_link_auth_directory,
            test_mode=settings.stripe_link_test_mode,
            approval_timeout_seconds=settings.stripe_link_approval_timeout_seconds,
            cli_timeout_seconds=settings.stripe_link_cli_timeout_seconds,
        )
        if settings.stripe_link_enabled and settings.stripe_link_auth_directory is not None
        else None
    )
    browserbase = BrowserbaseGateway(
        api_key=settings.browserbase_api_key.get_secret_value(),
        project_id=settings.browserbase_project_id,
        region=settings.browserbase_region,
        api_url=settings.browserbase_api_url,
        session_timeout_seconds=(
            settings.checkout_result_timeout_seconds
            + (
                settings.checkout_form_analysis_timeout_seconds
                if settings.local_direct_card_enabled
                else 0
            )
            + 60
        ),
    )
    issuing = (
        StripeIssuingGateway(
            secret_key=settings.stripe_secret_key.get_secret_value(),
            api_url=settings.stripe_api_url,
        )
        if settings.stripe_secret_key is not None
        else None
    )
    demo = (
        StripePaymentsDemoGateway(
            secret_key=settings.stripe_demo_secret_key.get_secret_value(),
            api_url=settings.stripe_api_url,
        )
        if settings.checkout_demo_enabled and settings.stripe_demo_secret_key is not None
        else None
    )
    demo_cards = StripeTestCardFixtures() if settings.checkout_demo_enabled else None
    direct_cards = (
        LocalDirectCardGateway(
            SessionFactory,
            encryption_key=settings.direct_card_encryption_key.get_secret_value(),
        )
        if settings.local_direct_card_enabled and settings.direct_card_encryption_key is not None
        else None
    )
    form_mapper = (
        StagehandCheckoutFormMapper(
            browserbase_api_key=settings.browserbase_api_key.get_secret_value(),
            model_name=settings.checkout_form_analysis_model,
            timeout_seconds=settings.checkout_form_analysis_timeout_seconds,
        )
        if settings.local_direct_card_enabled
        else None
    )
    worker = CheckoutWorker(
        repository=SqlAlchemyCheckoutRepository(SessionFactory),
        browser=BrowserbaseCheckout(
            browserbase,
            result_timeout_seconds=settings.checkout_result_timeout_seconds,
            form_mapper=form_mapper,
        ),
        issuing=issuing,
        link=link,
        demo=demo,
        demo_cards=demo_cards,
        direct_cards=direct_cards,
        direct_card_cvcs=direct_card_cvcs,
        broker=CheckoutRedisPublisher(redis),
        lease_seconds=settings.checkout_lease_seconds,
        max_attempts=settings.checkout_max_attempts,
        poll_seconds=settings.checkout_worker_poll_seconds,
        authorization_timeout_seconds=settings.checkout_authorization_timeout_seconds,
        authorization_poll_seconds=settings.checkout_authorization_poll_seconds,
        demo_observation_seconds=settings.checkout_demo_observation_seconds,
    )
    return worker, browserbase, issuing, demo


async def run() -> None:
    log_format = "%(levelname)s %(name)s %(message)s"
    logging.basicConfig(level=logging.CRITICAL, format=log_format)
    worker_logger = logging.getLogger("ag_platform_api.services.checkout.worker")
    worker_logger.handlers.clear()
    worker_handler = logging.StreamHandler()
    worker_handler.setFormatter(logging.Formatter(log_format))
    worker_logger.addHandler(worker_handler)
    worker_logger.setLevel(logging.INFO)
    worker_logger.propagate = False
    settings = get_worker_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    browserbase: BrowserbaseGateway | None = None
    issuing: StripeIssuingGateway | None = None
    demo: StripePaymentsDemoGateway | None = None
    cvc_broker: LocalDirectCardCvcBroker | None = None
    try:
        if (
            settings.local_direct_card_enabled
            and settings.local_direct_card_broker_token is not None
        ):
            cvc_broker = LocalDirectCardCvcBroker(
                socket_path=settings.local_direct_card_socket_path,
                broker_token=settings.local_direct_card_broker_token.get_secret_value(),
                ttl_seconds=settings.local_direct_card_cvc_ttl_seconds,
                timeout_seconds=settings.local_direct_card_socket_timeout_seconds,
            )
            await cvc_broker.start()
        worker, browserbase, issuing, demo = build_worker(
            settings,
            redis,
            direct_card_cvcs=cvc_broker,
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows event loops
                pass
        await worker.run_forever(stop)
    finally:
        if browserbase is not None:
            await browserbase.close()
        if issuing is not None:
            await issuing.close()
        if demo is not None:
            await demo.close()
        if cvc_broker is not None:
            await cvc_broker.close()
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
