"""Exercise Python loop affinity and shutdown without a native library/network."""

import asyncio
import threading

import pytest

from requests_utls import AsyncSession, InvalidRequestError


class LifecycleNative:
    def __init__(self):
        self.close_started = threading.Event()
        self.allow_close = threading.Event()
        self.close_finished = threading.Event()
        self.close_count = 0

    def call(self, name, *args):
        if name == "ruts_session_create":
            return 0, 1, b'{"profile_hash":"test","limitations":[]}'
        assert name == "ruts_session_poll", "cross-loop requests must not reach native submit"
        self.close_finished.wait(args[1] / 1000)
        return (2 if self.close_finished.is_set() else 1), 0, b""

    def status(self, name, *args):
        assert name == "ruts_session_close"
        self.close_count += 1
        self.close_started.set()
        if not self.allow_close.wait(5):
            raise AssertionError("test did not release native close")
        self.close_finished.set()
        return 0


@pytest.fixture
def lifecycle_native(monkeypatch):
    native = LifecycleNative()
    monkeypatch.setattr("requests_utls.session.get_native", lambda path: native)
    yield native
    native.allow_close.set()


def test_async_session_rejects_another_loop_before_native_work(lifecycle_native):
    session = AsyncSession(profile={})
    original_loop = asyncio.new_event_loop()
    other_loop = asyncio.new_event_loop()

    async def wrong_loop_operations():
        for operation in (session.get("https://example.test/"), session.aclose()):
            with pytest.raises(InvalidRequestError, match="one event loop"):
                await operation

    try:
        assert original_loop.run_until_complete(session.__aenter__()) is session
        other_loop.run_until_complete(wrong_loop_operations())
        assert not session.closed
        assert not lifecycle_native.close_started.is_set()
    finally:
        lifecycle_native.allow_close.set()
        original_loop.run_until_complete(session.aclose())
        original_loop.run_until_complete(original_loop.shutdown_default_executor())
        original_loop.close()
        other_loop.close()
    assert lifecycle_native.close_count == 1
    assert not session._dispatcher.is_alive()


def test_canceling_aclose_still_finishes_native_cleanup(lifecycle_native):
    async def scenario():
        session = AsyncSession(profile={})
        close_task = asyncio.create_task(session.aclose())
        try:
            started = await asyncio.wait_for(asyncio.to_thread(lifecycle_native.close_started.wait, 3), 4)
            assert started
            assert not lifecycle_native.close_finished.is_set()
            close_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close_task
            # Cancellation returns to the caller, but the shielded close keeps
            # running. A subsequent aclose must wait for that same cleanup.
            second_close = asyncio.create_task(session.aclose())
            await asyncio.sleep(0)
            assert not second_close.done()
            lifecycle_native.allow_close.set()
            await asyncio.wait_for(second_close, 3)
            assert lifecycle_native.close_finished.is_set()
            assert lifecycle_native.close_count == 1
            assert not session._dispatcher.is_alive()
        finally:
            lifecycle_native.allow_close.set()
            await session.aclose()

    asyncio.run(scenario())
