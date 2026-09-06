"""Python client for the independently released requests-utls Go engine."""

from .exceptions import (
    ForkSafetyError, HTTPError, InvalidRequestError, NativeLibraryError,
    QueueFullError, RequestCancelledError, RequestError, ResponseTooLargeError,
    SessionClosedError, Timeout, TransportError,
)
from .models import Headers, Profile, Response
from .session import AsyncSession, Session

__version__ = "0.1.0"

__all__ = [
    "AsyncSession", "Session", "Headers", "Profile", "Response", "get", "post",
    "RequestError", "InvalidRequestError", "SessionClosedError", "QueueFullError",
    "RequestCancelledError", "Timeout", "ResponseTooLargeError", "TransportError",
    "NativeLibraryError", "ForkSafetyError", "HTTPError",
]


def get(url, *, profile, session_options=None, **kwargs):
    """One request in a new Session. Reuse Session explicitly for connection reuse."""
    with Session(profile=profile, **dict(session_options or {})) as session:
        return session.get(url, **kwargs)


def post(url, *, profile, session_options=None, **kwargs):
    with Session(profile=profile, **dict(session_options or {})) as session:
        return session.post(url, **kwargs)
