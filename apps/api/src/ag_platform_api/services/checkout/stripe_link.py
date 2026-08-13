import asyncio
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.origins import normalize_origin
from ag_platform_api.services.checkout.types import ExpectedCardMetadata, IssuingCardSecret

LINK_PAYMENT_METHOD_PATTERN = re.compile(r"^csmrpd_[A-Za-z0-9]+$")
LINK_SPEND_REQUEST_PATTERN = re.compile(r"^lsrq_[A-Za-z0-9]+$")
LINK_ACTIVE_STATUSES = frozenset({"created", "pending_approval", "approved"})
LINK_TEST_CARD_NUMBER = "4000009990001984"
MAX_CLI_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class LinkSpendBinding:
    execution_id: UUID
    payment_method_id: str
    merchant_url: str
    amount_minor: int
    currency: str
    merchant_name: str
    context: str
    item_name: str
    item_quantity: int
    unit_amount_minor: int


@dataclass(frozen=True, slots=True)
class LinkSpendRequest:
    request_id: str
    status: str
    binding: LinkSpendBinding


class StripeLinkGateway:
    """Runs Link CLI outside model-facing processes and never returns raw CLI output."""

    def __init__(
        self,
        *,
        cli_path: str,
        expected_cli_version: str,
        auth_directory: Path,
        test_mode: bool,
        approval_timeout_seconds: int = 600,
        cli_timeout_seconds: int = 30,
    ) -> None:
        resolved_cli = shutil.which(cli_path)
        if resolved_cli is None and ("/" in cli_path or os.sep in cli_path):
            candidate = Path(cli_path).expanduser().resolve()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                resolved_cli = str(candidate)
        if resolved_cli is None:
            raise RuntimeError("Stripe Link CLI executable was not found.")
        directory = auth_directory.expanduser().resolve()
        try:
            directory_lstat = directory.lstat()
            directory_stat = directory.stat()
        except OSError:
            raise RuntimeError("Stripe Link auth directory is unavailable.") from None
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or stat.S_ISLNK(directory_lstat.st_mode)
            or stat.S_IMODE(directory_stat.st_mode) & 0o077
            or (hasattr(os, "getuid") and directory_stat.st_uid != os.getuid())
        ):
            raise RuntimeError("Stripe Link auth directory must exist with mode 0700.")
        self._cli_path = str(Path(resolved_cli).resolve())
        self._expected_cli_version = expected_cli_version
        self._version_checked = False
        self._version_lock = asyncio.Lock()
        self._auth_directory = directory
        self._test_mode = test_mode
        self._approval_timeout_seconds = approval_timeout_seconds
        self._cli_timeout_seconds = cli_timeout_seconds

    async def ensure_spend_request(
        self,
        *,
        owner_id: UUID,
        execution_id: UUID,
        payment_method_id: str,
        merchant_name: str,
        merchant_url: str,
        amount_minor: int,
        currency: str,
        context: str,
        existing_request_id: str | None,
        expected_card: ExpectedCardMetadata,
        item_name: str,
        item_quantity: int,
        unit_amount_minor: int,
    ) -> LinkSpendRequest:
        self._validate_inputs(
            payment_method_id=payment_method_id,
            merchant_url=merchant_url,
            amount_minor=amount_minor,
            currency=currency,
            context=context,
            item_quantity=item_quantity,
            unit_amount_minor=unit_amount_minor,
        )
        safe_item_name = self._safe_display(item_name)
        safe_merchant_name = self._safe_display(merchant_name)[:255]
        binding = LinkSpendBinding(
            execution_id=execution_id,
            payment_method_id=payment_method_id,
            merchant_url=merchant_url,
            amount_minor=amount_minor,
            currency=currency,
            merchant_name=safe_merchant_name,
            context=context,
            item_name=safe_item_name,
            item_quantity=item_quantity,
            unit_amount_minor=unit_amount_minor,
        )
        auth_file = self._auth_file(owner_id)
        await self._verify_payment_method(auth_file, payment_method_id, expected_card)
        if existing_request_id is not None:
            self._validate_request_id(existing_request_id)
            record = await self._retrieve_record(auth_file, existing_request_id)
            self._validate_exact_request(record, binding)
            return await self._request_approval_if_needed(auth_file, record, binding)

        records = await self._run_json(auth_file, "spend-request", "list", "--include-history")
        matching = [record for record in records if self._request_matches(record, binding)]
        execution_records = [
            record
            for record in records
            if isinstance(record.get("metadata"), Mapping)
            and record["metadata"].get("agpay_execution_id") == str(execution_id)
        ]
        if len(execution_records) > 1:
            raise CheckoutError(CheckoutErrorCode.card_reconciliation_required)
        if execution_records:
            if not matching:
                raise CheckoutError(CheckoutErrorCode.card_reconciliation_required)
            return await self._request_approval_if_needed(auth_file, execution_records[0], binding)

        arguments = [
            "spend-request",
            "create",
            "--payment-method-id",
            payment_method_id,
            "--merchant-name",
            safe_merchant_name,
            "--merchant-url",
            merchant_url,
            "--context",
            context,
            "--amount",
            str(amount_minor),
            "--currency",
            currency.lower(),
            "--line-item",
            (f"name:{safe_item_name},unit_amount:{unit_amount_minor},quantity:{item_quantity}"),
            "--total",
            f"type:total,display_text:Total,amount:{amount_minor}",
            "--metadata",
            f"agpay_execution_id:{execution_id}",
            "--request-approval",
        ]
        if self._test_mode:
            arguments.append("--test")
        created = self._one(await self._run_json(auth_file, *arguments))
        self._validate_exact_request(created, binding)
        return self._spend_request(created, binding)

    async def retrieve_card(
        self,
        *,
        owner_id: UUID,
        request: LinkSpendRequest,
        expected_billing_details: Mapping[str, object],
    ) -> IssuingCardSecret:
        request_id = request.request_id
        self._validate_request_id(request_id)
        auth_file = self._auth_file(owner_id)
        with tempfile.TemporaryDirectory(prefix="agpay-link-") as directory:
            credential_path = Path(directory) / "card.json"
            record = self._one(
                await self._run_json(
                    auth_file,
                    "spend-request",
                    "retrieve",
                    request_id,
                    "--include",
                    "card",
                    "--output-file",
                    str(credential_path),
                    retryable=True,
                )
            )
            self._validate_exact_request(record, request.binding)
            if record.get("status") != "approved":
                raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=True)
            payload = self._read_credential_file(credential_path)

        if payload.get("spend_request_id") != request_id:
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        card = payload.get("card")
        if not isinstance(card, Mapping):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        try:
            number = str(card["number"])
            cvc = str(card["cvc"])
            expiry_month = int(card["exp_month"])
            expiry_year = int(card["exp_year"])
        except (KeyError, TypeError, ValueError):
            raise CheckoutError(CheckoutErrorCode.card_unavailable) from None
        if (
            not number.isascii()
            or not number.isdigit()
            or not 12 <= len(number) <= 19
            or not cvc.isascii()
            or not cvc.isdigit()
            or len(cvc) not in {3, 4}
            or expiry_month not in range(1, 13)
            or expiry_year < 2020
            or (self._test_mode and number != LINK_TEST_CARD_NUMBER)
        ):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        self._validate_billing_address(card.get("billing_address"), expected_billing_details)
        secret = IssuingCardSecret(number, cvc, expiry_month, expiry_year)
        del payload, card, number, cvc
        return secret

    async def wait_for_approval(self, *, owner_id: UUID, request: LinkSpendRequest) -> None:
        request_id = request.request_id
        self._validate_request_id(request_id)
        auth_file = self._auth_file(owner_id)
        deadline = asyncio.get_running_loop().time() + self._approval_timeout_seconds
        while True:
            record = self._one(
                await self._run_json(
                    auth_file,
                    "spend-request",
                    "retrieve",
                    request_id,
                    retryable=True,
                )
            )
            self._validate_exact_request(record, request.binding)
            status = record.get("status")
            if status == "approved":
                return
            if status not in {"created", "pending_approval"}:
                raise CheckoutError(CheckoutErrorCode.card_unavailable)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=True)
            await asyncio.sleep(min(2, remaining))

    async def report_outcome(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        merchant_url: str,
        outcome: str,
        tag: str | None = None,
    ) -> None:
        """Best effort only: reporting can never change the durable checkout result."""
        if outcome not in {"success", "blocked", "abandoned"}:
            return
        try:
            self._validate_request_id(request_id)
            domain = urlsplit(merchant_url).hostname
            if domain is None:
                return
            arguments = [
                "report",
                "--domain",
                domain,
                "--outcome",
                outcome,
                "--spend-request-id",
                request_id,
            ]
            if tag is not None:
                arguments.extend(("--tag", tag))
            await self._run_json(self._auth_file(owner_id), *arguments)
        except Exception:
            return

    async def _request_approval_if_needed(
        self,
        auth_file: Path,
        record: Mapping[str, object],
        binding: LinkSpendBinding,
    ) -> LinkSpendRequest:
        status = record.get("status")
        if status == "created":
            request_id = str(record.get("id", ""))
            record = self._one(
                await self._run_json(
                    auth_file,
                    "spend-request",
                    "request-approval",
                    request_id,
                )
            )
            # request-approval responses can omit status.
            return LinkSpendRequest(
                request_id,
                str(record.get("status", "pending_approval")),
                binding,
            )
        return self._spend_request(record, binding)

    async def _retrieve_record(self, auth_file: Path, request_id: str) -> Mapping[str, object]:
        return self._one(await self._run_json(auth_file, "spend-request", "retrieve", request_id))

    async def _verify_payment_method(
        self,
        auth_file: Path,
        payment_method_id: str,
        expected: ExpectedCardMetadata,
    ) -> None:
        methods = await self._run_json(auth_file, "payment-methods", "list")
        matching = [method for method in methods if method.get("id") == payment_method_id]
        if len(matching) != 1:
            raise CheckoutError(CheckoutErrorCode.card_reference_invalid)
        method = matching[0]
        details = method.get("card_details")
        capabilities = method.get("capabilities")
        agentic = (
            capabilities.get("agentic_payments") if isinstance(capabilities, Mapping) else None
        )
        if (
            not isinstance(details, Mapping)
            or method.get("type") != "card"
            or (isinstance(agentic, Mapping) and agentic.get("eligible") is not True)
            or (agentic is not None and not isinstance(agentic, Mapping))
        ):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        try:
            matches = (
                str(details["brand"]).casefold() == expected.brand.casefold()
                and str(details["last4"]) == expected.last4
                and int(details["exp_month"]) == expected.expiry_month
                and int(details["exp_year"]) == expected.expiry_year
            )
        except (KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            raise CheckoutError(CheckoutErrorCode.card_unavailable)

    async def _run_json(
        self,
        auth_file: Path,
        *arguments: str,
        timeout_seconds: int | None = None,
        retryable: bool = False,
    ) -> list[Mapping[str, object]]:
        await self._ensure_version()
        environment = self._subprocess_environment()
        command = (
            self._cli_path,
            *arguments,
            "--auth",
            str(auth_file),
            "--format",
            "json",
        )
        stdout = await self._run_process(
            command,
            environment,
            timeout_seconds or self._cli_timeout_seconds,
            retryable=retryable,
        )
        try:
            decoded = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=retryable) from None
        if isinstance(decoded, Mapping):
            return [decoded]
        if isinstance(decoded, list) and all(isinstance(item, Mapping) for item in decoded):
            return list(decoded)
        raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=retryable)

    async def _ensure_version(self) -> None:
        if self._version_checked:
            return
        async with self._version_lock:
            if self._version_checked:
                return
            output = await self._run_process(
                (self._cli_path, "--version"),
                self._subprocess_environment(),
                self._cli_timeout_seconds,
                retryable=False,
            )
            rendered = output.decode("utf-8", errors="strict").strip()
            if rendered not in {
                self._expected_cli_version,
                f"link-cli/{self._expected_cli_version}",
                f"link-cli {self._expected_cli_version}",
            }:
                raise CheckoutError(CheckoutErrorCode.provider_unsupported)
            self._version_checked = True

    @staticmethod
    def _subprocess_environment() -> dict[str, str]:
        environment = {
            key: value
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
            if (value := os.environ.get(key)) is not None
        }
        environment["NO_UPDATE_NOTIFIER"] = "1"
        return environment

    async def _run_process(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        timeout_seconds: int,
        *,
        retryable: bool,
    ) -> bytes:
        async def read_bounded_stdout(
            process: asyncio.subprocess.Process,
        ) -> bytes:
            if process.stdout is None:  # pragma: no cover - PIPE is configured below
                raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=retryable)
            try:
                stdout = await process.stdout.readexactly(MAX_CLI_OUTPUT_BYTES + 1)
            except asyncio.IncompleteReadError as error:
                stdout = error.partial
            if len(stdout) > MAX_CLI_OUTPUT_BYTES:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=retryable)
            await process.wait()
            return stdout

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
                limit=MAX_CLI_OUTPUT_BYTES,
            )
            stdout = await asyncio.wait_for(read_bounded_stdout(process), timeout=timeout_seconds)
        except (OSError, TimeoutError):
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=retryable) from None
        if process.returncode != 0 or not stdout:
            raise CheckoutError(CheckoutErrorCode.card_unavailable, retryable=retryable)
        return stdout

    def _auth_file(self, owner_id: UUID) -> Path:
        auth_file = self._auth_directory / f"{owner_id}.json"
        try:
            file_stat = auth_file.lstat()
        except OSError:
            raise CheckoutError(CheckoutErrorCode.card_unavailable) from None
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_ISLNK(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) & 0o077
            or (hasattr(os, "getuid") and file_stat.st_uid != os.getuid())
        ):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        return auth_file

    @staticmethod
    def _read_credential_file(path: Path) -> Mapping[str, object]:
        try:
            file_stat = path.lstat()
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or stat.S_ISLNK(file_stat.st_mode)
                or stat.S_IMODE(file_stat.st_mode) != 0o600
                or file_stat.st_size > MAX_CLI_OUTPUT_BYTES
            ):
                raise OSError
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                contents = os.read(descriptor, MAX_CLI_OUTPUT_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(contents) > MAX_CLI_OUTPUT_BYTES:
                raise OSError
            payload = json.loads(contents)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CheckoutError(CheckoutErrorCode.card_unavailable) from None
        if not isinstance(payload, Mapping):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        return payload

    @classmethod
    def _validate_exact_request(
        cls,
        record: Mapping[str, object],
        binding: LinkSpendBinding,
    ) -> None:
        if not cls._request_matches(record, binding):
            raise CheckoutError(CheckoutErrorCode.card_reconciliation_required)

    @classmethod
    def _request_matches(
        cls,
        record: Mapping[str, object],
        binding: LinkSpendBinding,
    ) -> bool:
        metadata = record.get("metadata")
        line_items = record.get("line_items")
        totals = record.get("totals")
        expected_line_item = {
            "name": binding.item_name,
            "unit_amount": binding.unit_amount_minor,
            "quantity": binding.item_quantity,
        }
        expected_total = {
            "type": "total",
            "display_text": "Total",
            "amount": binding.amount_minor,
        }
        return bool(
            isinstance(metadata, Mapping)
            and metadata.get("agpay_execution_id") == str(binding.execution_id)
            and record.get("payment_details") == binding.payment_method_id
            and record.get("merchant_url") == binding.merchant_url
            and record.get("merchant_name") == binding.merchant_name
            and record.get("context") == binding.context
            and line_items == [expected_line_item]
            and totals == [expected_total]
            and record.get("credential_type") in {None, "card"}
            and type(record.get("amount")) is int
            and record.get("amount") == binding.amount_minor
            and isinstance(record.get("currency"), str)
            and str(record["currency"]).upper() == binding.currency.upper()
            and isinstance(record.get("status"), str)
            and LINK_SPEND_REQUEST_PATTERN.fullmatch(str(record.get("id", "")))
        )

    @staticmethod
    def _validate_billing_address(provided: object, expected_details: Mapping[str, object]) -> None:
        if not isinstance(provided, Mapping):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        expected_address = expected_details.get("address")
        if not isinstance(expected_address, Mapping):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        expected = {
            "name": expected_details.get("full_name") or expected_details.get("contact_name"),
            "line1": expected_address.get("line1"),
            "line2": expected_address.get("line2"),
            "city": expected_address.get("city"),
            "state": expected_address.get("region"),
            "postal_code": expected_address.get("postal_code"),
            "country": expected_address.get("country"),
        }

        def normalize(value: object) -> str:
            return " ".join(str(value or "").split()).casefold()

        if any(normalize(provided.get(key)) != normalize(value) for key, value in expected.items()):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)

    @staticmethod
    def _validate_inputs(
        *,
        payment_method_id: str,
        merchant_url: str,
        amount_minor: int,
        currency: str,
        context: str,
        item_quantity: int,
        unit_amount_minor: int,
    ) -> None:
        parsed = urlsplit(merchant_url)
        try:
            normalized_origin = normalize_origin(merchant_url)
        except CheckoutError:
            raise CheckoutError(CheckoutErrorCode.card_reference_invalid) from None
        if (
            LINK_PAYMENT_METHOD_PATTERN.fullmatch(payment_method_id) is None
            or parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or normalized_origin != merchant_url.rstrip("/")
            or not 0 < amount_minor <= 500_000
            or item_quantity <= 0
            or unit_amount_minor <= 0
            or unit_amount_minor * item_quantity != amount_minor
            or re.fullmatch(r"[A-Z]{3}", currency) is None
            or len(context) < 100
            or len(context) > 500
        ):
            raise CheckoutError(CheckoutErrorCode.card_reference_invalid)

    @staticmethod
    def _safe_display(value: str) -> str:
        rendered = re.sub(r"[^A-Za-z0-9 ._()/&+\-]", " ", value)
        rendered = " ".join(rendered.split())[:200]
        return rendered or "Approved item"

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if LINK_SPEND_REQUEST_PATTERN.fullmatch(request_id) is None:
            raise CheckoutError(CheckoutErrorCode.card_reference_invalid)

    @classmethod
    def _spend_request(
        cls, record: Mapping[str, object], binding: LinkSpendBinding
    ) -> LinkSpendRequest:
        request_id = str(record.get("id", ""))
        cls._validate_request_id(request_id)
        status = str(record.get("status", ""))
        if status not in LINK_ACTIVE_STATUSES:
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        return LinkSpendRequest(request_id, status, binding)

    @staticmethod
    def _one(records: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
        if len(records) != 1:
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        return records[0]
