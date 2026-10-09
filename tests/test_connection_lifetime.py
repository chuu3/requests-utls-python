"""Connection lifetime validation and real Python -> ABI -> socket rotation."""
import asyncio
import json
import time

import pytest

from requests_utls import AsyncSession, InvalidRequestError, Session


@pytest.mark.parametrize("cls", [Session, AsyncSession])
@pytest.mark.parametrize("age,jitter", [
    (-1, 0), (True, 0), (1, False), (None, 0), ("1", 0),
    (float("nan"), 0), (float("inf"), 0), (1, float("inf")),
    (0, 1), (1, 1), (1, -1), (1e20, 0), (10**1000, 0),
    (0.0009, 0.0008),  # rounding may not collapse the retirement interval
])
def test_invalid_lifetime_before_native(monkeypatch, cls, age, jitter):
    monkeypatch.setattr("requests_utls.session.get_native", lambda _: pytest.fail("native loaded"))
    with pytest.raises(InvalidRequestError):
        cls(profile={}, max_connection_age=age, connection_age_jitter=jitter)


@pytest.mark.parametrize("cls", [Session, AsyncSession])
@pytest.mark.parametrize("age,jitter,expected", [(0, 0, (0, 0)), (60, 10, (60000, 10000)), (1e-10, 0, (1, 0))])
def test_lifetime_units(monkeypatch, cls, age, jitter, expected):
    class Captured(Exception):
        pass

    captured = {}

    class Native:
        def call(self, name, data, length):
            assert name == "ruts_session_create"
            captured.update(json.loads(data))
            raise Captured

    monkeypatch.setattr("requests_utls.session.get_native", lambda _: Native())
    with pytest.raises(Captured):
        cls(profile={}, max_connection_age=age, connection_age_jitter=jitter)
    assert (captured.get("max_connection_age_ms", 0), captured.get("connection_age_jitter_ms", 0)) == expected


def test_lifetime_real_native_preserves_session_and_tickets(peer, engine_options):
    with Session(**engine_options, max_connection_age=0.3, connection_age_jitter=0.05,
                 cookies={"lifetime": "preserved"}) as session:
        first = session.get(peer["url"] + "/echo").json()
        same = session.get(peer["url"] + "/echo").json()
        assert first["connection"] == same["connection"]
        time.sleep(0.31)
        second = session.post(peer["url"] + "/echo", data=b"unique-post").json()
        assert second["connection"] != first["connection"]
        assert second["tls_did_resume"] is True
        assert ["cookie", "lifetime=preserved"] in second["headers"]
        assert session.get(peer["url"] + "/echo").json()["connection"] == second["connection"]


def test_async_lifetime_real_native(peer, engine_options):
    async def run():
        async with AsyncSession(**engine_options, max_connection_age=0.2) as session:
            first = (await session.get(peer["url"] + "/echo")).json()
            await asyncio.sleep(0.21)
            second = (await session.get(peer["url"] + "/echo")).json()
            assert second["connection"] != first["connection"]
            assert second["tls_did_resume"] is True
    asyncio.run(run())


def test_http1_lifetime_real_native(peer, engine_options):
    options = {**engine_options, "verify": peer["http1_ca_file"], "force_http1": True}
    with Session(**options, max_connection_age=0.2) as session:
        first = session.get(peer["http1_url"] + "/echo").json()
        assert session.get(peer["http1_url"] + "/echo").json()["connection"] == first["connection"]
        time.sleep(0.21)
        second = session.get(peer["http1_url"] + "/echo").json()
        assert second["connection"] != first["connection"]
