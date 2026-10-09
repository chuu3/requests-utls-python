# Usage guide

Python 3.11+ client for the separately maintained [requests-utls Go engine](https://github.com/chuu3/requests-utls).
Platform wheels include a prebuilt Go shared library, built-in TLS profiles and
the engine's dependency licenses. Installation does not require Go, a compiler,
GitHub access, or a separately downloaded engine. Python and Go remain separate
projects, connected through CFFI ABI 1.

This is a **0.3.2 release**. Release notes and development changes are listed
in the [changelog](../CHANGELOG.md). The client supports
HTTP/2 and HTTP/1.1, immutable Session defaults,
request-level `headers_order`, ordered duplicate headers, concurrent sync and
async requests, proxy authentication, and explicit resource lifecycle.

See [verification results](../docs/verification.md) for concurrent requests and live
fingerprint checks, and [packaging and releases](../docs/releases.md) for wheel builds.
For development and confidential reports, see [Contributing](../CONTRIBUTING.md)
and the [security policy](../SECURITY.md).

See [phase timeouts and error diagnostics](timeouts.md) for per-phase budgets
and structured error stages.

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
The bundled captures retain the engine's documented profile limitations below.

| Builtin name | Availability |
| --- | --- |
| `chrome_152` | Included since 0.2.1 |
| `chrome_150` | Included since 0.2.2 |

Use `Profile.builtin("chrome_150")` or `Profile.builtin("chrome_152")` with an
artifact containing the selected profile. Their source files are maintained in
the [Go repository](https://github.com/chuu3/requests-utls/tree/main/profiles),
and new wheels record the complete builtin list and profile hashes in
`engine.json`. They are separate captures with different extension ordering;
Chrome 150 does not advertise extension 51764.

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
the relative order of supplied fields, subject to the protocol rules below.
Listing a name once groups all its values together;
listing it multiple times places one occurrence at each position, and the number
of entries must equal its final occurrences. Missing names are ignored.
Unlisted fields follow in their original relative order. Pseudo-header order is
part of the immutable profile, not `headers_order`.

The Go engine applies protocol-specific rules after selecting the actual
HTTP protocol, including ALPN fallback; the Python Session does not remove
fields from shared defaults or from the caller's input.

- HTTP/1.1 always has a `Host` field, generated from the request URL when absent.
  It is sent first unless `headers_order` explicitly positions `host`. A supplied
  Host value and its field name spelling are retained. Other allowed fields,
  including `Connection`, `Keep-Alive` and connection-nominated fields, retain
  their supplied spelling and participate in ordering.
- HTTP/2 consumes a supplied `Host` as `:authority` instead of sending a regular
  `host` field. Without one, authority comes from the request URL. Its position
  follows the profile's pseudo-header order, not a `host` entry in `headers_order`.
  The engine removes `Connection`, `Keep-Alive`, `Proxy-Connection`,
  `Transfer-Encoding`, `Upgrade`, `HTTP2-Settings`, and regular fields named by
  `Connection`. A `TE` occurrence is retained only when its value is exactly
  `trailers`, ignoring case, and is sent as `te: trailers`; other TE occurrences
  are removed. Connection-nominated TE is removed as well. Other legal duplicate
  fields retain their request-level order.

For HTTP/2, occurrence counts apply to the fields that survive filtering;
order entries for absent fields are ignored. A Host nominated by Connection
still supplies `:authority`. A nominated Content-Length is omitted even for a
nonempty body; HTTP/2 DATA frames carry that body without requiring the field.
Invalid header names or values and caller-supplied `Proxy-Authorization` remain
errors. HTTP/1.1 still rejects unsupported request framing and protocol upgrades;
normalization does not enable chunked uploads or upgrade handling. A standalone
`Trailer` request field remains unsupported.

HTTP/1.1 has a Cookie exception: after ordering the original occurrences, the
engine joins their nonempty values with `; ` into one field, retaining the first
ordered Cookie field's spelling and position. If all values are empty, one
empty Cookie field remains. Thus repeated Cookie names in `headers_order` count
the original fields, before merging. This also applies when ALPN selects
HTTP/1.1 from an HTTP/2-capable profile. HTTP/2 preserves separate Cookie fields.

The engine calculates `Content-Length` from the final encoded request body,
including UTF-8 strings, JSON and form data. It adds the field when the body is
nonempty or the method is `POST`, `PUT` or `PATCH`; an empty body on those methods
sends `Content-Length: 0`. Empty `GET` and `HEAD` requests do not gain the field
unless one was supplied. A single supplied value is replaced with the actual
byte count, preserving its HTTP/1.1 spelling; repeated `Content-Length` fields
are rejected. The generated field participates in `headers_order`:

```python
response = session.post(
    url,
    data="雪🙂",
    headers=[("cookie", "a=1"), ("x-marker", "middle"), ("cookie", "b=2")],
    headers_order=["cookie", "content-length", "x-marker", "cookie"],
)
```

Here the HTTP/2 fields are sent as `cookie`, `content-length: 7`, `x-marker`,
then the second `cookie`. HTTP/1.1 connection reuse is implicit; the engine does
not add `Connection: keep-alive` by default. An explicitly supplied `Connection`
field participates in ordering for HTTP/1.1 and is removed for HTTP/2.

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
HTTP/1.1. Supplied header spelling and request-local order are preserved by the
HTTP/1.1 transport, with the Cookie merging rule described above.

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
See [proxy and certificate configuration](../docs/proxies.md) for Charles, hostname
verification and the effect of SSL interception on fingerprints.

## Redirect and Cookie ownership

For precedence conflicts, server deletions, concurrency and caller responsibilities,
see [the shared design rationale (中文)](https://github.com/chuu3/requests-utls/blob/main/docs/redirects-and-cookies.md).

The client executes the request you submit and returns any 3xx response with its
status, Location, Set-Cookie fields, and body. It does not follow redirects.
`allow_redirects` defaults to `False`; `True` raises `InvalidRequestError`.
Automatic redirect following and Cookie state management are intentionally
outside this library's API. There is no `cookie_policy` option or built-in
CookieJar.

The caller decides the next URL, method, body, headers, and cookies. In
particular, the caller decides which credentials to retain when changing
origins, and how to apply Cookie Domain, Path, Secure, expiry, and deletion rules.
Read separate Location or Set-Cookie fields with `headers.get_list(...)`, rather
than the comma-joined mapping view. Read-only `response.cookies` parsing does not
accept those cookies into a jar or schedule them for another request.

Request Cookie behavior remains explicit:

- Use Cookie headers or `cookies=`, not both when the merged cookie mapping is
  nonempty. This also applies to nonempty Session cookie defaults combined with
  an explicit Cookie header.
- Session `cookies=` are static defaults. Request `cookies=` overrides matching
  names; an empty mapping does not clear the Session defaults.
- Set-Cookie never updates or deletes those defaults, nor overrides a later
  request's explicit Cookie header. If you keep supplying an old login cookie,
  the library keeps sending that supplied value even after a server deletion.
- Header ordering and protocol rules still apply: HTTP/1.1 combines Cookie
  occurrences as documented above; HTTP/2 preserves separate occurrences.

For a caller-managed cookie store, avoid storing a second copy as Session cookie
defaults; select each request's cookies in the application and supply them
explicitly. The library does not choose between manual and server cookie values.

The Session still owns pooling, TLS, concurrency, cancellation, and resource
cleanup. Sequential manual requests can reuse connections. `timeout` applies to
each request, including its queue time; a caller wanting one total time budget
across manual redirects must pass the remaining budget to subsequent calls.
These rules apply equally to Session and AsyncSession.

## Requests, responses and limits

`request`, `get`, `post`, `put`, `patch`, `delete`, `head` and `options` accept
`headers`, `headers_order`, `cookies`, `params`, `data`, `json`, `timeout`, and
`allow_redirects=False`. Redirect following is intentionally unsupported;
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
values in reverse order, with at most four non-`identity` decoding layers.
Exceeding that depth raises `TransportError` when decoding is enabled.
`content`, `text` and `json()` use the decoded body;
`decoded` is true when a coding was removed. Response headers remain the
wire headers after protocol safety checks, so Content-Length may describe the compressed body.
For H2, connection-specific headers and invalid Content-Length values are
omitted; identical Content-Length duplicates are collapsed. Other repeated
fields, including Set-Cookie, preserve their original order.
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

Redirect following and automatic Cookie state management are caller-owned, as
described above. The prototype buffers complete responses; streaming, multipart
uploads and full requests adapter/hook compatibility are not implemented. The Go
profile's limitations still apply, including raw advertisement of extension
51764 rather than its full certificate-selection/retry protocol behavior.

Go runtimes cannot be safely reused after `fork`. Both Sessions and loaded
libraries reject inherited use before acquiring Python locks or making C calls.
Use multiprocessing with `spawn`, initialize a new Session in each child, and
avoid forking after native code is loaded. The OS loader retains the library for
process lifetime, including Python module cleanup and garbage collection, so it
cannot be unloaded while Go threads exist.

## Development

Use Python 3.11+ and an editable install for source development:

```sh
python -m pip install -e '.[test]'
python -m pytest tests/test_models.py tests/test_session_options.py
```

The [contribution guide](../CONTRIBUTING.md) has the complete unit command and
native integration setup: check out the exact Go revision in `engine.lock.json`,
build the shared library and test peer, then provide their absolute paths through
`REQUESTS_UTLS_LIBRARY` and `REQUESTS_UTLS_TEST_PEER`. Tests do not discover a
sibling checkout. Editable installs use external artifacts; ordinary wheel
builds require an explicit audited engine payload.

Push and pull-request CI runs Python unit tests on Python 3.11–3.14 plus the
complete native suite on Linux against the locked engine. The release workflow
builds and audits all five native wheels, installs each in a fresh environment,
and tests its bundled library before publication. See [packaging and
releases](../docs/releases.md) for engine pins, source tags and publishing.

## Limit physical connection lifetime

See [connection lifetime (中文)](connection-lifetime.md) for Session and
AsyncSession parameters, examples, validation and native compatibility.
