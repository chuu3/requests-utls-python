"""Verify connection reuse and real TLS ticket acceptance through the C ABI."""

import asyncio

import pytest

from requests_utls import AsyncSession, Session


def _assert_initial_connection(responses):
    assert len({response["connection"] for response in responses}) == 1
    assert len({response["stream_id"] for response in responses}) == len(responses)
    assert all(response["tls_did_resume"] is False for response in responses)


def _assert_reconnect(first, resumed, reused, enabled):
    assert resumed["connection"] != first["connection"]
    assert resumed["tls_did_resume"] is enabled
    assert reused["connection"] == resumed["connection"]
    assert reused["tls_did_resume"] is enabled
    assert reused["stream_id"] != resumed["stream_id"]


@pytest.mark.parametrize("enabled", [True, False], ids=["default", "disabled"])
def test_sync_connection_reuse_resumption_and_session_isolation(peer, engine_options, enabled):
    # Leave the enabled case unspecified to test the public default end to end.
    options = {**engine_options, **({} if enabled else {"session_resumption": False})}
    with Session(**options) as session:
        initial = [session.get(peer["url"] + "/echo").json() for _ in range(3)]
        # GOAWAY precedes END_STREAM, so completion guarantees pool retirement.
        initial.append(session.get(peer["url"] + "/reconnect").json())
        _assert_initial_connection(initial)
        resumed = session.get(peer["url"] + "/echo").json()
        reused = session.get(peer["url"] + "/echo").json()
        _assert_reconnect(initial[0], resumed, reused, enabled)

        # Keep the first Session alive so an accidentally global ticket cache
        # cannot hide behind cache cleanup during close.
        with Session(**engine_options) as separate:
            fresh = separate.get(peer["url"] + "/echo").json()
            assert fresh["tls_did_resume"] is False
            assert fresh["connection"] not in {initial[0]["connection"], resumed["connection"]}


@pytest.mark.parametrize("enabled", [True, False], ids=["default", "disabled"])
def test_async_connection_reuse_resumption_and_session_isolation(peer, engine_options, enabled):
    async def scenario():
        options = {**engine_options, **({} if enabled else {"session_resumption": False})}
        async with AsyncSession(**options) as session:
            initial = [(await session.get(peer["url"] + "/echo")).json() for _ in range(3)]
            initial.append((await session.get(peer["url"] + "/reconnect")).json())
            _assert_initial_connection(initial)
            resumed = (await session.get(peer["url"] + "/echo")).json()
            reused = (await session.get(peer["url"] + "/echo")).json()
            _assert_reconnect(initial[0], resumed, reused, enabled)

            async with AsyncSession(**engine_options) as separate:
                fresh = (await separate.get(peer["url"] + "/echo")).json()
                assert fresh["tls_did_resume"] is False
                assert fresh["connection"] not in {initial[0]["connection"], resumed["connection"]}

    asyncio.run(scenario())
