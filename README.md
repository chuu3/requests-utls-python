# requests-utls — Python client

Python 3.11+ client for the separately maintained **requests-utls Go engine**.
The Python project contains no Go source and never builds or imports a sibling
checkout. It uses CFFI ABI 1 to load an independently built shared library.

This is a **0.1.0 prototype**: HTTPS with HTTP/2, immutable Session defaults,
request-level `headers_order`, ordered duplicate headers, concurrent sync and
async requests, proxy authentication, and explicit resource lifecycle.

See [verification results](docs/verification.md): 33 passing tests, independent
wheel installation, and live sync/async fingerprint checks through a test proxy proxy.

## Install and connect the engine

```sh
python -m pip install -e '.[test]'
export REQUESTS_UTLS_LIBRARY=/absolute/path/to/librequests_utls.dylib
```

Use `librequests_utls.so` on Linux and `librequests_utls.dll` on Windows.
The current Python-only wheel requires a separately distributed native engine
and profile. Build those in the Go project using its documented build targets,
or consume its trusted release artifacts. The loader first uses `library_path=`,
then `REQUESTS_UTLS_LIBRARY`, then a library under `requests_utls/native/` if a
future platform wheel bundles one. An ABI mismatch fails immediately. Importing
`requests_utls` alone does not load native code.

Platform wheel construction is not implemented yet. The current build includes
only Python files and `py.typed`; shared libraries are excluded so a binary cannot
accidentally be shipped with the universal `py3-none-any` wheel tag.

## Synchronous requests and request-local order

```python
from concurrent.futures import ThreadPoolExecutor
from requests_utls import Profile, Session

profile = Profile.from_file("chrome_152.json")

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

Header names are normalized to lowercase. An omitted or empty order preserves
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

## Async requests

```python
import asyncio
from requests_utls import AsyncSession

async def main():
    async with AsyncSession(profile="chrome_152.json") as session:
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
from requests_utls import Session

with Session(
    profile="chrome_152.json",
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
`headers`, `headers_order`, `cookies`, `params`, `data`, `json` and `timeout`.
`data` supports bytes, string, or a form mapping; `json` and non-None `data` are
mutually exclusive. JSON and form encoding add Content-Type only when absent.
No User-Agent or Accept-Encoding is added by Python. Timeouts are seconds,
including native queue time; `None` disables the per-request deadline.

Response exposes `status_code`, immutable `headers`, `content`, `text`, `json()`,
`url`, `protocol`, `ok`, and `raise_for_status()`. Use
`response.headers.get_list("set-cookie")` for separate values and
`response.headers.multi_items()` for the complete field sequence. Mapping lookup
joins duplicates with `, ` and must not be used to parse Set-Cookie.

Defaults are 64 active requests, 256 pending requests, a 64 MiB maximum response
body, and the engine's three retries for requests proven unprocessed by the
peer. `max_unprocessed_retries=0` selects that engine default; `-1` disables
retries and `1..32` chooses an explicit limit. Configure `max_concurrent_requests`,
`max_pending_requests`, `max_response_bytes`, `max_unprocessed_retries` when
creating the Session. The Go engine additionally caps submitted bodies at 64 MiB
and metadata at 4 MiB. Requests beyond native capacity raise `QueueFullError`.
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

The prototype buffers complete responses; streaming, automatic decompression,
redirect following, mutable browser cookie jars, multipart uploads, HTTP/1.1
fallback and full requests adapter/hook compatibility are not implemented.
Raw content remains encoded if you explicitly request gzip/br/etc. The Go
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
python -m pytest tests/test_models.py
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

Use the library filename for your platform. GitHub Actions runs independent
Python unit tests on Python 3.11–3.14; native integration tests require the
explicit artifacts above and are not silently skipped as part of that unit job.
