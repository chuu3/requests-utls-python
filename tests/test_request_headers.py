from concurrent.futures import Future
import json
import os
import threading
from types import MappingProxyType

import pytest

from requests_utls import Headers, InvalidRequestError
from requests_utls.session import _BaseSession, _Pending


def make_session(headers=None):
    session = object.__new__(_BaseSession)
    session._pid = os.getpid()
    session._headers = Headers(headers)
    session._cookies = MappingProxyType({})
    session._timeout = 30_000
    return session


def test_original_field_spelling_survives_snapshot_merge_and_abi():
    defaults = [("User-Agent", "base"), ("X-Replaced", "old"), ("x-default", "keep")]
    session = make_session(defaults)
    defaults.clear()
    fields = [("x-REPLACED", "new"), ("X-Mixed", "one"), ("x-mixed", "two")]
    requested = Headers(fields)
    fields.clear()
    order = ["X-MIXED", "x-mixed", "user-agent"]
    metadata, _, _ = session._prepare("GET", "https://example.test", headers=requested, headers_order=order)
    result = json.loads(metadata)
    assert result["headers"] == [
        {"name": "User-Agent", "value": "base"},
        {"name": "x-default", "value": "keep"},
        {"name": "x-REPLACED", "value": "new"},
        {"name": "X-Mixed", "value": "one"},
        {"name": "x-mixed", "value": "two"},
    ]
    assert result["headers_order"] == ["x-mixed", "x-mixed", "user-agent"]
    assert order == ["X-MIXED", "x-mixed", "user-agent"]
    assert session.headers["USER-AGENT"] == "base"
    assert requested.multi_items() == [("x-replaced", "new"), ("x-mixed", "one"), ("x-mixed", "two")]
    assert Headers({"X-Name": "value"}) == Headers({"x-name": "value"})
    requested.raw_items().clear()
    assert len(requested.raw_items()) == 3


def test_generated_fields_do_not_duplicate_mixed_case_explicit_headers():
    session = make_session()
    metadata, _, _ = session._prepare("POST", "https://example.test", headers={"cOnTeNt-TyPe": "custom"}, json={})
    assert json.loads(metadata)["headers"] == [{"name": "cOnTeNt-TyPe", "value": "custom"}]
    with pytest.raises(InvalidRequestError, match="Cookie header"):
        session._prepare("GET", "https://example.test", headers={"COOKIE": "one=1"}, cookies={"two": "2"})


def test_allow_redirects_false_is_accepted_and_true_is_explicitly_unsupported():
    session = make_session()
    session._prepare("GET", "https://example.test", allow_redirects=False)
    with pytest.raises(InvalidRequestError, match="redirect following is not implemented"):
        session._prepare("GET", "https://example.test", allow_redirects=True)
    for value in (None, 0, 1, "false"):
        with pytest.raises(InvalidRequestError, match="allow_redirects must be True or False"):
            session._prepare("GET", "https://example.test", allow_redirects=value)


@pytest.mark.parametrize("decoded", [False, True])
def test_completion_preserves_native_decoding_flag_and_original_headers(decoded):
    session = make_session()
    session._sid = 1
    session._lock = threading.Lock()
    session._closed = False
    session._close_done = threading.Event()
    session._secrets = ()
    future = Future()
    session._pending = {7: _Pending(future, None, "https://example.test")}

    class NativeCompletion:
        polled = False

        def call(self, name, *args):
            if name == "ruts_request_body":
                return 0, 7, b"body supplied by Go"
            assert name == "ruts_session_poll"
            if self.polled:
                return 2, 0, b'{"message":"closed"}'
            self.polled = True
            return 0, 7, json.dumps({
                "status_code": 200, "protocol": "HTTP/1.1", "decoded": decoded,
                "headers": [{"name": "Content-Encoding", "value": "gzip"},
                            {"name": "Set-Cookie", "value": "name=value"}],
            }).encode()

        def status(self, name, *args):
            return 0

    session._native = NativeCompletion()
    session._dispatch()
    result = future.result()
    assert result.content == b"body supplied by Go" and result.decoded is decoded
    assert result.protocol == "HTTP/1.1"
    assert result.headers["content-encoding"] == "gzip"
    assert result.cookies.get_dict() == {"name": "value"}
