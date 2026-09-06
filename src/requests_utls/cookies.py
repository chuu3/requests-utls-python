"""Immutable response cookies; no shared storage or automatic cookie sending."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from http.cookiejar import http2time
from http.cookies import CookieError, SimpleCookie
import re
import time
from types import MappingProxyType
from urllib.parse import urlsplit

from .exceptions import CookieConflictError

_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z", re.ASCII)
_INTEGER = re.compile(r"-?[0-9]+\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class Cookie:
    """One Set-Cookie directive, including deletion/expiry instructions.

    ``expires`` is a Unix timestamp; a valid Max-Age takes precedence over
    Expires. ``attributes`` retains raw attribute values using lowercase keys.
    Values are excluded from representations.
    """

    name: str
    value: str = field(repr=False)
    domain: str
    path: str
    domain_specified: bool = False
    path_specified: bool = False
    secure: bool = False
    http_only: bool = False
    same_site: str | None = None
    expires: int | None = None
    max_age: int | None = None
    attributes: Mapping[str, str | None] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def host_only(self) -> bool:
        return not self.domain_specified

    def is_expired(self, now: float | None = None) -> bool:
        return self.expires is not None and self.expires <= (time.time() if now is None else now)


def _parse_cookie(line, host, default_path, now):
    # Commas are ordinary characters here, especially in Expires. Never split
    # combined header lookup values: this receives exactly one Set-Cookie field.
    parts = line.split(";")
    name, separator, value = parts[0].strip().partition("=")
    name = name.strip()
    value = value.strip()
    if not separator or not _TOKEN.fullmatch(name):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if value.startswith('"') != value.endswith('"') or value == '"':
        return None
    try:
        value = SimpleCookie().value_decode(value)[0]
    except (CookieError, ValueError):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    attributes = {}
    for part in parts[1:]:
        key, separator, attribute_value = part.strip().partition("=")
        key = key.strip().lower()
        if not _TOKEN.fullmatch(key):
            continue
        attributes[key] = attribute_value.strip() if separator else None
    domain_attribute = attributes.get("domain")
    domain = domain_attribute.lower().lstrip(".") if domain_attribute else host
    path_attribute = attributes.get("path")
    path = path_attribute if path_attribute and path_attribute.startswith("/") else default_path
    expires_attribute = attributes.get("expires")
    expires = None
    if expires_attribute:
        try:
            parsed = http2time(expires_attribute.strip('"'))
            if parsed is not None:
                expires = int(parsed)
        except (OverflowError, ValueError):
            pass
    max_age = None
    max_age_attribute = attributes.get("max-age")
    if max_age_attribute and _INTEGER.fullmatch(max_age_attribute):
        try:
            max_age = int(max_age_attribute)
            expires = now + max_age if max_age > 0 else 0
        except ValueError:
            pass  # Ignore an unreasonable integer rather than failing a response.
    return Cookie(
        name=name, value=value, domain=domain, path=path,
        domain_specified=bool(domain_attribute), path_specified=bool(path_attribute and path_attribute.startswith("/")),
        secure="secure" in attributes, http_only="httponly" in attributes,
        same_site=attributes.get("samesite"), expires=expires, max_age=max_age,
        attributes=attributes,
    )


@dataclass(frozen=True, slots=True, init=False)
class ResponseCookies:
    """Response-local cookie snapshot with explicit handling of name conflicts.

    Iteration yields Cookie records; items() includes every name/value pair.
    get() and get_dict() raise CookieConflictError for ambiguous names. Select
    domain/path, or inspect the records instead of flattening distinct scopes.
    This container does not implement browser acceptance or sending policy.
    """

    _cookies: tuple[Cookie, ...] = field(repr=False)

    def __init__(self, cookies=()):
        indexed = {}
        for cookie in cookies:
            if not isinstance(cookie, Cookie):
                raise TypeError("ResponseCookies requires Cookie records")
            # A later directive for the same cookie identity replaces the
            # earlier directive. Other domains and paths remain independent.
            indexed[(cookie.name, cookie.domain.lstrip(".").lower(), cookie.path)] = cookie
        object.__setattr__(self, "_cookies", tuple(indexed.values()))

    @classmethod
    def from_headers(cls, headers, url):
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path
        default_path = path.rsplit("/", 1)[0] if path.startswith("/") else ""
        default_path = default_path or "/"
        now = int(time.time())
        cookies = (_parse_cookie(line, host, default_path, now) for line in headers.get_list("set-cookie"))
        return cls(cookie for cookie in cookies if cookie is not None)

    def __iter__(self) -> Iterator[Cookie]:
        return iter(self._cookies)

    def __len__(self) -> int:
        return len(self._cookies)

    def __contains__(self, name) -> bool:
        return any(cookie.name == name for cookie in self._cookies)

    def __getitem__(self, name) -> str:
        missing = object()
        value = self.get(name, missing)
        if value is missing:
            raise KeyError(name)
        return value

    def _matching(self, domain, path):
        domain = domain.lstrip(".").lower() if domain is not None else None
        return (cookie for cookie in self if
                (domain is None or cookie.domain.lstrip(".").lower() == domain) and
                (path is None or cookie.path == path))

    def get(self, name, default=None, *, domain=None, path=None):
        matches = [cookie for cookie in self._matching(domain, path) if cookie.name == name]
        if len(matches) > 1:
            raise CookieConflictError("cookie name matches multiple domain/path scopes; specify domain and path")
        return matches[0].value if matches else default

    def get_dict(self, domain=None, path=None) -> dict[str, str]:
        result = {}
        for cookie in self._matching(domain, path):
            if cookie.name in result:
                raise CookieConflictError("cookie names match multiple domain/path scopes; specify domain and path")
            result[cookie.name] = cookie.value
        return result

    def items(self) -> list[tuple[str, str]]:
        return [(cookie.name, cookie.value) for cookie in self]

    def keys(self) -> list[str]:
        return [cookie.name for cookie in self]

    def values(self) -> list[str]:
        return [cookie.value for cookie in self]

    def __repr__(self):
        return f"ResponseCookies({len(self)} cookies)"
