"""Public error types. No exception includes Session proxy configuration."""


class RequestError(Exception):
    """Base class for client and transport errors."""


class InvalidRequestError(RequestError, ValueError):
    pass


class SessionClosedError(RequestError):
    pass


class QueueFullError(RequestError):
    pass


class RequestCancelledError(RequestError):
    pass


class Timeout(RequestError, TimeoutError):
    pass


class ResponseTooLargeError(RequestError):
    pass


class TransportError(RequestError):
    pass


class NativeLibraryError(RequestError):
    pass


class ForkSafetyError(RequestError):
    pass


class HTTPError(RequestError):
    def __init__(self, message, *, response):
        super().__init__(message)
        self.response = response


_ERROR_TYPES = {
    2: SessionClosedError,
    3: InvalidRequestError,
    4: SessionClosedError,
    5: QueueFullError,
    6: RequestCancelledError,
    7: Timeout,
    8: ResponseTooLargeError,
    9: TransportError,
    10: NativeLibraryError,
}


def native_error(code, message):
    return _ERROR_TYPES.get(code, NativeLibraryError)(message)
