"""One Go completion queue per Session; no worker thread per request."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
import json as jsonlib
import math
import os
from pathlib import Path
import re
import threading
from types import MappingProxyType
from urllib.parse import urlencode, urlsplit, urlunsplit, unquote

from ._native import check_process, get_native, result_error
from .exceptions import InvalidRequestError, NativeLibraryError, SessionClosedError
from .models import Headers, Profile, Response, header_name

_UNSET = object()
_MAX_TIMEOUT_MS = 9_223_372_036_854
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*\Z", re.ASCII)


def _timeout_ms(timeout):
    if timeout is None:
        return 0
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or timeout <= 0 or isinstance(timeout, float) and not math.isfinite(timeout)):
        raise InvalidRequestError("timeout must be a positive number of seconds or None")
    # Compare before multiplying or converting arbitrary-size integers to float.
    # Otherwise finite floats can overflow to inf, and math.isfinite(int) can
    # itself raise OverflowError for an integer outside the float range.
    if timeout > _MAX_TIMEOUT_MS / 1000:
        raise InvalidRequestError("timeout is too large")
    value = math.ceil(timeout * 1000)
    if value > _MAX_TIMEOUT_MS:
        raise InvalidRequestError("timeout is too large")
    return value


def _limit(name, value, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidRequestError(f"{name} must be an integer >= {minimum}")
    return value


def _cookies(values):
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise InvalidRequestError("cookies must be a mapping of strings")
    result = {}
    for name, value in values.items():
        header_name(name)  # HTTP token validation, without lowercasing cookie names.
        if not isinstance(value, str) or not _COOKIE_VALUE.fullmatch(value):
            raise InvalidRequestError("cookie values must contain only RFC 6265 cookie octets")
        result[name] = value
    return result


@dataclass
class _Pending:
    future: object
    loop: object
    url: str


def _set_result(pending, response=None, error=None):
    def deliver():
        if not pending.future.done():
            if error is not None:
                pending.future.set_exception(error)
            else:
                pending.future.set_result(response)

    if pending.loop is None:
        deliver()
    else:
        try:
            pending.loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            # Closing an event loop without closing its Session is a caller
            # lifecycle error. Native handles have already been released.
            pass


class _BaseSession:
    def __init__(
        self,
        *,
        profile,
        library_path=None,
        proxy=None,
        proxy_auth=None,
        verify=True,
        session_resumption=True,
        random_ja3=False,
        force_http1=False,
        decode_content=True,
        headers=None,
        cookies=None,
        timeout=30,
        connect_timeout=None,
        proxy_connect_timeout=None,
        tls_handshake_timeout=None,
        response_header_timeout=None,
        body_timeout=None,
        max_concurrent_requests=64,
        max_pending_requests=256,
        max_response_bytes=64 * 1024 * 1024,
        max_unprocessed_retries=0,
    ):
        self._pid = os.getpid()
        self._headers = Headers(headers)
        self._cookies = MappingProxyType(_cookies(cookies))
        self._timeout = _timeout_ms(timeout)
        for name, value in (("session_resumption", session_resumption), ("random_ja3", random_ja3),
                            ("force_http1", force_http1), ("decode_content", decode_content)):
            if not isinstance(value, bool):
                raise InvalidRequestError(f"{name} must be True or False")
        if not isinstance(profile, Profile):
            profile = Profile.from_dict(profile) if isinstance(profile, Mapping) else Profile.from_file(profile)
        self._profile = profile
        config = {
            "profile": profile.to_dict(),
            "disable_session_resumption": not session_resumption,
            "random_ja3": random_ja3,
            "force_http1": force_http1,
            "disable_content_decoding": not decode_content,
            "max_concurrent_requests": _limit("max_concurrent_requests", max_concurrent_requests, 1),
            "max_pending_requests": _limit("max_pending_requests", max_pending_requests, 0),
            "max_response_bytes": _limit("max_response_bytes", max_response_bytes, 1),
            "max_unprocessed_retries": _limit("max_unprocessed_retries", max_unprocessed_retries, -1),
        }
        for name, value in (("connect_timeout", connect_timeout),
                            ("proxy_connect_timeout", proxy_connect_timeout),
                            ("tls_handshake_timeout", tls_handshake_timeout),
                            ("response_header_timeout", response_header_timeout),
                            ("body_timeout", body_timeout)):
            if value is not None:
                try:
                    config[name + "_ms"] = _timeout_ms(value)
                except InvalidRequestError:
                    raise InvalidRequestError(f"{name} must be a positive number of seconds or None") from None
        secrets = []
        if proxy is not None:
            if not isinstance(proxy, str):
                raise InvalidRequestError("proxy must be a URL string")
            config["proxy_url"] = proxy
            try:
                parsed = urlsplit(proxy)
            except ValueError:
                # urllib errors for invalid Unicode hosts can include userinfo.
                raise InvalidRequestError("proxy must be a valid URL") from None
            secrets.extend(unquote(value) for value in (parsed.username, parsed.password) if value)
        if proxy_auth is not None:
            if not isinstance(proxy_auth, Sequence) or isinstance(proxy_auth, (str, bytes, bytearray)):
                raise InvalidRequestError("proxy_auth must be a (username, password) string pair")
            auth = tuple(proxy_auth)
            if len(auth) != 2 or not all(isinstance(part, str) for part in auth):
                raise InvalidRequestError("proxy_auth must be a (username, password) string pair")
            username, password = auth
            config["proxy_auth"] = {"username": username, "password": password}
            secrets.extend(part for part in (username, password) if part)
        self._secrets = tuple(sorted(set(secrets), key=len, reverse=True))
        if verify is False:
            config["insecure_skip_verify"] = True
        elif verify is not True:
            if not isinstance(verify, (str, os.PathLike)):
                raise InvalidRequestError("verify must be True, False, or a CA PEM file path")
            config["ca_pem"] = Path(verify).read_text(encoding="ascii")
        self._native = get_native(library_path)
        data = jsonlib.dumps(config, separators=(",", ":"), allow_nan=False).encode("utf-8")
        code, self._sid, payload = self._native.call("ruts_session_create", data, len(data))
        if code:
            raise result_error(code, payload, self._redact)
        try:
            info = jsonlib.loads(payload)
            self._profile_hash = info["profile_hash"]
            self._limitations = tuple(info.get("limitations") or ())
        except Exception:
            self._native.status("ruts_session_close", self._sid)
            raise NativeLibraryError("native engine returned invalid Session metadata") from None
        self._lock = threading.Lock()
        self._closed = False
        self._close_done = threading.Event()
        self._pending = {}
        self._loop = None
        self._dispatcher = threading.Thread(
            target=self._dispatch,
            name=f"requests-utls-completion-{self._sid}",
            daemon=True,
        )
        try:
            self._dispatcher.start()
        except BaseException:
            self._native.status("ruts_session_close", self._sid)
            raise

    @property
    def profile(self):
        return self._profile

    @property
    def headers(self):
        return self._headers

    @property
    def cookies(self):
        """Static defaults; responses never update this immutable mapping."""
        return self._cookies

    @property
    def profile_hash(self):
        return self._profile_hash

    @property
    def limitations(self):
        return self._limitations

    @property
    def closed(self):
        check_process(self._pid)
        with self._lock:
            return self._closed

    def __repr__(self):
        return f"<{type(self).__name__}>"

    def _redact(self, message):
        for secret in self._secrets:
            message = message.replace(secret, "<redacted>")
        return message

    def _prepare(self, method, url, *, headers=None, headers_order=None, cookies=None, params=None, data=None, json=_UNSET, timeout=_UNSET, allow_redirects=False):
        check_process(self._pid)
        if not isinstance(allow_redirects, bool):
            raise InvalidRequestError("allow_redirects must be True or False")
        if allow_redirects:
            raise InvalidRequestError("redirect following is not implemented; use allow_redirects=False")
        if not isinstance(method, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method):
            raise InvalidRequestError("method must be an HTTP token")
        if not isinstance(url, str):
            raise InvalidRequestError("url must be a string")
        try:
            parsed = urlsplit(url)
        except ValueError:
            # Invalid Unicode authority errors can contain rejected userinfo.
            raise InvalidRequestError("url must be a valid URL") from None
        if parsed.username is not None or parsed.password is not None:
            raise InvalidRequestError("origin URL credentials are unsupported; use an explicit Authorization header")
        if params is not None:
            try:
                query = urlencode(list(params.items()) if isinstance(params, Mapping) else params, doseq=True)
            except (TypeError, ValueError):
                # Encoding errors can include user-supplied values. Do not put
                # those values into the public exception or its displayed chain.
                raise InvalidRequestError("params must be a mapping or a sequence of key/value pairs") from None
            url = urlunsplit(parsed._replace(query="&".join(value for value in (parsed.query, query) if value)))
        # Only local immutable snapshots reach the binding. No Session state is
        # modified while preparing a request.
        requested = Headers(headers)
        names = set(requested)
        # Preserve spelling through the ABI: Go selects HTTP/1 or HTTP/2 and
        # lowercases only HTTP/2 fields, including when ALPN falls back to H1.
        fields = [(name, value) for name, value in self._headers.raw_items() if name.lower() not in names]
        fields.extend(requested.raw_items())
        names.update(self._headers)
        request_cookies = dict(self._cookies)
        request_cookies.update(_cookies(cookies))
        if request_cookies:
            if "cookie" in names:
                raise InvalidRequestError("use either a Cookie header or cookies defaults/argument")
            fields.append(("Cookie", "; ".join(f"{name}={value}" for name, value in request_cookies.items())))
        if json is not _UNSET and data is not None:
            raise InvalidRequestError("data and json cannot be used together")
        if json is not _UNSET:
            try:
                body = jsonlib.dumps(json, separators=(",", ":"), allow_nan=False).encode("utf-8")
            except (TypeError, ValueError):
                raise InvalidRequestError("json must contain serializable JSON values") from None
            if "content-type" not in names:
                fields.append(("Content-Type", "application/json"))
        elif data is None:
            body = b""
        elif isinstance(data, str):
            try:
                body = data.encode("utf-8")
            except UnicodeEncodeError:
                raise InvalidRequestError("data must be valid UTF-8 text") from None
        elif isinstance(data, (bytes, bytearray, memoryview)):
            body = bytes(data)
        elif isinstance(data, Mapping):
            try:
                body = urlencode(list(data.items()), doseq=True).encode("ascii")
            except (TypeError, ValueError):
                raise InvalidRequestError("data must contain valid form values") from None
            if "content-type" not in names:
                fields.append(("Content-Type", "application/x-www-form-urlencoded"))
        else:
            raise InvalidRequestError("data must be bytes, str, or a form mapping")
        if isinstance(headers_order, (str, bytes, bytearray, Mapping, set, frozenset)):
            raise InvalidRequestError("headers_order must be a sequence of header names")
        try:
            order_source = iter(headers_order) if headers_order is not None else iter(())
        except TypeError:
            raise InvalidRequestError("headers_order must be a sequence of header names") from None
        order = [header_name(name) for name in order_source]
        metadata = {
            "method": method.upper(),
            "url": url,
            "headers": [{"name": name, "value": value} for name, value in fields],
            "headers_order": order,
            "timeout_ms": self._timeout if timeout is _UNSET else _timeout_ms(timeout),
        }
        encoded = jsonlib.dumps(metadata, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return encoded, body, url

    def _submit(self, metadata, body, url, loop=None):
        check_process(self._pid)
        future = Future() if loop is None else loop.create_future()
        # submit copies buffers before returning. Pairing submit+registration
        # under this lock prevents a very fast completion beating registration.
        with self._lock:
            if self._closed:
                raise SessionClosedError("Session is closed")
            code, rid, payload = self._native.call(
                "ruts_request_submit", self._sid, metadata, len(metadata), body, len(body)
            )
            if code:
                raise result_error(code, payload, self._redact)
            self._pending[rid] = _Pending(future, loop, url)
        return rid, future

    def _dispatch(self):
        try:
            while True:
                with self._lock:
                    if self._closed:
                        return
                code, rid, payload = self._native.call("ruts_session_poll", self._sid, 100)
                if code == 1:
                    continue
                if rid == 0:
                    self._fail_pending(result_error(code, payload, self._redact))
                    return
                response = None
                error = None
                try:
                    if code:
                        error = result_error(code, payload, self._redact)
                    else:
                        info = jsonlib.loads(payload)
                        body_code, _, body = self._native.call("ruts_request_body", rid)
                        if body_code:
                            error = result_error(body_code, body, self._redact)
                        else:
                            response = (info, body)
                except Exception:
                    error = NativeLibraryError("native engine returned an invalid completion")
                finally:
                    self._native.status("ruts_request_release", rid)
                with self._lock:
                    pending = self._pending.pop(rid, None)
                if pending is not None:
                    if response is not None:
                        info, body = response
                        try:
                            response = Response(
                                status_code=info["status_code"],
                                headers=Headers((field["name"], field["value"]) for field in info["headers"]),
                                content=body,
                                url=pending.url,
                                protocol=info.get("protocol", "HTTP/2.0"),
                                decoded=info.get("decoded", False),
                            )
                        except Exception:
                            response = None
                            error = NativeLibraryError("native engine returned invalid response metadata")
                    _set_result(pending, response, error)
        except Exception:
            self._fail_pending(NativeLibraryError("native completion dispatcher failed"))

    def _fail_pending(self, error):
        # A dispatcher must not wait for another closer: that closer can be
        # waiting for this dispatcher to return from poll and terminate.
        self._shutdown(error, wait=False)

    def _shutdown(self, error, *, wait):
        with self._lock:
            owner = not self._closed
            if owner:
                self._closed = True
                pending, self._pending = self._pending, {}
            else:
                pending = {}
        if owner:
            try:
                for entry in pending.values():
                    _set_result(entry, error=type(error)(str(error)))
                self._native.status("ruts_session_close", self._sid)
            finally:
                self._close_done.set()
        elif wait:
            self._close_done.wait()

    def _cancel(self, rid):
        check_process(self._pid)
        with self._lock:
            pending = self._pending.pop(rid, None)
        if pending is not None:
            self._native.status("ruts_request_cancel", rid)
            self._native.status("ruts_request_release", rid)

    def _close(self):
        check_process(self._pid)
        self._shutdown(SessionClosedError("Session was closed"), wait=True)
        # Never join or invoke native close while holding the binding lock.
        if threading.current_thread() is not self._dispatcher:
            self._dispatcher.join()


class Session(_BaseSession):
    """A thread-safe Session with immutable defaults and request-local ordering."""

    def request(self, method, url, **kwargs):
        metadata, body, url = self._prepare(method, url, **kwargs)
        rid, future = self._submit(metadata, body, url)
        try:
            return future.result()
        except BaseException:
            self._cancel(rid)
            raise

    def close(self):
        self._close()

    def __enter__(self):
        if self.closed:
            raise SessionClosedError("Session is closed")
        return self

    def __exit__(self, *exc):
        self.close()

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)

    def options(self, url, **kwargs):
        return self.request("OPTIONS", url, **kwargs)


class AsyncSession(_BaseSession):
    """Async API with one completion thread, bound to its first event loop."""

    def _current_loop(self):
        check_process(self._pid)
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._loop is None:
                self._loop = loop
            elif self._loop is not loop:
                raise InvalidRequestError("AsyncSession must be used on one event loop")
        return loop

    async def request(self, method, url, **kwargs):
        loop = self._current_loop()
        metadata, body, url = self._prepare(method, url, **kwargs)
        rid, future = self._submit(metadata, body, url, loop)
        try:
            return await future
        except BaseException:
            self._cancel(rid)
            raise

    async def aclose(self):
        self._current_loop()
        # Shutdown alone can wait for the dispatcher. Requests themselves never
        # consume an executor thread. Shield ensures cancellation still closes.
        await asyncio.shield(asyncio.to_thread(self._close))

    async def close(self):
        await self.aclose()

    async def __aenter__(self):
        self._current_loop()
        if self.closed:
            raise SessionClosedError("Session is closed")
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)

    async def put(self, url, **kwargs):
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url, **kwargs):
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url, **kwargs):
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url, **kwargs):
        return await self.request("OPTIONS", url, **kwargs)
