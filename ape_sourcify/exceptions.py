from typing import Any

from ape.exceptions import ApeException


class SourcifyError(ApeException):
    """Base error for the ape-sourcify plugin."""


class SourcifyAPIError(SourcifyError):
    """An HTTP or response-format error returned by Sourcify."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        custom_code: str | None = None,
        error_id: str | None = None,
        response_data: Any = None,
    ):
        self.status_code = status_code
        self.custom_code = custom_code
        self.error_id = error_id
        self.response_data = response_data

        details = []
        if status_code is not None:
            details.append(f"HTTP {status_code}")
        if custom_code:
            details.append(custom_code)

        prefix = f"Sourcify API error ({', '.join(details)})" if details else "Sourcify API error"
        suffix = f" [error id: {error_id}]" if error_id else ""
        super().__init__(f"{prefix}: {message}{suffix}")


class SourcifyResponseError(SourcifyAPIError):
    """Sourcify returned a success response with an invalid JSON shape."""


class SourcifyTimeoutError(SourcifyError):
    """A Sourcify request or verification job timed out."""


class ContractAlreadyVerifiedError(SourcifyAPIError):
    """The requested contract is already verified by Sourcify."""


class ContractVerificationError(SourcifyError):
    """Sourcify could not verify a contract."""

    def __init__(
        self,
        message: str,
        *,
        custom_code: str | None = None,
        error_id: str | None = None,
        error_data: Any = None,
    ):
        self.custom_code = custom_code
        self.error_id = error_id
        self.error_data = error_data
        suffix = f" [error id: {error_id}]" if error_id else ""
        prefix = f"{custom_code}: " if custom_code else ""
        super().__init__(f"{prefix}{message}{suffix}")
