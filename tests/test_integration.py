"""Exercise the actual Python -> C ABI -> Go -> TLS/HTTP2 wire path."""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import threading
import time
import uuid

import pytest

from requests_utls import (
    AsyncSession,
    InvalidRequestError,
    Session,
    SessionClosedError,
    Timeout,
    TransportError,
)


def _wait_stats(session, peer, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        stats = session.get(peer["url"] + "/stats").json()
        if predicate(stats):
            return stats
        time.sleep(0.01)
    pytest.fail(f"peer did not reach expected state: {stats}")


async def _await_stats(session, peer, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        stats = (await session.get(peer["url"] + "/stats")).json()
        if predicate(stats):
            return stats
        await asyncio.sleep(0.01)
    pytest.fail(f"peer did not reach expected state: {stats}")


def _ordered_request(index):
    marker = ("x-marker", str(index))
    cookie1 = ("cookie", f"first={index}")
    cookie2 = ("cookie", f"second={index}")
    a1 = ("x-a", f"{index}-1")
    b2 = ("x-b", f"{index}-2")
    a3 = ("x-a", f"{index}-3")
    headers = [marker, cookie1, a1, b2, a3, cookie2]
    if index % 3 == 0:
        order = ["x-b", "cookie", "x-a"]
        expected = [b2, cookie1, cookie2, a1, a3, marker]
    elif index % 3 == 1:
        order = ["x-a", "cookie", "x-b", "x-a", "cookie"]
        expected = [a1, cookie1, b2, a3, cookie2, marker]
    else:
        order = None
        expected = headers
    return headers, order, [list(pair) for pair in expected]


def test_headers_order_and_duplicate_response_fields(peer, engine_options):
    with Session(**engine_options) as session:
        headers = [("x-a", "1"), ("x-b", "2"), ("x-a", "3")]
        response = session.get(peer["url"] + "/echo", headers=headers, headers_order=["x-b", "x-a"])
        assert response.status_code == 200
        assert response.json()["headers"] == [["x-b", "2"], ["x-a", "1"], ["x-a", "3"]]
        assert headers == [("x-a", "1"), ("x-b", "2"), ("x-a", "3")]
        assert response.headers.get_list("set-cookie") == ["peer_first=one; Path=/", "peer_second=two; Path=/"]
        assert [tuple(pair) for pair in response.headers.multi_items() if pair[0] in {"set-cookie", "x-peer"}] == [
            ("set-cookie", "peer_first=one; Path=/"),
            ("x-peer", "between"),
            ("set-cookie", "peer_second=two; Path=/"),
        ]
        # The response's Set-Cookie fields never alter a shared cookie jar.
        assert session.get(peer["url"] + "/echo").json()["headers"] == []


def test_three_cookie_fields_keep_interleaved_wire_order_and_stay_request_local(peer, engine_options):
    with Session(**engine_options) as session:
        headers = [
            ("Cookie", "first=1"),
            ("x-a", "between-first-second"),
            ("Cookie", "second=2"),
            ("x-b", "between-second-third"),
            ("Cookie", "third=3"),
        ]
        first = session.get(
            peer["url"] + "/echo",
            headers=headers,
            headers_order=["cookie", "x-b", "cookie", "x-a", "cookie"],
        ).json()
        assert first["headers"] == [
            ["cookie", "first=1"],
            ["x-b", "between-second-third"],
            ["cookie", "second=2"],
            ["x-a", "between-first-second"],
            ["cookie", "third=3"],
        ]
        assert [value for name, value in first["headers"] if name == "cookie"] == ["first=1", "second=2", "third=3"]
        second = session.get(
            peer["url"] + "/echo",
            headers=[("cookie", "next=own"), ("x-next", "independent"), ("cookie", "another=own")],
            headers_order=["x-next", "cookie", "cookie"],
        ).json()
        assert second["connection"] == first["connection"]
        assert second["headers"] == [
            ["x-next", "independent"],
            ["cookie", "next=own"],
            ["cookie", "another=own"],
        ]
        assert headers == [
            ("Cookie", "first=1"),
            ("x-a", "between-first-second"),
            ("Cookie", "second=2"),
            ("x-b", "between-second-third"),
            ("Cookie", "third=3"),
        ]


def test_shared_sync_session_multiplexes_independent_headers_order(peer, engine_options):
    size = 32
    group = uuid.uuid4().hex
    with Session(**engine_options) as session:
        connection = session.get(peer["url"] + "/warmup").json()["connection"]

        def request(index):
            headers, order, expected = _ordered_request(index)
            result = session.get(
                peer["url"] + f"/barrier?group={group}&size={size}",
                headers=headers,
                headers_order=order,
            ).json()
            assert result["headers"] == expected
            assert result["connection"] == connection
            return result["stream_id"]

        with ThreadPoolExecutor(max_workers=size) as pool:
            futures = [pool.submit(request, index) for index in range(size)]
            streams = [future.result(timeout=12) for future in futures]
        assert len(set(streams)) == size


def test_async_multiplexing_has_one_dispatch_thread_and_cancellation_isolated(peer, engine_options):
    async def scenario():
        size = 32
        group = uuid.uuid4().hex
        before = set(threading.enumerate())
        async with AsyncSession(**engine_options) as session:
            connection = (await session.get(peer["url"] + "/warmup")).json()["connection"]

            async def request(index):
                headers, order, expected = _ordered_request(index)
                result = (await session.get(
                    peer["url"] + f"/barrier?group={group}&size={size + 1}",
                    headers=headers,
                    headers_order=order,
                )).json()
                assert result["headers"] == expected
                assert result["connection"] == connection
                return result["stream_id"]

            tasks = [asyncio.create_task(request(index)) for index in range(size)]
            try:
                # Hold all 32 streams in the peer, then inspect thread growth
                # before the 33rd stream releases the barrier.
                await _await_stats(session, peer, lambda stats: stats["barriers"].get(group) == size)
                added_threads = [thread for thread in threading.enumerate() if thread not in before]
                assert len(added_threads) == 1, [thread.name for thread in added_threads]
                assert added_threads[0].name.startswith("requests-utls-completion-")
                await session.get(peer["url"] + f"/barrier?group={group}&size={size + 1}")
                streams = await asyncio.gather(*tasks)
                assert len(set(streams)) == size
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            slow = asyncio.create_task(session.get(peer["url"] + "/delay?ms=5000"))
            try:
                await _await_stats(session, peer, lambda stats: stats["active_delays"] == 1)
                sibling = (await session.get(peer["url"] + "/sibling")).json()
                assert sibling["connection"] == connection
                slow.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await slow
                await _await_stats(session, peer, lambda stats: stats["active_delays"] == 0)
                assert (await session.get(peer["url"] + "/after-cancel")).json()["connection"] == connection
            finally:
                slow.cancel()
                await asyncio.gather(slow, return_exceptions=True)

    asyncio.run(scenario())


def test_bad_order_is_request_local_and_session_remains_usable(peer, engine_options):
    with Session(**engine_options) as session:
        with pytest.raises(InvalidRequestError):
            session.get(peer["url"] + "/echo", headers_order=[":path"])
        with pytest.raises(InvalidRequestError):
            session.get(peer["url"] + "/echo", headers=[("x-a", "1")], headers_order=["x-a", "x-a"])
        assert session.get(peer["url"] + "/echo", headers=[("x-b", "2")]).json()["headers"] == [["x-b", "2"]]


def test_default_headers_and_cookies_are_copied_and_overrides_are_local(peer, engine_options):
    headers = [("x-default", "original"), ("x-keep", "yes")]
    cookies = {"initial": "original", "stay": "yes"}
    with Session(**engine_options, headers=headers, cookies=cookies) as session:
        headers[0] = ("x-default", "mutated")
        cookies["initial"] = "mutated"
        first = session.get(peer["url"] + "/echo").json()["headers"]
        assert ["x-default", "original"] in first
        cookie = next(value for name, value in first if name == "cookie")
        assert "initial=original" in cookie and "stay=yes" in cookie
        second = session.get(
            peer["url"] + "/echo", headers=[("x-default", "override")], cookies={"initial": "override"},
            headers_order=["cookie", "x-default"],
        ).json()["headers"]
        assert second[0][0] == "cookie" and "initial=override" in second[0][1]
        assert second[1] == ["x-default", "override"]
        assert ["x-keep", "yes"] in second
        assert session.get(peer["url"] + "/echo").json()["headers"] == first


def test_binary_and_json_request_bodies_cross_native_boundary(peer, engine_options):
    with Session(**engine_options) as session:
        binary = b"\x00\xff\x80binary\x00\r\n"
        response = session.post(peer["url"] + "/echo", data=binary)
        assert base64.b64decode(response.json()["body_base64"]) == binary
        payload = {"message": "中文", "count": 2}
        echoed = session.post(peer["url"] + "/echo", json=payload).json()
        assert json.loads(base64.b64decode(echoed["body_base64"])) == payload
        assert ["content-type", "application/json"] in echoed["headers"]


def test_timeout_cancels_stream_without_poisoning_connection(peer, engine_options):
    with Session(**engine_options) as session:
        connection = session.get(peer["url"] + "/warmup").json()["connection"]
        with pytest.raises(Timeout):
            session.get(peer["url"] + "/delay?ms=5000", timeout=0.03)
        _wait_stats(session, peer, lambda stats: stats["active_delays"] == 0)
        assert session.get(peer["url"] + "/after-timeout").json()["connection"] == connection


def test_sync_close_cancels_in_flight_and_is_idempotent(peer, engine_options):
    session = Session(**engine_options)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(session.get, peer["url"] + "/delay?ms=5000")
            _wait_stats(session, peer, lambda stats: stats["active_delays"] == 1)
            session.close()
            with pytest.raises(SessionClosedError):
                future.result(timeout=3)
        session.close()
        with pytest.raises(SessionClosedError):
            session.get(peer["url"] + "/echo")
    finally:
        session.close()


def test_concurrent_close_waits_for_native_cleanup(peer, engine_options, monkeypatch):
    session = Session(**engine_options)
    entered_native_close = threading.Event()
    release_native_close = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    failures = []
    native_close_calls = []
    original_status = session._native.status

    def gated_status(name, *args):
        if name == "ruts_session_close" and args == (session._sid,):
            native_close_calls.append(args)
            entered_native_close.set()
            if not release_native_close.wait(timeout=5):
                raise AssertionError("native close was not released")
        return original_status(name, *args)

    monkeypatch.setattr(session._native, "status", gated_status)

    def close(first):
        if not first:
            second_started.set()
        try:
            session.close()
        except BaseException as error:
            failures.append(error)
        finally:
            if not first:
                second_finished.set()

    first = threading.Thread(target=close, args=(True,))
    second = threading.Thread(target=close, args=(False,))
    try:
        first.start()
        assert entered_native_close.wait(timeout=3)
        # The dispatch thread is already done, so joining it cannot accidentally
        # conceal an early return from the second concurrent close call.
        session._dispatcher.join(timeout=2)
        assert not session._dispatcher.is_alive()
        second.start()
        assert second_started.wait(timeout=3)
        assert not second_finished.wait(timeout=0.1), "second close returned before native cleanup completed"
        assert len(native_close_calls) == 1
    finally:
        release_native_close.set()
        first.join(timeout=3)
        if second.ident is not None:
            second.join(timeout=3)
        session.close()
    assert not first.is_alive() and not second.is_alive()
    assert failures == []


def test_async_close_cancels_in_flight_and_is_idempotent(peer, engine_options):
    async def scenario():
        session = AsyncSession(**engine_options)
        slow = asyncio.create_task(session.get(peer["url"] + "/delay?ms=5000"))
        try:
            await _await_stats(session, peer, lambda stats: stats["active_delays"] == 1)
            await session.aclose()
            with pytest.raises(SessionClosedError):
                await slow
            await session.aclose()
            with pytest.raises(SessionClosedError):
                await session.get(peer["url"] + "/echo")
        finally:
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
            await session.aclose()

    asyncio.run(scenario())


def test_authenticated_proxy_and_credentials_do_not_reach_origin(peer, engine_options):
    with Session(
        **engine_options,
        proxy=peer["proxy_url"],
        proxy_auth=(peer["proxy_username"], peer["proxy_password"]),
    ) as session:
        echoed = session.get(peer["url"] + "/echo", headers=[("x-origin", "ok")]).json()
        assert echoed["headers"] == [["x-origin", "ok"]]
    with Session(**engine_options, proxy=peer["proxy_url"], proxy_auth=("wrong", "credentials")) as session:
        with pytest.raises(TransportError):
            session.get(peer["url"] + "/echo")


@pytest.mark.parametrize("attempt", range(3))
def test_go_library_survives_python_wrapper_collection_and_process_exit(peer, engine_options, attempt):
    # A Go c-shared image keeps runtime threads after every Session is closed.
    # Losing all Python references must never dlclose/FreeLibrary that image.
    # Isolate this regression: an owning ffi.dlopen(filename) can crash the
    # entire process during GC, a later load, or interpreter finalization.
    script = r'''
import gc
import json
import os
import sys
import time
import weakref
from requests_utls import Session, _native

options = json.loads(sys.argv[1])
for cycle in range(2):
    session = Session(**options)
    response = session.get(sys.argv[2], headers=[("x-cycle", str(cycle))])
    assert response.json()["headers"] == [["x-cycle", str(cycle)]]
    session.close()
    native_reference = weakref.ref(session._native)
    del response, session
    _native._libraries.clear()
    _native._loaded_handles.clear()
    for _ in range(3):
        gc.collect()
    assert native_reference() is None, "test did not release the Native wrapper"
    # Check residency as well as survival: an unloaded Go image may not crash
    # until one of its dormant runtime threads next wakes. NOLOAD never loads
    # the image, making this assertion deterministic on supported platforms.
    probe = _native.FFI()
    path = os.path.realpath(os.environ["REQUESTS_UTLS_LIBRARY"])
    if sys.platform == "win32":
        probe.cdef("void * __stdcall GetModuleHandleW(const wchar_t *);")
        loader = probe.dlopen("kernel32.dll")
        assert loader.GetModuleHandleW(path) != probe.NULL, "Go library was unloaded during Python GC"
    elif hasattr(os, "RTLD_NOLOAD"):
        probe.cdef("void *dlopen(const char *, int); int dlclose(void *);")
        loader = probe.dlopen(None)
        handle = loader.dlopen(os.fsencode(path), os.RTLD_NOW | os.RTLD_NOLOAD)
        assert handle != probe.NULL, "Go library was unloaded during Python GC"
        # Balance only the temporary residency probe's added loader reference.
        assert loader.dlclose(handle) == 0
    # Let the Go scheduler and background runtime threads execute while every
    # CFFI library wrapper and cache reference has been collected.
    time.sleep(0.05)

# Normal interpreter finalization is part of the assertion; do not os._exit.
print("native wrappers collected; requests and shutdown completed")
'''
    options = {key: str(value) if key in {"profile", "verify"} else value for key, value in engine_options.items()}
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(options), peer["url"] + "/echo"],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONFAULTHANDLER": "1"},
    )
    assert result.returncode == 0, f"attempt {attempt}: exit={result.returncode}\n{result.stdout}\n{result.stderr}"
    assert "native wrappers collected; requests and shutdown completed" in result.stdout


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_inherited_session_fails_before_entering_go(peer, engine_options):
    # Fork in an isolated subprocess so pytest itself never forks its Go runtime
    # or completion threads. The child must hit the Python PID guard immediately.
    script = r'''
import json
import os
import sys
from requests_utls import ForkSafetyError, Session
options = json.loads(sys.argv[1])
session = Session(**options)
pid = os.fork()
if pid == 0:
    try:
        session.get(sys.argv[2])
    except ForkSafetyError:
        os._exit(0)
    except BaseException:
        os._exit(91)
    os._exit(92)
_, status = os.waitpid(pid, 0)
session.close()
sys.exit(os.waitstatus_to_exitcode(status))
'''
    options = {key: str(value) if key in {"profile", "verify"} else value for key, value in engine_options.items()}
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(options), peer["url"] + "/echo"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
