import json

import pytest

from requests_utls import AsyncSession, InvalidRequestError, Session


class CapturedConfiguration(Exception):
    pass


@pytest.mark.parametrize("session_class", [Session, AsyncSession])
@pytest.mark.parametrize(
    ("options", "disabled"),
    [({}, False), ({"session_resumption": True}, False), ({"session_resumption": False}, True)],
)
def test_session_resumption_native_configuration(monkeypatch, session_class, options, disabled):
    """Check public defaults and opt-out across the actual JSON ABI boundary."""
    captured = {}

    class NativeRecorder:
        def call(self, name, data, length):
            assert name == "ruts_session_create"
            assert length == len(data)
            captured.update(json.loads(data))
            # Stop before creating handles or starting the completion thread.
            raise CapturedConfiguration

    monkeypatch.setattr("requests_utls.session.get_native", lambda path: NativeRecorder())
    with pytest.raises(CapturedConfiguration):
        session_class(profile={}, **options)
    assert captured["disable_session_resumption"] is disabled


@pytest.mark.parametrize("session_class", [Session, AsyncSession])
@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_session_resumption_rejects_non_bool_before_loading_native(monkeypatch, session_class, value):
    def unexpected_native_load(path):
        pytest.fail("invalid options must not load a Go runtime")

    monkeypatch.setattr("requests_utls.session.get_native", unexpected_native_load)
    with pytest.raises(InvalidRequestError, match="session_resumption must be True or False"):
        session_class(profile={}, session_resumption=value)


@pytest.mark.parametrize("session_class", [Session, AsyncSession])
@pytest.mark.parametrize("options, expected", [
    ({}, {"random_ja3": False, "force_http1": False, "disable_content_decoding": False}),
    ({"random_ja3": True, "force_http1": True, "decode_content": False},
     {"random_ja3": True, "force_http1": True, "disable_content_decoding": True}),
])
def test_protocol_and_decoding_flags_cross_native_boundary(monkeypatch, session_class, options, expected):
    captured = {}

    class NativeRecorder:
        def call(self, name, data, length):
            assert name == "ruts_session_create"
            assert length == len(data)
            captured.update(json.loads(data))
            raise CapturedConfiguration

    monkeypatch.setattr("requests_utls.session.get_native", lambda path: NativeRecorder())
    with pytest.raises(CapturedConfiguration):
        session_class(profile={}, **options)
    assert {key: captured[key] for key in expected} == expected


@pytest.mark.parametrize("option", ["random_ja3", "force_http1", "decode_content"])
@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_protocol_and_decoding_flags_reject_non_bool_before_native(monkeypatch, option, value):
    def unexpected_native_load(path):
        pytest.fail("invalid options must not load a Go runtime")

    monkeypatch.setattr("requests_utls.session.get_native", unexpected_native_load)
    with pytest.raises(InvalidRequestError, match=f"{option} must be True or False"):
        Session(profile={}, **{option: value})
