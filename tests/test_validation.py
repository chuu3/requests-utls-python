"""Invalid public inputs fail predictably without loading a native engine."""

import json
import math
import os
import traceback
from types import MappingProxyType

import pytest

from requests_utls import AsyncSession, Headers, InvalidRequestError, RequestError, Session
from requests_utls.session import _MAX_TIMEOUT_MS, _timeout_ms


@pytest.fixture
def no_native(monkeypatch):
    def unexpected_load(path):
        pytest.fail("invalid inputs must not load a native engine")

    monkeypatch.setattr("requests_utls.session.get_native", unexpected_load)


@pytest.mark.parametrize("session_class", [Session, AsyncSession])
@pytest.mark.parametrize("timeout", [1e308, 10**1000, -(10**1000)], ids=["large-float", "large-int", "large-negative-int"])
def test_extreme_timeouts_are_invalid_requests_before_native(no_native, session_class, timeout):
    with pytest.raises(InvalidRequestError) as raised:
        session_class(profile={}, timeout=timeout)
    assert isinstance(raised.value, RequestError)


def test_timeout_boundaries_preserve_millisecond_rounding():
    assert _timeout_ms(1e-300) == 1
    assert _timeout_ms(_MAX_TIMEOUT_MS // 1000) == (_MAX_TIMEOUT_MS // 1000) * 1000
    assert _timeout_ms(_MAX_TIMEOUT_MS / 1000) == _MAX_TIMEOUT_MS
    with pytest.raises(InvalidRequestError, match="too large"):
        _timeout_ms(math.nextafter(_MAX_TIMEOUT_MS / 1000, math.inf))


@pytest.mark.parametrize("session_class", [Session, AsyncSession])
@pytest.mark.parametrize("auth", [1, object(), "user", b"up", {}, {"u", "p"}, iter(("u", "p")), (), ("u",), ("u", "p", "extra"), ("u", 1)])
def test_proxy_auth_requires_a_string_pair(no_native, session_class, auth):
    with pytest.raises(InvalidRequestError, match="proxy_auth must be"):
        session_class(profile={}, proxy_auth=auth)


def test_proxy_auth_error_does_not_display_supplied_values(no_native):
    secret = "synthetic-auth-value"
    with pytest.raises(InvalidRequestError) as raised:
        Session(profile={}, proxy_auth=("test-user", secret, "extra"))
    assert secret not in "".join(traceback.format_exception(raised.value))
    assert "test-user" not in str(raised.value)


@pytest.mark.parametrize("auth", [("u", "p"), ["u", "p"]])
def test_proxy_auth_tuple_and_list_are_snapshotted(monkeypatch, auth):
    captured = {}

    class RecordedConfiguration(Exception):
        pass

    class NativeRecorder:
        def call(self, name, data, length):
            assert name == "ruts_session_create"
            captured.update(json.loads(data))
            raise RecordedConfiguration

    monkeypatch.setattr("requests_utls.session.get_native", lambda path: NativeRecorder())
    with pytest.raises(RecordedConfiguration):
        Session(profile={}, proxy_auth=auth)
    if isinstance(auth, list):
        auth.clear()
    assert captured["proxy_auth"] == {"username": "u", "password": "p"}


@pytest.mark.parametrize("fields", [1, object(), "xy", b"xy", [None], [1], [()], [("x",)], [("x", "v", "extra")], ["xy"], [{"x": "v", "y": "w"}]])
def test_malformed_header_shapes_are_invalid_requests(fields):
    with pytest.raises(InvalidRequestError):
        Headers(fields)


def test_headers_still_accept_iterators_and_ordered_duplicate_fields():
    fields = iter([("X-A", "1"), ("X-B", "2"), ("X-A", "3")])
    assert Headers(fields).raw_items() == [("X-A", "1"), ("X-B", "2"), ("X-A", "3")]


@pytest.fixture
def prepared_session(monkeypatch):
    session = object.__new__(Session)
    session._pid = os.getpid()
    session._headers = Headers()
    session._cookies = MappingProxyType({})
    session._timeout = 30_000

    def unexpected_submit(*args, **kwargs):
        pytest.fail("invalid inputs must not submit a native request")

    monkeypatch.setattr(session, "_submit", unexpected_submit)
    return session


@pytest.mark.parametrize("options", [
    {"timeout": 10**1000}, {"timeout": 1e308},
    {"headers_order": 1}, {"headers_order": "x"}, {"headers_order": {"x", "y"}},
    {"headers_order": {"x": "y"}}, {"headers_order": [1]},
    {"headers": [("x",)]}, {"params": 1}, {"params": [("x",)]},
    {"params": {"x": "\ud800"}}, {"data": "\ud800"}, {"data": {"x": "\ud800"}},
    {"json": {"x": object()}}, {"json": {"x": float("nan")}},
])
def test_request_encoding_and_shape_errors_are_invalid_requests(prepared_session, options):
    with pytest.raises(InvalidRequestError):
        prepared_session.request("POST", "https://example.test/", **options)


def test_circular_json_is_an_invalid_request(prepared_session):
    value = []
    value.append(value)
    with pytest.raises(InvalidRequestError, match="serializable JSON"):
        prepared_session.request("POST", "https://example.test/", json=value)


def test_iterator_programming_errors_are_not_hidden(prepared_session):
    def broken_iterator():
        yield ("X-A", "1")
        raise RuntimeError("iterator implementation failed")

    with pytest.raises(RuntimeError, match="iterator implementation failed"):
        Headers(broken_iterator())
    with pytest.raises(RuntimeError, match="iterator implementation failed"):
        prepared_session.request("GET", "https://example.test/", headers_order=(item[0] for item in broken_iterator()))


def test_encoding_programming_errors_are_not_hidden(prepared_session):
    class BrokenValue:
        def __str__(self):
            raise RuntimeError("value implementation failed")

    with pytest.raises(RuntimeError, match="value implementation failed"):
        prepared_session.request("GET", "https://example.test/", params={"x": BrokenValue()})
