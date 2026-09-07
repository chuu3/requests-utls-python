"""Certificate verification controls against an untrusted local TLS peer."""

import pytest

from requests_utls import Session, TransportError


@pytest.mark.parametrize("protocol", ["h2", "h1"])
@pytest.mark.parametrize("use_proxy", [False, True], ids=["direct", "authenticated-connect"])
def test_untrusted_certificate_requires_ca_or_explicit_verify_false(peer, engine_options, protocol, use_proxy):
    is_http1 = protocol == "h1"
    url = peer["http1_url" if is_http1 else "url"] + "/echo"
    ca_file = peer["http1_ca_file" if is_http1 else "ca_file"]
    options = {**engine_options, "force_http1": is_http1}
    if use_proxy:
        options.update(
            proxy=peer["http1_proxy_url" if is_http1 else "proxy_url"],
            proxy_auth=(peer["proxy_username"], peer["proxy_password"]),
        )

    # The fixture generates its own CA and never installs it into system trust.
    # Establish that verification fails before claiming the False case bypasses it.
    with Session(**{**options, "verify": True}) as session:
        with pytest.raises(TransportError) as raised:
            session.get(url)
    message = str(raised.value)
    assert "certificate" in message.lower() or "x509" in message.lower()
    if use_proxy:
        assert peer["proxy_username"] not in message
        assert peer["proxy_password"] not in message

    for verify in (ca_file, False):
        with Session(**{**options, "verify": verify}) as session:
            response = session.get(url)
            assert response.status_code == 200
            assert response.protocol == ("HTTP/1.1" if is_http1 else "HTTP/2.0")
            headers = response.json()["headers"]
            names = headers if isinstance(headers, dict) else [name for name, _ in headers]
            assert "proxy-authorization" not in {name.lower() for name in names}
