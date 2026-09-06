from dataclasses import FrozenInstanceError
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from requests_utls import Headers, HTTPError, InvalidRequestError, Profile, Response
from requests_utls.session import _BaseSession, _cookies, _timeout_ms


def test_headers_preserve_interleaving_case_insensitive_lookup_and_snapshot():
    source = [("X-A", "1"), ("x-b", "2"), ("x-a", "3")]
    headers = Headers(source)
    source.clear()
    assert headers.multi_items() == [("x-a", "1"), ("x-b", "2"), ("x-a", "3")]
    assert headers.get_list("X-A") == ["1", "3"]
    assert headers["X-A"] == "1, 3"
    assert list(headers) == ["x-a", "x-b"]
    assert len(headers) == 2
    headers.multi_items().clear()
    assert headers.get_list("x-b") == ["2"]
    with pytest.raises(FrozenInstanceError):
        headers._items = ()


@pytest.mark.parametrize("fields", [[(":path", "/")], [("a b", "c")], [("x", "a\r\nb")], [("x", "\0")], [("x", 1)]])
def test_headers_reject_invalid_fields(fields):
    with pytest.raises(InvalidRequestError):
        Headers(fields)


def test_profile_is_deep_snapshot(tmp_path):
    source = {"schema_version": 1, "tls": {"extensions": [{"type": "server_name"}]}}
    profile = Profile.from_dict(source)
    source["tls"]["extensions"].clear()
    assert profile.to_dict()["tls"]["extensions"] == [{"type": "server_name"}]
    profile.to_dict()["tls"].clear()
    assert profile.to_dict()["tls"]["extensions"] == [{"type": "server_name"}]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile.to_dict()))
    assert Profile.from_file(path).to_dict() == profile.to_dict()


def test_response_separate_set_cookie_encoding_json_and_http_error():
    response = Response(200, Headers([("content-type", "text/plain; charset=iso-8859-1"), ("set-cookie", "a=1"), ("set-cookie", "b=2")]), b"caf\xe9", "https://test")
    assert response.text == "café"
    assert response.headers.get_list("set-cookie") == ["a=1", "b=2"]
    assert response.ok
    assert Response(200, Headers(), b'{"x":1}', "https://test").json() == {"x": 1}
    failure = Response(404, Headers(), b"", "https://test")
    with pytest.raises(HTTPError) as raised:
        failure.raise_for_status()
    assert raised.value.response is failure


def test_prepare_parallel_request_snapshots_without_native_engine():
    import os
    from types import MappingProxyType

    session = object.__new__(_BaseSession)
    session._pid = os.getpid()
    session._headers = Headers([("x-default", "base"), ("x-overridden", "old")])
    session._cookies = MappingProxyType({"fixed": "base"})
    session._timeout = 30_000

    def prepare(index):
        fields = [("X-Overridden", str(index)), ("X-A", "1"), ("X-B", "2"), ("X-A", "3")]
        order = ["X-A", "X-B", "X-A", "X-Overridden"]
        encoded, body, _ = session._prepare("post", "https://test/path", headers=fields, headers_order=order, cookies={"request": str(index)}, params={"id": index}, json={"id": index})
        assert fields[0][0] == "X-Overridden"
        assert order[0] == "X-A"
        metadata = json.loads(encoded)
        assert metadata["headers_order"] == ["x-a", "x-b", "x-a", "x-overridden"]
        assert metadata["headers"][1] == {"name": "x-overridden", "value": str(index)}
        assert metadata["headers"][-2] == {"name": "cookie", "value": f"fixed=base; request={index}"}
        assert json.loads(body) == {"id": index}

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(prepare, range(128)))
    assert session._headers.get_list("x-overridden") == ["old"]
    assert dict(session._cookies) == {"fixed": "base"}


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "30"])
def test_invalid_timeout(timeout):
    with pytest.raises(InvalidRequestError):
        _timeout_ms(timeout)


def test_timeout_units_and_cookie_validation():
    assert _timeout_ms(None) == 0
    assert _timeout_ms(0.0001) == 1
    assert _timeout_ms(1.5) == 1500
    assert _cookies({"Name": "value"}) == {"Name": "value"}
    with pytest.raises(InvalidRequestError):
        _cookies({"name": "a; b=2"})


def test_invalid_origin_url_redacts_credentials_before_native_submission():
    import os
    import traceback

    session = object.__new__(_BaseSession)
    session._pid = os.getpid()
    with pytest.raises(InvalidRequestError) as raised:
        session._prepare("GET", "https://user:secret@example.com：443/")
    assert str(raised.value) == "url must be a valid URL"
    assert "secret" not in "".join(traceback.format_exception_only(raised.value))
    assert raised.value.__suppress_context__
