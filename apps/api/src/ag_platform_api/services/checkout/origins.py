import ipaddress
from urllib.parse import urljoin, urlsplit

from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode


def normalize_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise CheckoutError(CheckoutErrorCode.origin_blocked) from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise CheckoutError(CheckoutErrorCode.origin_blocked)
    hostname = parsed.hostname.rstrip(".").lower()
    reject_private_hostname(hostname)
    default_port = port in {None, 443}
    authority = hostname if default_port else f"{hostname}:{port}"
    return f"https://{authority}"


def validate_checkout_url(value: str, allowed_origins: tuple[str, ...]) -> str:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        raise CheckoutError(CheckoutErrorCode.origin_blocked) from None
    if not parsed.path:
        path = "/"
        value = parsed._replace(path=path).geturl()
    origin = normalize_origin(value)
    normalized_allowed = {normalize_origin(item) for item in allowed_origins}
    if origin not in normalized_allowed:
        raise CheckoutError(CheckoutErrorCode.origin_blocked)
    return value


def validate_receipt_url(
    value: str,
    *,
    base_url: str,
    allowed_origins: tuple[str, ...],
) -> str:
    if len(value) > 2048:
        raise CheckoutError(CheckoutErrorCode.origin_blocked)
    resolved = urljoin(base_url, value)
    validate_checkout_url(resolved, allowed_origins)
    return resolved


def reject_private_hostname(hostname: str) -> None:
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise CheckoutError(CheckoutErrorCode.origin_blocked)
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return
    if not address.is_global:
        raise CheckoutError(CheckoutErrorCode.origin_blocked)


def browserbase_allowed_domains(origins: tuple[str, ...]) -> list[str]:
    domains: set[str] = set()
    for origin in origins:
        normalized = normalize_origin(origin)
        hostname = urlsplit(normalized).hostname
        if hostname is None:  # pragma: no cover - normalize_origin guarantees this
            raise CheckoutError(CheckoutErrorCode.origin_blocked)
        domains.add(hostname)
    return sorted(domains)
