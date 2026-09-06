# requests-utls — Python client

Python 3.11+ client for the separately maintained [requests-utls Go engine](https://github.com/chuu3/requests-utls).
Platform wheels include a prebuilt Go shared library, a Chrome 152 profile and
the engine's dependency licenses. Installation does not require Go, a compiler,
GitHub access, or a separately downloaded engine. Python and Go remain separate
projects, connected through CFFI ABI 1.

This is a **0.2.0 prototype**: HTTP/2 and HTTP/1.1, immutable Session defaults,
request-level `headers_order`, ordered duplicate headers, concurrent sync and
async requests, proxy authentication, and explicit resource lifecycle.

See [verification results](docs/verification.md) for concurrent requests and live
fingerprint checks, and [packaging and releases](docs/releases.md) for wheel builds.

## Install

```sh
python -m pip install requests-utls
```

| Platform | Architecture | Minimum system |
| --- | --- | --- |
| Linux | x86_64, ARM64 | glibc 2.28 |
| macOS | Apple Silicon, Intel | macOS 13 |
| Windows | x64 | Windows 10 / Server 2016 |

Python 3.11 or newer is required. Linux musl/Alpine and Windows ARM64 wheels are
not provided. Releases contain platform wheels only; unsupported systems fail
installation instead of trying to compile Go locally.

```python
from requests_utls import Profile, Session

with Session(profile=Profile.builtin("chrome_152")) as session:
    response = session.get("https://tls.peet.ws/api/all")
    print(response.json())
```

`Profile.builtin(...)` loads an installed profile without network access. Custom
profiles still work through `Profile.from_file(...)` and `Profile.from_dict(...)`.
The bundled capture retains the engine's documented profile limitations below.

For engine development, the loader also accepts `library_path=` or
`REQUESTS_UTLS_LIBRARY`, in that priority order, ahead of the bundled library.
An ABI mismatch fails immediately. Importing `requests_utls` alone does not load
native code.

## Synchronous requests and request-local order

```python
from concurrent.futures import ThreadPoolExecutor
from requests_utls import Profile, Session

profile = Profile.builtin("chrome_152")

with Session(profile=profile, timeout=30) as session:
    def fetch(index):
        return session.get(
            "https://tls.peet.ws/api/all",
            headers=[("x-a", str(index)), ("x-b", "middle"), ("x-a", "last")],
            headers_order=["x-a", "x-b", "x-a"],
            cookies={"request_id": str(index)},
        ).json()

    with ThreadPoolExecutor(max_workers=8) as threads:
        results = list(threads.map(fetch, range(8)))
```

`headers_order` belongs to **each request**. The Session profile and defaults are
immutable snapshots. There are no adapters to remount and no shared mutable
cookie jar. Native submission also copies the metadata and request body before
returning. As with other APIs, do not mutate a supplied collection while that
same call is taking its snapshot.

Request header spelling is preserved for HTTP/1.1; HTTP/2 lowercases names on
the wire. Lookup and `headers_order` matching are case-insensitive.
`Headers.multi_items()` returns lowercase names, while `Headers.raw_items()`
retains input spelling. An omitted or empty order preserves
the exact input sequence. Listing a name once groups all its values together;
listing it multiple times places one occurrence at each position, and the number
of entries must equal its supplied occurrences. Missing names are ignored.
Unlisted fields follow in their original relative order. Pseudo-header order is
part of the immutable profile, not `headers_order`.

Session headers are defaults: request fields replace all default fields with the
same name, while preserving the request's duplicate entries. Remaining defaults
precede request fields unless `headers_order` reorders them. Session `cookies=`
are static defaults, request cookies override them by name, and `Set-Cookie`
never updates shared state. Use either explicit `Cookie` headers or `cookies=`;
combining them is rejected. Protocol-owned fields and framing remain subject to
the Go engine's validation.

Use a list of pairs to send several separate `cookie` fields. For example:

```python
response = session.get(
    url,
    headers=[("cookie", "a=1"), ("x-marker", "between"), ("cookie", "b=2")],
    headers_order=["cookie", "x-marker", "cookie"],
)
```

This preserves both fields and their interleaving on the HTTP/2 wire. A Python
dict cannot represent repeated header names; the `cookies=` mapping encodes
cookie pairs into one field.

## Connection reuse and TLS session resumption

`Session` and `AsyncSession` reuse an available HTTP/2 connection to the same
origin by default, including multiplexing concurrent requests. Two requests on
that connection share its existing TLS handshake: the second request does not
send another ClientHello or add a TLS extension.

TLS session resumption is also enabled by default. When a new connection is
needed, the Session can reuse a TLS 1.3 ticket received on an earlier connection
to that origin. A resumed handshake offers the `pre_shared_key` extension
(41), which changes the ClientHello fingerprint. This requires a usable ticket,
a compatible profile, and server acceptance; a second request alone does not
guarantee resumption. The `key_share` extension (51) is separate and is already
used by ordinary TLS 1.3 handshakes.

The bounded ticket cache belongs to one Session and is never shared with another
Session. It is safe for concurrent requests; closing the Session discards its
connections and cache. To keep new connections on full handshakes while still
reusing existing connections, set:

```python
with Session(profile=Profile.builtin("chrome_152"), session_resumption=False) as session:
    first = session.get(url)
    second = session.get(url)
```

The same option applies to `AsyncSession`. Resumption does not enable TLS 0-RTT
early data.

`force_http1=True` selects HTTP/1.1 and changes the TLS advertisement to match,
including ALPN and removal of HTTP/2 application settings. It therefore changes
the captured TLS fingerprint. Without this override, the engine supports
HTTP/1.1 when selected by the TLS negotiation; plain `http://` URLs also use
HTTP/1.1. Supplied header spelling, duplicate fields and request-local order
are preserved by the HTTP/1.1 transport.

`random_ja3=True` shuffles eligible TLS extensions for each new connection,
keeping GREASE, padding and PSK positions fixed. It does not mutate the profile
or change a connection's existing handshake. The default is `False`, which
retains the profile's extension order. Both options, `session_resumption`, and
`decode_content` require actual Boolean values.

## Async requests

```python
import asyncio
from requests_utls import AsyncSession, Profile

async def main():
    async with AsyncSession(profile=Profile.builtin("chrome_152")) as session:
        responses = await asyncio.gather(*(
            session.get("https://tls.peet.ws/api/all", headers_order=["accept"],
                        headers={"accept": "application/json"})
            for _ in range(8)
        ))
        return [response.json() for response in responses]

results = asyncio.run(main())
```

Each Session has one daemon completion dispatcher, independent of request count.
The dispatcher copies completed bodies and delivers Futures to the event loop;
it does not allocate a Python worker thread per request or call Python from Go.
An `AsyncSession` is bound to its first event loop. Canceling a task cancels and
releases the corresponding native request. `await session.aclose()` (also
`await session.close()`) closes it; shutdown alone can use an executor thread to
wait for the dispatcher. Use context managers and close Sessions before closing
their event loops. Synchronous `close()` is idempotent and safe against requests
and other concurrent closes; outstanding requests fail with `SessionClosedError`.

## Proxies, authentication and certificates

```python
import os
from requests_utls import Profile, Session

with Session(
    profile=Profile.builtin("chrome_152"),
    proxy="http://proxy.example:8080",
    proxy_auth=(os.environ["PROXY_USERNAME"], os.environ["PROXY_PASSWORD"]),
    verify=True,
) as session:
    response = session.get("https://tls.peet.ws/api/all")
    response.raise_for_status()
```

The prototype supports HTTP CONNECT proxies and Basic proxy authentication.
SOCKS and HTTPS-to-proxy support are not implemented. Proxy credentials stay in
proxy configuration and are never
added to origin request headers or Session representations. Proxy configuration
is fixed per Session. `verify=True` uses system roots. `verify="ca.pem"` trusts
only that PEM bundle; `verify=False` explicitly disables certificate validation.
Environment proxy variables and `.netrc` are not consulted.

## Requests, responses and limits

`request`, `get`, `post`, `put`, `patch`, `delete`, `head` and `options` accept
`headers`, `headers_order`, `cookies`, `params`, `data`, `json`, `timeout`, and
`allow_redirects=False`. Redirect following is not implemented;
`allow_redirects=True` raises `InvalidRequestError`.
`data` supports bytes, string, or a form mapping; `json` and non-None `data` are
mutually exclusive. JSON and form encoding add Content-Type only when absent.
No User-Agent or Accept-Encoding is added by Python. Timeouts are seconds,
including native queue time; `None` disables the per-request deadline.

Response exposes `status_code`, immutable `headers`, `content`, `text`, `json()`,
`url`, `protocol`, `decoded`, `cookies`, `ok`, and `raise_for_status()`. Use
`response.headers.get_list("set-cookie")` for separate values and
`response.headers.multi_items()` for the complete field sequence. Mapping lookup
joins duplicates with `, ` and must not be used to parse Set-Cookie.

`Session(decode_content=True)` is the default. The Go engine decodes gzip,
deflate (zlib or raw), Brotli and Zstandard, including stacked Content-Encoding
values in reverse order. `content`, `text` and `json()` use the decoded body;
`decoded` is true when a coding was removed. Response headers remain the
original wire headers, so Content-Length may describe the compressed body.
Use `decode_content=False` to retain the original body bytes. Unknown or corrupt
content encodings raise `TransportError` when decoding is enabled. No Python
codec dependency or request worker thread is needed.

`response.cookies` is an immutable response-local container. Each Set-Cookie
field is parsed separately. `get_dict()` and `get(name)` work for unambiguous
names; when the same name has different domain/path scopes, they raise
`CookieConflictError` instead of silently dropping a cookie. Select a scope
with `get(name, domain="example.com", path="/")` or
`get_dict(domain="example.com", path="/")`. `items()` includes every name/value
pair, including repeated names, and iteration yields immutable `Cookie`
records with `domain`, `path`, `secure`, `http_only`, `same_site`, `expires` and
`max_age`. Attribute keys in `cookie.attributes` are lowercase; raw values,
including the original Expires date, are retained. The `expires` timestamp honors
Max-Age precedence. Invalid cookie fields are skipped without losing other
fields, and a later directive replaces an earlier one with the same
name/domain/path identity.

Expiry and deletion directives remain inspectable in this container. It does
not implement a browser's cookie acceptance/sending policy, and received cookies
never change Session defaults. Applications control cookie updates explicitly.

Defaults are 64 active requests, 256 pending requests, a 64 MiB maximum response
body, and the engine's three retries for requests proven unprocessed by the
peer. `max_unprocessed_retries=0` selects that engine default; `-1` disables
retries and `1..32` chooses an explicit limit. Configure `max_concurrent_requests`,
`max_pending_requests`, `max_response_bytes`, `max_unprocessed_retries` when
creating the Session. The Go engine additionally caps submitted bodies at 64 MiB
and metadata at 4 MiB. The response limit applies to encoded bytes, every
intermediate decoding stage and the final body. Requests beyond native capacity raise `QueueFullError`.
The dispatcher drains completed responses promptly, but application-held
Responses still consume Python memory independently of native queue limits.

Typed failures include `InvalidRequestError`, `Timeout`, `QueueFullError`,
`ResponseTooLargeError`, `TransportError`, `SessionClosedError` and
`NativeLibraryError`, all under `RequestError`. `HTTPError` from
`raise_for_status()` retains `.response`.

## Profile import and current boundaries

`Profile.from_dict(...)` and `Profile.from_file(...)` take immutable snapshots;
the native engine validates their complete supported semantics at Session
creation. Inspect `session.profile_hash` and `session.limitations`.
`Profile.from_peet(capture_dict_or_path, allow_opaque=True)` imports a single
tls.peet.ws capture using the native engine. `allow_opaque` defaults to false;
opting in accepts the engine's documented advertisement-only limitations.

The prototype buffers complete responses; streaming, redirect following,
mutable browser cookie jars, multipart uploads and full requests adapter/hook
compatibility are not implemented. The Go
profile's limitations still apply, including raw advertisement of extension
51764 rather than its full certificate-selection/retry protocol behavior.

Go runtimes cannot be safely reused after `fork`. Both Sessions and loaded
libraries reject inherited use before acquiring Python locks or making C calls.
Use multiprocessing with `spawn`, initialize a new Session in each child, and
avoid forking after native code is loaded. The OS loader retains the library for
process lifetime, including Python module cleanup and garbage collection, so it
cannot be unloaded while Go threads exist.

## Development

```sh
python -m pip install -e '.[test]'
python -m pytest tests/test_models.py tests/test_session_options.py
```

Native integration tests additionally require explicit
`REQUESTS_UTLS_LIBRARY` and `REQUESTS_UTLS_TEST_PEER` artifact paths. The test peer
is built in the Go project; tests do not look for a sibling checkout. See the
test and build documentation added with those artifacts.

```sh
REQUESTS_UTLS_LIBRARY=/absolute/path/to/librequests_utls.dylib \
REQUESTS_UTLS_TEST_PEER=/absolute/path/to/requests-utls-testpeer \
python -m pytest -q
```

Use the library filename for your platform. Editable installations use external
engine artifacts and profile files. Ordinary wheel builds require an explicit
audited engine artifact; `REQUESTS_UTLS_PURE_PYTHON=1` is a development-only escape
hatch for Python unit tests and must not be used for releases.

GitHub Actions runs Python unit tests on Python 3.11–3.14. The release workflow
builds and audits all five native wheels, installs each in a fresh environment,
and runs the full suite with its bundled library before publication. See
[packaging and releases](docs/releases.md) for the pinned engine and publishing
configuration.
