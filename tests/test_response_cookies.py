from dataclasses import FrozenInstanceError

import pytest

from requests_utls import Cookie, CookieConflictError, Headers, Response, ResponseCookies


def response(*fields, url="https://example.test/account/login"):
    return Response(200, Headers(("set-cookie", value) for value in fields), b"", url)


def test_independent_cookie_fields_expires_comma_and_attributes(monkeypatch):
    monkeypatch.setattr("requests_utls.cookies.time.time", lambda: 1_700_000_000)
    result = response(
        'sid="session-value"; Domain=.Example.Test; Path=/; Expires=Wed, 09 Jun 2027 10:18:14 GMT; Max-Age=3600; Secure; HttpOnly; SameSite=None; Partitioned',
        "preference=dark; Path=/account; SameSite=Lax; Priority=High",
    )
    assert result.cookies.get_dict() == {"sid": "session-value", "preference": "dark"}
    assert result.cookies.items() == [("sid", "session-value"), ("preference", "dark")]
    sid, preference = result.cookies
    assert sid.domain == "example.test"
    assert sid.path == "/"
    assert sid.domain_specified and not sid.host_only
    assert sid.path_specified and sid.secure and sid.http_only
    assert sid.same_site == "None"
    assert sid.max_age == 3600 and sid.expires == 1_700_003_600
    assert sid.attributes["expires"] == "Wed, 09 Jun 2027 10:18:14 GMT"
    assert "partitioned" in sid.attributes
    assert preference.attributes["priority"] == "High"
    assert len(result.cookies) == 2  # Unknown attributes do not become cookies.
    assert "session-value" not in repr(sid) + repr(result.cookies) + repr(result)


def test_cookie_name_scope_conflicts_are_explicit_and_filterable():
    cookies = response(
        "id=base; Path=/",
        "id=account; Path=/account",
        "id=other; Domain=other.test; Path=/",
    ).cookies
    assert len(cookies) == 3
    assert cookies.items() == [("id", "base"), ("id", "account"), ("id", "other")]
    for lookup in (lambda: cookies.get("id"), lambda: cookies["id"], cookies.get_dict,
                   lambda: cookies.get_dict(domain="example.test")):
        with pytest.raises(CookieConflictError):
            lookup()
    assert cookies.get("id", domain=".EXAMPLE.TEST", path="/") == "base"
    assert cookies.get_dict(path="/account") == {"id": "account"}
    assert cookies.get_dict(domain="other.test") == {"id": "other"}
    assert cookies.get("missing", "fallback") == "fallback"
    assert "id" in cookies and "missing" not in cookies
    with pytest.raises(KeyError):
        cookies["missing"]


def test_same_identity_last_directive_wins_and_deletions_are_inspectable():
    cookies = response("id=old; Domain=.example.test; Path=/", "id=; Domain=example.test; Path=/; Max-Age=0").cookies
    assert len(cookies) == 1
    assert cookies.get_dict() == {"id": ""}
    cookie = next(iter(cookies))
    assert cookie.max_age == 0 and cookie.is_expired()


@pytest.mark.parametrize("url, expected", [
    ("https://example.test", "/"),
    ("https://example.test/one", "/"),
    ("https://example.test/one/", "/one"),
    ("https://example.test/one/two?x=1", "/one"),
])
def test_host_and_default_path_follow_response_url(url, expected):
    cookie = next(iter(response("name=value; Path=relative", url=url).cookies))
    assert cookie.domain == "example.test" and cookie.host_only
    assert cookie.path == expected and not cookie.path_specified


def test_expiry_is_parsed_without_splitting_cookie_on_comma():
    cookies = response("old=yes; Expires=Thu, 01 Jan 1970 00:00:01 GMT", "new=yes; Expires=not-a-date; Max-Age=invalid").cookies
    old, new = cookies
    assert old.expires == 1 and old.is_expired(now=2)
    assert new.expires is None and new.max_age is None and not new.is_expired()


def test_invalid_field_does_not_discard_other_cookie_fields():
    cookies = response("invalid", "bad name=value", 'unclosed="value', "valid=ok; Unknown=attribute").cookies
    assert cookies.get_dict() == {"valid": "ok"}


def test_cookie_records_and_containers_are_immutable_snapshots():
    attributes = {"samesite": "Lax"}
    cookie = Cookie("name", "value", "example.test", "/", attributes=attributes)
    cookies = ResponseCookies([cookie])
    attributes.clear()
    assert cookie.attributes["samesite"] == "Lax"
    with pytest.raises(TypeError):
        cookie.attributes["samesite"] = "None"
    with pytest.raises(FrozenInstanceError):
        cookie.value = "changed"
    with pytest.raises(FrozenInstanceError):
        cookies._cookies = ()
    cookies.get_dict().clear()
    cookies.items().clear()
    assert cookies["name"] == "value"
    assert cookies.keys() == ["name"] and cookies.values() == ["value"]
    assert not ResponseCookies()


def test_responses_do_not_share_cookie_containers():
    first = response("one=1")
    second = response("two=2")
    assert first.cookies.get_dict() == {"one": "1"}
    assert second.cookies.get_dict() == {"two": "2"}
