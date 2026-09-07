"""Cookie normalization after actual HTTP/1 selection, through the Python API."""

from __future__ import annotations

import base64
import copy

import pytest

from requests_utls import Session


@pytest.mark.parametrize("force_http1", [False, True], ids=["alpn-fallback", "forced-http1"])
def test_http1_cookie_merge_follows_occurrence_order_and_stays_request_local(peer, engine_options, force_http1):
    # The fixture profile advertises h2 first. With no override, TLS negotiates
    # HTTP/1.1 at this peer and the request crosses the H2-to-H1 handoff.
    options = {**engine_options, "verify": peer["http1_ca_file"], "force_http1": force_http1}
    defaults = [("cOoKiE", "default=one"), ("Cookie", "other=two"), ("X-Default", "kept")]
    defaults_before = copy.deepcopy(defaults)
    url = peer["http1_url"] + "/echo"
    with Session(**options, headers=defaults) as session:
        connection = None
        for index, order in enumerate([
            ["Cookie", "Content-Length", "X-Marker", "Cookie", "X-Repeated", "Cookie"],
            ["X-Marker", "Cookie", "X-Repeated", "Cookie", "Cookie", "Content-Length"],
        ]):
            headers = [
                ("cOoKiE", ""), ("Cookie", f"first={index}"),
                ("X-Repeated", "one"), ("X-Marker", str(index)),
                ("cookie", f"second={index}"), ("x-repeated", "two"),
            ]
            body = bytearray(("雪🙂" + str(index)).encode("utf-8"))
            original = copy.deepcopy((headers, order, body))
            response = session.post(url, headers=headers, headers_order=order, data=body)
            assert response.status_code == 200 and response.protocol == "HTTP/1.1"
            echo = response.json()
            # The H1 echo uses net/http's parsed map. Case and exact first-field
            # position are covered by the Go raw-wire tests; duplicate values
            # here still demonstrate whether one or several fields arrived.
            fields = {name.lower(): values for name, values in echo["headers"].items()}
            assert fields["cookie"] == [f"first={index}; second={index}"]
            assert fields["content-length"] == [str(len(body))]
            assert fields["x-repeated"] == ["one", "two"]
            assert fields["x-marker"] == [str(index)]
            assert fields["x-default"] == ["kept"]
            assert "connection" not in fields
            assert base64.b64decode(echo["body_base64"]) == bytes(body)
            assert (headers, order, body) == original
            if connection is None:
                connection = echo["connection"]
            else:
                assert echo["connection"] == connection
            assert list(session.headers.raw_items()) == defaults_before

        # Request overrides do not replace the immutable default Cookie fields.
        final = session.get(url, headers_order=["X-Default", "Cookie", "Cookie"]).json()
        assert final["connection"] == connection
        fields = {name.lower(): values for name, values in final["headers"].items()}
        assert fields["cookie"] == ["default=one; other=two"]
        assert "content-length" not in fields
        assert list(session.headers.raw_items()) == defaults_before
    assert defaults == defaults_before


@pytest.mark.parametrize("protocol", ["h1", "h2"])
@pytest.mark.parametrize("values,merged", [
    (("", "", ""), ""),
    (("", "first=1", "second=2"), "first=1; second=2"),
    (("first=1", "", "second=2"), "first=1; second=2"),
    (("first=1", "second=2", ""), "first=1; second=2"),
], ids=["all-empty", "empty-first", "empty-middle", "empty-last"])
def test_empty_cookie_occurrences_merge_only_for_actual_http1(peer, engine_options, protocol, values, merged):
    options = dict(engine_options)
    if protocol == "h1":
        options["verify"] = peer["http1_ca_file"]
    url = peer["http1_url" if protocol == "h1" else "url"] + "/echo"
    headers = [("cOoKiE", values[0]), ("cookie", values[1]), ("COOKIE", values[2]), ("X-Marker", "own")]
    order = ["Cookie", "Content-Length", "Cookie", "X-Marker", "Cookie"]
    original = copy.deepcopy((headers, order))
    with Session(**options) as session:
        response = session.post(url, headers=headers, headers_order=order)
        assert response.protocol == ("HTTP/1.1" if protocol == "h1" else "HTTP/2.0")
        echo = response.json()
        if protocol == "h1":
            fields = {name.lower(): values for name, values in echo["headers"].items()}
            assert fields["cookie"] == [merged]
            assert fields["content-length"] == ["0"]
        else:
            assert echo["headers"] == [
                ["cookie", values[0]], ["content-length", "0"],
                ["cookie", values[1]], ["x-marker", "own"], ["cookie", values[2]],
            ]
        assert base64.b64decode(echo["body_base64"]) == b""
        after = session.get(url).json()
        assert after["connection"] == echo["connection"]
        names = after["headers"] if protocol == "h1" else [name for name, _ in after["headers"]]
        assert "cookie" not in {name.lower() for name in names}
    assert (headers, order) == original
