"""Domain-specific exceptions for OTTO API interactions."""

from typing import Any
import httpx


class OttoApiException(httpx.HTTPStatusError):
    """Base domain exception for OTTO API errors.

    Inherits from httpx.HTTPStatusError for full backward compatibility
    with callers catching HTTPStatusError, while providing domain-specific
    fields (endpoint, controller, error_details, status_code).
    """

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        endpoint: str,
        controller: str,
        error_details: Any = None,
    ):
        super().__init__(message, request=request, response=response)
        self.endpoint = endpoint
        self.controller = controller
        self.error_details = error_details
        self.status_code = response.status_code


class OttoNotFoundError(OttoApiException):
    """Raised when OTTO returns 404 Not Found."""
    pass


class OttoAuthenticationError(OttoApiException):
    """Raised when OTTO returns 401 Unauthorized or 403 Forbidden."""
    pass


class OttoRateLimitError(OttoApiException):
    """Raised when OTTO returns 429 Too Many Requests."""

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        endpoint: str,
        controller: str,
        error_details: Any = None,
        retry_after: float | None = None,
    ):
        super().__init__(
            message,
            request=request,
            response=response,
            endpoint=endpoint,
            controller=controller,
            error_details=error_details,
        )
        self.retry_after = retry_after
