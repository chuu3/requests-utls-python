"""Public Python APIs across the native ABI and local HTTP/2 + HTTP/1 peers."""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from requests_utls import AsyncSession, ResponseTooLargeError, Session, TransportError


CODINGS = ("gzip", "deflate", "raw-deflate", "br", "zstd", "gzip,br")


def _endpoint(peer, engine_options, protocol):
    if protocol == "h1":
        return peer["http1_url"], {**engine_options, "verify": peer["http1_ca_file"], "force_http1": True}
    return peer["url"], dict(engine_options)


def _request_fields(echo):
    result = {}
    if isinstance(echo["headers"], dict):
        return {name.lower(): values for name, values in echo["headers"].items()}
    for name, value in echo["headers"]:
        result.setdefault(name.lower(), []).append(value)
    return result


def _assert_connection_recovery(previous, current, protocol):
    if protocol == "h2":
        assert current == previous
    else:
        # A decoder failure retires the HTTP/1 connection before reuse.
        assert current != previous


@pytest.mark.parametrize("protocol", ["h2", "h1"])
@pytest.mark.parametrize("coding", CODINGS)
def test_supported_content_decoding_and_raw_opt_out(peer, engine_options, protocol, coding):
    url, options = _endpoint(peer, engine_options, protocol)
    expected_coding = "deflate" if coding == "raw-deflate" else coding
    with Session(**options) as session:
        response = session.post(url + "/compressed/" + coding, data=b"request body", headers={"X-Marker": "decoded"})
        assert response.status_code == 200 and response.decoded is True
        assert response.protocol == ("HTTP/1.1" if protocol == "h1" else "HTTP/2.0")
        assert response.headers["content-encoding"] == expected_coding
        echo = response.json()
        assert base64.b64decode(echo["body_base64"]) == b"request body"
        assert _request_fields(echo)["x-marker"] == ["decoded"]
        assert response.cookies.get_dict() == {"peer_first": "one", "peer_second": "two"}
        repeated = session.get(url + "/compressed/" + coding, params={"size": 4096})
        assert repeated.content == b"x" * 4096 and repeated.text == "x" * 4096
        assert repeated.decoded is True
        if protocol == "h1":
            # Header lengths remain the original encoded representation.
            assert int(repeated.headers["content-length"]) < len(repeated.content)

    with Session(**options, decode_content=False, max_response_bytes=1024) as session:
        raw = session.get(url + "/compressed/" + coding, params={"size": 4096})
        assert raw.decoded is False
        assert raw.headers["content-encoding"] == expected_coding
        assert 0 < len(raw.content) < 1024
        assert raw.content != b"x" * 4096
        if protocol == "h1":
            assert int(raw.headers["content-length"]) == len(raw.content)


@pytest.mark.parametrize("protocol", ["h2", "h1"])
@pytest.mark.parametrize("coding", CODINGS)
def test_decoding_limit_failure_does_not_poison_session(peer, engine_options, protocol, coding):
    url, options = _endpoint(peer, engine_options, protocol)
    with Session(**options, max_response_bytes=1024) as session:
        connection = session.get(url + "/echo").json()["connection"]
        # Encoded data fits the limit, but the decoded representation does not.
        with pytest.raises(ResponseTooLargeError):
            session.get(url + "/compressed/" + coding, params={"size": 4096})
        after = session.get(url + "/echo")
        assert after.status_code == 200 and after.decoded is False
        _assert_connection_recovery(connection, after.json()["connection"], protocol)


@pytest.mark.parametrize("protocol", ["h2", "h1"])
@pytest.mark.parametrize("coding", CODINGS)
def test_corrupt_content_is_typed_and_recovery_is_isolated(peer, engine_options, protocol, coding):
    url, options = _endpoint(peer, engine_options, protocol)
    with Session(**options) as session:
        connection = session.get(url + "/echo").json()["connection"]
        with pytest.raises(TransportError):
            session.get(url + "/compressed/" + coding, params={"corrupt": 1})
        after = session.get(url + "/echo").json()
        _assert_connection_recovery(connection, after["connection"], protocol)


@pytest.mark.parametrize("protocol", ["h2", "h1"])
def test_raw_mode_leaves_even_corrupt_coded_bytes_untouched(peer, engine_options, protocol):
    url, options = _endpoint(peer, engine_options, protocol)
    with Session(**options, decode_content=False) as session:
        response = session.get(url + "/compressed/gzip", params={"corrupt": 1})
        assert response.content == b"invalid compressed data" and response.decoded is False
        assert session.get(url + "/echo").status_code == 200


@pytest.mark.parametrize("protocol", ["h2", "h1"])
def test_decoded_size_limit_is_inclusive(peer, engine_options, protocol):
    url, options = _endpoint(peer, engine_options, protocol)
    with Session(**options, max_response_bytes=4096) as session:
        assert session.get(url + "/compressed/gzip", params={"size": 4096}).content == b"x" * 4096
        with pytest.raises(ResponseTooLargeError):
            session.get(url + "/compressed/gzip", params={"size": 4097})


@pytest.mark.parametrize("force_http1", [False, True])
def test_http1_negotiation_reuses_connection_and_preserves_cookie_isolation(peer, engine_options, force_http1):
    options = {**engine_options, "verify": peer["http1_ca_file"], "force_http1": force_http1}
    with Session(**options, cookies={"fixed": "default"}) as session:
        first = session.get(peer["http1_url"] + "/echo", cookies={"local": "first"}, allow_redirects=False)
        assert first.protocol == "HTTP/1.1" and first.decoded is False
        assert first.json()["protocol"] == "HTTP/1.1"
        assert _request_fields(first.json())["cookie"] == ["fixed=default; local=first"]
        assert first.cookies.get_dict() == {"peer_first": "one", "peer_second": "two"}
        records = {cookie.name: cookie for cookie in first.cookies}
        assert records["peer_first"].http_only
        assert records["peer_second"].same_site == "Lax"
        first.cookies.get_dict().clear()
        second = session.get(peer["http1_url"] + "/echo")
        assert second.json()["connection"] == first.json()["connection"]
        assert _request_fields(second.json())["cookie"] == ["fixed=default"]
        assert dict(session.cookies) == {"fixed": "default"}
        assert first.cookies is not second.cookies


@pytest.mark.parametrize("force_http1", [False, True])
def test_http1_authenticated_proxy_keeps_authentication_out_of_origin(peer, engine_options, force_http1):
    options = {**engine_options, "verify": peer["http1_ca_file"], "force_http1": force_http1,
               "proxy": peer["http1_proxy_url"]}
    with Session(**options, proxy_auth=(peer["proxy_username"], peer["proxy_password"])) as session:
        response = session.get(peer["http1_url"] + "/compressed/gzip", headers={"X-Origin": "own"})
        assert response.protocol == "HTTP/1.1" and response.decoded is True
        fields = _request_fields(response.json())
        assert fields["x-origin"] == ["own"]
        assert "proxy-authorization" not in fields and "authorization" not in fields
    with Session(**options, proxy_auth=("incorrect", "credentials")) as session:
        with pytest.raises(TransportError):
            session.get(peer["http1_url"] + "/echo")


def test_shared_http1_session_handles_concurrent_requests_without_state_leaks(peer, engine_options):
    url, options = _endpoint(peer, engine_options, "h1")
    workers = 8
    start = threading.Barrier(workers)
    with Session(**options, max_concurrent_requests=workers, cookies={"fixed": "default"}) as session:
        initial = session.get(url + "/echo").json()["connection"]
        assert session.get(url + "/echo").json()["connection"] == initial

        def request(index):
            start.wait(timeout=5)
            order = ["X-Marker", "Cookie", "X-Repeated"]
            fields = [("X-Repeated", "first"), ("x-repeated", "second"), ("x-Marker", str(index))]
            result = session.post(url + "/compressed/gzip,br", data=str(index), headers=fields,
                                  headers_order=order, cookies={"local": str(index)})
            echo = result.json()
            actual = _request_fields(echo)
            assert actual["x-marker"] == [str(index)]
            assert actual["x-repeated"] == ["first", "second"]
            assert actual["cookie"] == [f"fixed=default; local={index}"]
            assert base64.b64decode(echo["body_base64"]) == str(index).encode()
            assert result.decoded and result.protocol == "HTTP/1.1"
            assert result.cookies.get_dict() == {"peer_first": "one", "peer_second": "two"}
            assert order == ["X-Marker", "Cookie", "X-Repeated"]
            return echo["connection"]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            connections = list(pool.map(request, range(32)))
        assert len(set(connections)) <= workers
        assert _request_fields(session.get(url + "/echo").json())["cookie"] == ["fixed=default"]


@pytest.mark.parametrize("protocol", ["h2", "h1"])
def test_async_decoding_concurrency_and_request_cookies_use_one_completion_thread(peer, engine_options, protocol):
    url, options = _endpoint(peer, engine_options, protocol)

    async def scenario():
        before = set(threading.enumerate())
        async with AsyncSession(**options, cookies={"fixed": "default"}, max_response_bytes=1024) as session:
            async def request(index):
                coding = CODINGS[index % len(CODINGS)]
                response = await session.post(url + "/compressed/" + coding, data=str(index), cookies={"task": str(index)})
                echo = response.json()
                assert response.decoded
                assert base64.b64decode(echo["body_base64"]) == str(index).encode()
                assert _request_fields(echo)["cookie"] == [f"fixed=default; task={index}"]
                assert response.cookies.get_dict() == {"peer_first": "one", "peer_second": "two"}

            await asyncio.gather(*(request(index) for index in range(24)))
            added = [thread for thread in threading.enumerate() if thread not in before]
            assert len(added) == 1 and added[0].name.startswith("requests-utls-completion-")
            with pytest.raises(ResponseTooLargeError):
                await session.get(url + "/compressed/zstd", params={"size": 4096})
            with pytest.raises(TransportError):
                await session.get(url + "/compressed/deflate", params={"corrupt": 1})
            after = await session.get(url + "/echo")
            assert after.decoded is False
            assert _request_fields(after.json())["cookie"] == ["fixed=default"]
        async with AsyncSession(**options, decode_content=False) as session:
            raw = await session.get(url + "/compressed/gzip", params={"size": 4096})
            assert raw.decoded is False and raw.content[:2] == b"\x1f\x8b"

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_http1_connection_close_resumption_is_session_controlled(peer, engine_options, enabled):
    url, options = _endpoint(peer, engine_options, "h1")
    with Session(**options, session_resumption=enabled) as session:
        first = session.get(url + "/reconnect").json()
        second = session.get(url + "/echo").json()
        third = session.get(url + "/echo").json()
        assert first["tls_did_resume"] is False
        assert second["connection"] != first["connection"]
        assert second["tls_did_resume"] is enabled
        assert third["connection"] == second["connection"]
