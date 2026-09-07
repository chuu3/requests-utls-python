"""Protocol-specific normalization occurs in Go after actual negotiation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from requests_utls import InvalidRequestError, Session


def h1_fields(echo):
    return {name.lower(): values for name, values in echo["headers"].items()}


def test_h2_removes_connection_fields_and_preserves_authority_and_legal_order(peer, engine_options):
    headers = [
        ("HoSt", "alternate.example.test:8443"),
        ("Connection", "keep-alive, X-Remove, Host"),
        ("Keep-Alive", "timeout=5"), ("Proxy-Connection", "keep-alive"),
        ("Transfer-Encoding", "chunked"), ("Upgrade", "h2c"), ("HTTP2-Settings", "AA"),
        ("X-Remove", "connection-scoped"), ("TE", "gzip"), ("te", "TrAiLeRs"),
        ("Cookie", "first=one"), ("X-Keep", "first"), ("Cookie", "second=two"), ("x-keep", "second"),
    ]
    order = ["host", "connection", "x-remove", "cookie", "x-keep", "cookie", "te", "x-keep",
             "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "http2-settings"]
    original = deepcopy((headers, order))
    with Session(**engine_options) as session:
        response = session.get(peer["url"] + "/echo", headers=headers, headers_order=order)
    assert response.protocol == "HTTP/2.0"
    echo = response.json()
    assert echo["authority"] == "alternate.example.test:8443"
    assert echo["headers"] == [
        ["cookie", "first=one"], ["x-keep", "first"], ["cookie", "second=two"],
        ["te", "trailers"], ["x-keep", "second"],
    ]
    assert (headers, order) == original


@pytest.mark.parametrize("force_http1", [False, True], ids=["alpn-fallback", "forced-http1"])
def test_h1_preserves_allowed_fields_and_custom_or_generated_host(peer, engine_options, force_http1):
    options = {**engine_options, "verify": peer["http1_ca_file"], "force_http1": force_http1}
    headers = [("hOsT", "alternate.example.test:8443"), ("Connection", "keep-alive, X-Hop"),
               ("Keep-Alive", "timeout=5"), ("X-Hop", "preserved"), ("TE", "gzip")]
    order = ["x-hop", "connection", "host", "keep-alive", "te"]
    with Session(**options) as session:
        response = session.get(peer["http1_url"] + "/echo", headers=headers, headers_order=order)
        assert response.protocol == "HTTP/1.1"
        echo = response.json()
        assert echo["host"] == "alternate.example.test:8443"
        fields = h1_fields(echo)
        assert fields["connection"] == ["keep-alive, X-Hop"]
        assert fields["keep-alive"] == ["timeout=5"]
        assert fields["x-hop"] == ["preserved"]
        assert fields["te"] == ["gzip"]
        following = session.get(peer["http1_url"] + "/echo", headers={"X-Next": "own"}).json()
        assert following["host"] == urlsplit(peer["http1_url"]).netloc
        assert following["connection"] == echo["connection"]
        assert h1_fields(following) == {"x-next": ["own"]}
    assert headers[0] == ("hOsT", "alternate.example.test:8443")
    assert order == ["x-hop", "connection", "host", "keep-alive", "te"]
    # net/http canonicalizes parsed headers. Exact Host placement and spelling
    # are asserted by the Go raw-wire tests, not inferred from this semantic echo.


def test_shared_session_normalizes_each_protocol_without_changing_defaults(peer, engine_options, tmp_path):
    ca = tmp_path / "both-local-peers.pem"
    ca.write_bytes(Path(peer["ca_file"]).read_bytes() + b"\n" + Path(peer["http1_ca_file"]).read_bytes())
    defaults = {"Connection": "keep-alive, X-Hop", "Keep-Alive": "timeout=5", "X-Hop": "default", "TE": "gzip"}
    with Session(**{**engine_options, "verify": ca}, headers=defaults) as session:
        def request(index):
            is_h1 = index % 2 == 1
            url = peer["http1_url" if is_h1 else "url"] + "/echo"
            headers = [("Host", f"request-{index}.example.test"), ("Cookie", f"a={index}"),
                       ("X-Marker", str(index)), ("Cookie", f"b={index}")]
            order = ["cookie", "x-marker", "cookie", "host", "connection", "keep-alive", "x-hop", "te"]
            original = deepcopy((headers, order))
            response = session.get(url, headers=headers, headers_order=order)
            echo = response.json()
            if is_h1:
                assert response.protocol == "HTTP/1.1"
                assert echo["host"] == f"request-{index}.example.test"
                fields = h1_fields(echo)
                assert fields["cookie"] == [f"a={index}; b={index}"]
                assert fields["x-hop"] == ["default"]
                assert fields["connection"] == ["keep-alive, X-Hop"]
            else:
                assert response.protocol == "HTTP/2.0"
                assert echo["authority"] == f"request-{index}.example.test"
                assert echo["headers"] == [["cookie", f"a={index}"], ["x-marker", str(index)], ["cookie", f"b={index}"]]
            assert (headers, order) == original

        with ThreadPoolExecutor(max_workers=6) as threads:
            list(threads.map(request, range(12)))
        assert dict(session.headers) == {name.lower(): value for name, value in defaults.items()}
        assert session.get(peer["url"] + "/echo").json()["headers"] == []
    assert defaults == {"Connection": "keep-alive, X-Hop", "Keep-Alive": "timeout=5", "X-Hop": "default", "TE": "gzip"}


@pytest.mark.parametrize("protocol", ["h2", "h1"])
def test_protocol_filtering_does_not_hide_invalid_headers_or_origin_proxy_auth(peer, engine_options, protocol):
    options = dict(engine_options)
    url = peer["url"]
    if protocol == "h1":
        options["verify"] = peer["http1_ca_file"]
        url = peer["http1_url"]
    with Session(**options) as session:
        for headers in ([('Connection', 'keep-alive\r\nInjected: bad')],
                        [('Connection', 'Proxy-Authorization'), ('Proxy-Authorization', 'synthetic-test-value')]):
            with pytest.raises(InvalidRequestError):
                session.get(url + "/echo", headers=headers)
        assert session.get(url + "/echo").status_code == 200


def test_h1_fallback_still_rejects_unsupported_request_framing(peer, engine_options):
    with Session(**{**engine_options, "verify": peer["http1_ca_file"]}) as session:
        with pytest.raises(InvalidRequestError):
            session.post(peer["http1_url"] + "/echo", data=b"body", headers={"Transfer-Encoding": "chunked"})
        assert session.get(peer["http1_url"] + "/echo").status_code == 200
