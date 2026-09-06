"""Immutable profile and ordered header snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Iterator
from dataclasses import dataclass, field
from email.message import Message
import json
from pathlib import Path
import re

from .exceptions import HTTPError, InvalidRequestError

_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9a-z-]+\Z", re.ASCII)


def header_name(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidRequestError("header names must be strings")
    value = value.lower()
    if not _HEADER_NAME.fullmatch(value):
        raise InvalidRequestError("headers require regular HTTP field names; pseudo headers belong to the profile")
    return value


@dataclass(frozen=True, slots=True, init=False)
class Headers(Mapping[str, str]):
    """Immutable, case-insensitive lookup with exact ordered duplicate fields.

    ``headers[name]`` joins values with ``, `` for mapping compatibility. Use
    ``get_list`` for fields such as Set-Cookie which must remain separate.
    """

    _items: tuple[tuple[str, str], ...] = field(repr=False)

    def __init__(self, values=None):
        if values is None:
            items = ()
        elif isinstance(values, Headers):
            items = values._items
        else:
            source = values.items() if isinstance(values, Mapping) else values
            items = []
            for name, value in source:
                name = header_name(name)
                if not isinstance(value, str):
                    raise InvalidRequestError("header values must be strings")
                if any(ord(c) < 32 and c != "\t" or ord(c) == 127 for c in value):
                    raise InvalidRequestError("header values cannot contain control characters")
                items.append((name, value))
            items = tuple(items)
        object.__setattr__(self, "_items", items)

    def __getitem__(self, key: str) -> str:
        values = self.get_list(key)
        if not values:
            raise KeyError(key)
        return ", ".join(values)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.fromkeys(name for name, _ in self._items))

    def __len__(self) -> int:
        return len(set(name for name, _ in self._items))

    def get_list(self, name: str) -> list[str]:
        return [value for key, value in self._items if key == name.lower()]

    def multi_items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def __repr__(self):
        return f"Headers({len(self._items)} fields)"


@dataclass(frozen=True, slots=True, init=False)
class Profile:
    """JSON snapshot; the native engine validates semantics during Session creation."""

    _json: bytes = field(repr=False)

    def __init__(self, value: Mapping):
        if not isinstance(value, Mapping):
            raise InvalidRequestError("profile must be a JSON object")
        try:
            encoded = json.dumps(dict(value), separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError("profile must contain JSON values") from exc
        object.__setattr__(self, "_json", encoded)

    @classmethod
    def from_dict(cls, value: Mapping) -> Profile:
        return cls(value)

    @classmethod
    def from_file(cls, path) -> Profile:
        return cls(json.loads(Path(path).read_bytes()))

    @classmethod
    def builtin(cls, name: str) -> Profile:
        """Load a profile shipped in the installed wheel, without network access."""
        from importlib.resources import files

        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise InvalidRequestError("builtin profile name must contain only letters, digits, underscores or hyphens")
        resource = files("requests_utls").joinpath("profiles", name + ".json")
        try:
            data = resource.read_bytes()
        except FileNotFoundError:
            raise InvalidRequestError(f"bundled profile {name!r} is unavailable in this installation") from None
        return cls(json.loads(data))

    @classmethod
    def from_peet(cls, capture, *, allow_opaque=False, library_path=None) -> Profile:
        """Import one tls.peet.ws capture via the independently installed engine."""
        from ._native import get_native, result_error

        if isinstance(capture, (str, Path)):
            data = Path(capture).read_bytes()
        elif isinstance(capture, (bytes, bytearray)):
            data = bytes(capture)
        else:
            data = json.dumps(capture, allow_nan=False).encode("utf-8")
        native = get_native(library_path)
        code, _, payload = native.call("ruts_profile_import", data, len(data), int(allow_opaque))
        if code:
            raise result_error(code, payload)
        return cls(json.loads(payload))

    def to_dict(self) -> dict:
        return json.loads(self._json)

    def __repr__(self):
        return "Profile(immutable JSON snapshot)"


@dataclass(frozen=True, slots=True)
class Response:
    status_code: int
    headers: Headers
    content: bytes = field(repr=False)
    url: str = field(repr=False)
    protocol: str = "HTTP/2.0"

    @property
    def encoding(self) -> str:
        message = Message()
        message["content-type"] = self.headers.get("content-type", "")
        return message.get_content_charset() or "utf-8"

    @property
    def text(self) -> str:
        try:
            return self.content.decode(self.encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self, **kwargs):
        return json.loads(self.content, **kwargs)

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}", response=self)
