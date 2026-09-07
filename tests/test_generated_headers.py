"""Final request framing and generated fields across the public Python API."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from urllib.parse import parse_qs

import pytest

from requests_utls import AsyncSession, InvalidRequestError, Session


@pytest.fixture(params=("h2", "h1"))
def generated_endpoint(request, peer, engine_options):
    if request.param == "h1":
        return "h1", peer["http1_url"], {
            **engine_options, "verify": peer["http1_ca_file"], "force_http1": True,
        }
    return "h2", peer["url"], dict(engine_options)


def _fields(echo):
    if isinstance(echo["headers"], dict):
        return {name.lower(): values for name, values in echo["headers"].items()}
    result = {}
    for name, value in echo["headers"]:
        result.setdefault(name.lower(), []).append(value)
    return result


@pytest.mark.parametrize("body_kind", ("utf8", "binary", "json", "form", "empty"))
def test_content_length_uses_final_encoded_body_bytes(generated_endpoint, body_kind):
    protocol, url, options = generated_endpoint
    arguments = {
        "utf8": {"data": "雪🙂 café"},
        "binary": {"data": bytearray(b"\x00\xffbinary\x80")},
        "json": {"json": {"message": "雪🙂", "values": [1, 2]}},
        "form": {"data": {"message": "雪🙂", "repeat": ["a", "b"], "space": "a b"}},
        "empty": {},
    }[body_kind]
    headers = [("X-Marker", body_kind)]
    order = ["Content-Length", "X-Marker", "Content-Type"]
    original = copy.deepcopy(arguments)
    with Session(**options) as session:
        response = session.post(url + "/echo", headers=headers, headers_order=order, **arguments)
    assert response.status_code == 200
    echo = response.json()
    body = base64.b64decode(echo["body_base64"])
    fields = _fields(echo)
    assert fields["content-length"] == [str(len(body))]
    assert "connection" not in fields
    if body_kind == "utf8":
        assert body == arguments["data"].encode("utf-8")
        assert len(body) > len(arguments["data"])
    elif body_kind == "binary":
        assert body == bytes(arguments["data"])
    elif body_kind == "json":
        assert json.loads(body) == arguments["json"]
        assert fields["content-type"] == ["application/json"]
    elif body_kind == "form":
        assert parse_qs(body.decode("ascii")) == {
            "message": ["雪🙂"], "repeat": ["a", "b"], "space": ["a b"],
        }
        assert fields["content-type"] == ["application/x-www-form-urlencoded"]
    else:
        assert body == b"" and fields["content-length"] == ["0"]
    if protocol == "h2":
        assert echo["headers"][:2] == [["content-length", str(len(body))], ["x-marker", body_kind]]
    assert arguments == original
    assert headers == [("X-Marker", body_kind)]
    assert order == ["Content-Length", "X-Marker", "Content-Type"]


@pytest.mark.parametrize("method", ("PUT", "PATCH"))
def test_empty_body_methods_generate_zero_content_length(generated_endpoint, method):
    _, url, options = generated_endpoint
    with Session(**options) as session:
        echo = session.request(method, url + "/echo", headers_order=["content-length"]).json()
    assert _fields(echo)["content-length"] == ["0"]
    assert base64.b64decode(echo["body_base64"]) == b""


def test_single_explicit_content_length_is_replaced_without_mutating_defaults(generated_endpoint):
    protocol, url, options = generated_endpoint
    defaults = {"cOnTeNt-LeNgTh": "999", "X-Default": "kept"}
    headers = [("X-Marker", "own")]
    order = ["X-Marker", "Content-Length", "X-Default"]
    with Session(**options, headers=defaults) as session:
        echo = session.post(url + "/echo", headers=headers, headers_order=order, data="雪").json()
        assert _fields(echo)["content-length"] == ["3"]
        if protocol == "h2":
            assert echo["headers"] == [
                ["x-marker", "own"], ["content-length", "3"], ["x-default", "kept"],
            ]
        assert session.headers["content-length"] == "999"
        # Explicit framing remains present even when this method has no body.
        empty = session.get(url + "/echo").json()
        assert _fields(empty)["content-length"] == ["0"]
        assert base64.b64decode(empty["body_base64"]) == b""
    assert defaults == {"cOnTeNt-LeNgTh": "999", "X-Default": "kept"}
    assert headers == [("X-Marker", "own")]
    assert order == ["X-Marker", "Content-Length", "X-Default"]


def test_repeated_content_length_is_rejected_and_session_remains_usable(generated_endpoint):
    _, url, options = generated_endpoint
    headers = [("Content-Length", "3"), ("content-length", "3")]
    with Session(**options) as session:
        with pytest.raises(InvalidRequestError):
            session.post(url + "/echo", data="雪", headers=headers)
        echo = session.get(url + "/echo").json()
        assert "content-length" not in _fields(echo)
    assert headers == [("Content-Length", "3"), ("content-length", "3")]


def _cookie_request(index):
    body = "雪🙂" + str(index)
    headers = [("Cookie", f"first={index}"), ("X-Marker", str(index)), ("cookie", f"second={index}")]
    if index % 2:
        order = ["X-Marker", "Cookie", "Cookie", "Content-Length"]
        expected = [
            ["x-marker", str(index)], ["cookie", f"first={index}"],
            ["cookie", f"second={index}"], ["content-length", str(len(body.encode("utf-8")))],
        ]
    else:
        order = ["Cookie", "Content-Length", "X-Marker", "Cookie"]
        expected = [
            ["cookie", f"first={index}"], ["content-length", str(len(body.encode("utf-8")))],
            ["x-marker", str(index)], ["cookie", f"second={index}"],
        ]
    return body, headers, order, expected


def _check_cookie_echo(protocol, echo, index, body, expected):
    assert base64.b64decode(echo["body_base64"]) == body.encode("utf-8")
    assert _fields(echo)["content-length"] == [str(len(body.encode("utf-8")))]
    if protocol == "h2":
        assert _fields(echo)["cookie"] == [f"first={index}", f"second={index}"]
        assert echo["headers"] == expected
    else:
        assert _fields(echo)["cookie"] == [f"first={index}; second={index}"]


def test_generated_content_length_participates_in_cookie_occurrence_order(generated_endpoint):
    protocol, url, options = generated_endpoint
    with Session(**options) as session:
        for index in range(2):
            body, headers, order, expected = _cookie_request(index)
            original = copy.deepcopy((headers, order))
            echo = session.post(url + "/echo", data=body, headers=headers, headers_order=order).json()
            _check_cookie_echo(protocol, echo, index, body, expected)
            assert (headers, order) == original


def test_async_generated_framing_and_cookie_order_remain_request_local(generated_endpoint):
    protocol, url, options = generated_endpoint

    async def scenario():
        async with AsyncSession(**options) as session:
            async def request(index):
                body, headers, order, expected = _cookie_request(index)
                original = copy.deepcopy((headers, order))
                response = await session.post(url + "/echo", data=body, headers=headers, headers_order=order)
                _check_cookie_echo(protocol, response.json(), index, body, expected)
                assert (headers, order) == original

            await asyncio.gather(*(request(index) for index in range(12)))
            empty = await session.post(url + "/echo")
            assert _fields(empty.json())["content-length"] == ["0"]
            assert "cookie" not in _fields(empty.json())

    asyncio.run(scenario())
