# Proxies and certificate verification

`Session` and `AsyncSession` accept an explicit HTTP CONNECT proxy. The proxy URL
uses `http://` even when the target URL is HTTPS. Proxy configuration and
certificate verification settings are fixed when the Session is created.

## Local Charles proxy

If Charles is listening on local port 8888:

```python
from requests_utls import Profile, Session

with Session(
    profile=Profile.builtin("chrome_152"),
    proxy="http://127.0.0.1:8888",
    verify=True,
    session_resumption=False,
) as session:
    response = session.get("https://tls.peet.ws/api/all")
    response.raise_for_status()
    print(response.json()["tls"]["ja3_hash"])
```

The client asks Charles to open a CONNECT tunnel to the destination, then starts
TLS through that tunnel. Charles can either forward this TLS connection or
intercept it. With **SSL Proxying enabled for the target**, Charles presents a
certificate signed by its own root CA and establishes a separate TLS connection
to the destination. The destination therefore sees Charles's TLS fingerprint.
To validate a requests-utls profile at the remote server, disable SSL Proxying
for that target and create a new Session. With interception disabled, Charles
forwards the TLS traffic. See the official
[Charles SSL Proxying documentation](https://www.charlesproxy.com/documentation/proxying/ssl-proxying/).

`session_resumption=False` keeps any new connections on cold handshakes for this
comparison. Reusing an existing connection does not send another ClientHello.
Changing certificate verification settings does not turn SSL Proxying on or off
and cannot make an intercepted connection expose the original client fingerprint.
`response.protocol` reports the HTTP protocol negotiated between this client and
its TLS peer. During interception that peer is Charles; Charles's separate
upstream connection to the destination can use a different HTTP version.

## Trust settings

| Setting | Behavior |
| --- | --- |
| `verify=True` (default) | Verify the certificate chain against system roots and verify the target hostname. |
| `verify="ca.pem"` | Verify the chain and hostname using only the roots in this PEM file; the file replaces, rather than augments, system roots. |
| `verify=False` | Skip certificate-chain and hostname verification for the target TLS connection. |

For intentional Charles interception, export its root certificate as PEM and
pass its path, for example `verify="charles-ca.pem"`. That trusts certificates
issued by the supplied CA while retaining hostname checks. Whether `verify=True`
trusts that CA depends on the machine's trust configuration. An explicit
`verify=False` can be used for a controlled debugging session, but it removes
server authentication. These options apply to the TLS connection after CONNECT;
they do not add TLS support to the proxy endpoint itself.

The same configuration works with the async API:

```python
import asyncio
from requests_utls import AsyncSession, Profile

async def main():
    async with AsyncSession(
        profile=Profile.builtin("chrome_152"),
        proxy="http://127.0.0.1:8888",
        verify="charles-ca.pem",
    ) as session:
        response = await session.get("https://tls.peet.ws/api/all")
        response.raise_for_status()
        return response.status_code

print(asyncio.run(main()))
```

## Authentication and supported routes

Basic proxy authentication is supplied separately as
`proxy_auth=(username, password)`, preferably using environment variables or
another external credential source. It is sent to the proxy in CONNECT and is
not added to origin headers. The client does not read environment proxy settings
or `.netrc` automatically.

HTTPS proxy endpoints (`proxy="https://..."`) and SOCKS proxies are not
implemented. Plain `http://` targets also use CONNECT through a configured proxy,
then send HTTP/1.1 in the tunnel without target TLS. The proxy must permit CONNECT
to that destination port, including port 80 when applicable; a proxy that accepts
only ordinary forwarded HTTP requests on port 80 will reject this route.

Whenever the actual client connection uses HTTP/1.1, including ALPN fallback
through a CONNECT tunnel, multiple Cookie fields are combined after applying
`headers_order` to their original occurrences. Nonempty values are joined with
`; ` at the first ordered Cookie field's original spelling and position; all
empty values produce one empty Cookie field. The supplied headers and order are
unchanged. HTTP/2 retains separate Cookie fields, even if an intercepting proxy
later chooses HTTP/1.1 for its own upstream connection.
