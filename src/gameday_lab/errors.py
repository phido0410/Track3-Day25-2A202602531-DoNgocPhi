"""Part A1 — Error classification.

The single most important decision in a reliability layer is *what kind of
failure* you are looking at, because each kind needs a different reaction.

Tests: tests/test_errors_retry.py
"""

from __future__ import annotations

from enum import Enum

from gameday_lab.sim.contracts import ProviderError


class ErrorKind(str, Enum):
    RATE_LIMITED = "rate_limited"  # 429 + retry-after: our tenant is over its limit
    QUOTA_EXHAUSTED = "quota_exhausted"  # 429 insufficient_quota: no point retrying
    OVERLOADED = "overloaded"  # 529 / 503: provider is shedding load or down
    SERVER_ERROR = "server_error"  # 500 / 502 / 504
    TIMEOUT = "timeout"  # we gave up waiting (status 0, code client_timeout)
    CLIENT_ERROR = "client_error"  # 400 / 401 / 403 / 404 / 422: our bug or config


def classify(err: ProviderError) -> ErrorKind:
    """Map a provider error to an ErrorKind."""
    if err.status == 0 or err.status == 408 or err.code == "client_timeout":
        return ErrorKind.TIMEOUT
    if err.status == 429:
        if err.code == "insufficient_quota":
            return ErrorKind.QUOTA_EXHAUSTED
        return ErrorKind.RATE_LIMITED
    if err.status in (503, 529) or err.code in ("overloaded_error", "service_unavailable"):
        return ErrorKind.OVERLOADED
    if 500 <= err.status < 600:
        return ErrorKind.SERVER_ERROR
    return ErrorKind.CLIENT_ERROR


def is_retryable(kind: ErrorKind) -> bool:
    """May we try *the same provider* again for this call (after a delay)?"""
    return kind in (ErrorKind.RATE_LIMITED, ErrorKind.OVERLOADED, ErrorKind.SERVER_ERROR, ErrorKind.TIMEOUT)


def counts_toward_breaker(kind: ErrorKind) -> bool:
    """Does this error say something about the *health* of the provider?"""
    return kind in (ErrorKind.OVERLOADED, ErrorKind.SERVER_ERROR, ErrorKind.TIMEOUT)


def failover_helps(kind: ErrorKind) -> bool:
    """Would sending the same request to *another* provider help?"""
    return kind is not ErrorKind.CLIENT_ERROR

