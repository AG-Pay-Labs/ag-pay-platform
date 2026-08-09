"""Trusted, deterministic checkout execution services.

This package is deliberately separate from the agent-facing API.  Browser and
card secrets are only handled inside the worker process and never become model
or API payloads.
"""

from ag_platform_api.services.checkout.worker import CheckoutWorker

__all__ = ["CheckoutWorker"]
