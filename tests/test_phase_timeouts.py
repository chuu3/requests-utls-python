"""Real native phase timeouts, shared H2 sessions and sync/async parity."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from requests_utls import AsyncSession, Session, Timeout


@pytest.mark.parametrize("asynchronous", [False, True])
def test_h2_header_timeout_native(peer, engine_options, asynchronous):
    options = {**engine_options, "response_header_timeout": 0.06}
    if not asynchronous:
        with Session(**options) as session:
            assert session.get(peer["url"]).status_code == 200
            with pytest.raises(Timeout) as caught:
                session.get(peer["url"] + "/delay?ms=2000")
            assert caught.value.stage == "response_headers"
            assert caught.value.elapsed_ms > 0
            assert session.get(peer["url"]).status_code == 200
    else:
        async def run():
            async with AsyncSession(**options) as session:
                assert (await session.get(peer["url"])).status_code == 200
                slow = asyncio.create_task(session.get(peer["url"] + "/delay?ms=2000"))
                assert (await session.get(peer["url"])).status_code == 200
                with pytest.raises(Timeout) as caught:
                    await slow
                assert caught.value.stage == "response_headers"
                assert caught.value.elapsed_ms > 0
                assert (await session.get(peer["url"])).status_code == 200
        asyncio.run(run())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_http1_body_timeout_native(peer, engine_options, asynchronous):
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "10")
            self.end_headers()
            self.wfile.flush()
            release.wait(5)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    options = {**engine_options, "body_timeout": 0.06}
    try:
        if not asynchronous:
            with Session(**options) as session:
                with pytest.raises(Timeout) as caught:
                    session.get(url)
                assert caught.value.stage == "body"
                assert caught.value.elapsed_ms > 0
        else:
            async def run():
                async with AsyncSession(**options) as session:
                    with pytest.raises(Timeout) as caught:
                        await session.get(url)
                    assert caught.value.stage == "body"
                    assert caught.value.elapsed_ms > 0
            asyncio.run(run())
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
