import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from ape.utils import USER_AGENT
from pydantic import ValidationError
from requests import Response, Session

from ape_sourcify.exceptions import (
    ContractAlreadyVerifiedError,
    SourcifyAPIError,
    SourcifyResponseError,
    SourcifyTimeoutError,
)
from ape_sourcify.types import (
    ChainInfo,
    VerificationJob,
    VerificationSubmission,
    VerifiedContract,
)

_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class SourcifyClient:
    """Small synchronous client for the Sourcify v2 HTTP API."""

    def __init__(
        self,
        api_url: str = "https://sourcify.dev/server",
        *,
        timeout: float = 30.0,
        retries: int = 3,
        retry_backoff: float = 0.5,
        chain_cache_ttl: float = 3600.0,
        session: Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.chain_cache_ttl = chain_cache_ttl
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._chains: tuple[ChainInfo, ...] | None = None
        self._chains_cached_at = 0.0

    def get_chains(self, *, force_refresh: bool = False) -> tuple[ChainInfo, ...]:
        now = self._clock()
        cache_fresh = (
            self._chains is not None
            and not force_refresh
            and now - self._chains_cached_at <= self.chain_cache_ttl
        )
        if cache_fresh:
            return self._chains or ()

        data = self._request_json("GET", "chains")
        if not isinstance(data, list):
            raise SourcifyResponseError(
                "Expected the chains endpoint to return a JSON array.", response_data=data
            )

        try:
            chains = tuple(ChainInfo.model_validate(item) for item in data)
        except ValidationError as err:
            raise SourcifyResponseError(
                f"Invalid response from the chains endpoint: {err}", response_data=data
            ) from err

        self._chains = chains
        self._chains_cached_at = now
        return chains

    def get_supported_chain_ids(self, *, force_refresh: bool = False) -> frozenset[int]:
        return frozenset(
            chain.chain_id
            for chain in self.get_chains(force_refresh=force_refresh)
            if chain.supported
        )

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id in self.get_supported_chain_ids()

    def get_contract(
        self,
        chain_id: int,
        address: str,
        *,
        fields: str | Sequence[str] = ("abi", "compilation"),
    ) -> VerifiedContract | None:
        field_value = fields if isinstance(fields, str) else ",".join(fields)
        response = self._request(
            "GET",
            f"v2/contract/{chain_id}/{address}",
            params={"fields": field_value},
        )
        if response.status_code == 404:
            return None

        data = self._response_json(response)
        self._raise_for_response(response, data)
        try:
            return VerifiedContract.model_validate(data)
        except ValidationError as err:
            raise SourcifyResponseError(
                f"Invalid verified-contract response: {err}",
                status_code=response.status_code,
                response_data=data,
            ) from err

    def submit_verification(
        self,
        chain_id: int,
        address: str,
        *,
        std_json_input: Mapping[str, Any],
        compiler_version: str,
        contract_identifier: str,
        creation_transaction_hash: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "stdJsonInput": dict(std_json_input),
            "compilerVersion": compiler_version.removeprefix("v"),
            "contractIdentifier": contract_identifier,
        }
        if creation_transaction_hash:
            payload["creationTransactionHash"] = creation_transaction_hash.lower()

        response = self._request("POST", f"v2/verify/{chain_id}/{address}", json=payload)
        data = self._response_json(response)
        if response.status_code == 409 and isinstance(data, dict):
            raise ContractAlreadyVerifiedError(
                str(data.get("message") or "Contract is already verified."),
                status_code=response.status_code,
                custom_code=_optional_str(data.get("customCode")),
                error_id=_optional_str(data.get("errorId")),
                response_data=data,
            )

        self._raise_for_response(response, data)
        try:
            return VerificationSubmission.model_validate(data).verification_id
        except ValidationError as err:
            raise SourcifyResponseError(
                f"Invalid verification-submission response: {err}",
                status_code=response.status_code,
                response_data=data,
            ) from err

    def get_verification_job(self, verification_id: str) -> VerificationJob:
        data = self._request_json("GET", f"v2/verify/{verification_id}")
        try:
            return VerificationJob.model_validate(data)
        except ValidationError as err:
            raise SourcifyResponseError(
                f"Invalid verification-job response: {err}", response_data=data
            ) from err

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        data = self._response_json(response)
        self._raise_for_response(response, data)
        return data

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        url = f"{self.api_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **dict(kwargs.pop("headers", {}) or {}),
        }
        if "json" in kwargs:
            headers.setdefault("Content-Type", "application/json")

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.request(
                    method.upper(),
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    **kwargs,
                )
            except requests.Timeout as err:
                last_error = err
                if attempt >= self.retries:
                    raise SourcifyTimeoutError(
                        f"Sourcify request timed out after {self.retries + 1} attempt(s): "
                        f"{method.upper()} {url}"
                    ) from err

                self._sleep(self._retry_delay(attempt, None))
                continue
            except requests.ConnectionError as err:
                last_error = err
                if attempt >= self.retries:
                    raise SourcifyAPIError(
                        f"Unable to connect after {self.retries + 1} attempt(s): "
                        f"{method.upper()} {url}"
                    ) from err

                self._sleep(self._retry_delay(attempt, None))
                continue

            if not self._should_retry(response) or attempt >= self.retries:
                return response

            self._sleep(self._retry_delay(attempt, response))

        # The loop always returns or raises. This guard keeps type-checkers honest.
        raise SourcifyAPIError(f"Request failed: {last_error}")

    def _should_retry(self, response: Response) -> bool:
        if response.status_code not in _TRANSIENT_STATUS_CODES:
            return False

        # A duplicate job is not a rate-limit condition and retrying cannot recover its ID.
        if response.status_code == 429:
            try:
                data = response.json()
            except requests.JSONDecodeError:
                return True

            if (
                isinstance(data, dict)
                and data.get("customCode") == "duplicate_verification_request"
            ):
                return False

        return True

    def _retry_delay(self, attempt: int, response: Response | None) -> float:
        if response is not None and (retry_after := response.headers.get("Retry-After")):
            if retry_after.isdigit():
                return max(0.0, float(retry_after))

            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass

        return self.retry_backoff * (2**attempt)

    @staticmethod
    def _response_json(response: Response) -> Any:
        try:
            return response.json()
        except requests.JSONDecodeError as err:
            raise SourcifyResponseError(
                "Expected a JSON response.",
                status_code=response.status_code,
                response_data=response.text,
            ) from err

    @staticmethod
    def _raise_for_response(response: Response, data: Any) -> None:
        if 200 <= response.status_code < 300:
            return

        if isinstance(data, dict):
            message = str(data.get("message") or response.reason or "Request failed.")
            custom_code = _optional_str(data.get("customCode"))
            error_id = _optional_str(data.get("errorId"))
        else:
            message = response.reason or "Request failed."
            custom_code = None
            error_id = None

        raise SourcifyAPIError(
            message,
            status_code=response.status_code,
            custom_code=custom_code,
            error_id=error_id,
            response_data=data,
        )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
