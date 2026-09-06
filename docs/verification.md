# Python prototype verification — 2026-09-06

Verified locally on macOS arm64, CPython 3.11.9 and the separately built Go 1.27.1
engine, native ABI 1. Other platforms have CI definitions but have not been
executed as part of this local verification.

## Local tests and independent installation

**55 tests passed**: 35 model/input tests and 20 integration cases using a real
shared library and a local TLS/HTTP2 peer. Integration coverage includes:

- 32 simultaneous streams on one reused connection from threads sharing a
  Session, with different request-level `headers_order`, duplicate fields and
  explicit cookies.
- 32 held concurrent asyncio streams, with one Python completion thread, and
  cancellation of one stream while the connection remains usable.
- Duplicate response fields, immutable defaults, binary and JSON request bodies,
  invalid request ordering, deadlines, and active/concurrent Session close.
- Three separate Cookie fields interleaved with other headers in exact order,
  followed by another request on the same connection with only its own cookies.
- An authenticated HTTP CONNECT proxy, rejection of incorrect credentials and
  absence of proxy credentials in origin headers.
- Rejection of inherited native state in a forked child before calling Go.
- Three independent subprocesses that close Sessions, collect all Python/native
  wrapper references, reload the engine, make another request and exit normally.
- Sync and async default TLS resumption after an explicit peer GOAWAY, with the
  server confirming `DidResume=true` on a new connection. Also test ordinary
  sequential HTTP/2 connection reuse, the disable option and Session isolation.

The Go project additionally passed `go test -race ./...` and `go vet ./...`,
including 64-stream Go ordering tests and native queue/handle lifecycle races.

Both sdist and `requests_utls-0.1.0-py3-none-any.whl` built successfully. The wheel
was inspected for absence of Go source or platform libraries. It was installed
in a fresh virtual environment, with the shared library, test peer and tests
copied to a temporary directory. Imports resolved to that environment's
`site-packages`; **all 55 tests passed again** there. The Python distribution
uses CFFI and requires no Go toolchain during installation or execution.

Initial wheel checks exposed an intermittent SIGSEGV after passing tests, during
interpreter shutdown. The macOS crash report showed Go background threads with
unmapped code addresses while Python finalized modules: retaining CFFI wrappers
in a global list did not prevent `dlclose` during that cleanup. The loader now
keeps an OS loading reference for process lifetime and wraps a borrowed CFFI
handle, with `RTLD_NODELETE` where available. After the fix, **20 independent
wheel test processes each passed all 33 cases and exited with status 0**. This
includes forced collection/reload subprocess checks within every suite. Those
33-case runs preceded the additional resumption tests; the current 55-case
suite retains the same lifecycle checks.

## Live TLS fingerprint and ordering

Endpoint: `https://tls.peet.ws/api/all`, reached through the user's authenticated HTTP CONNECT proxy 2.0 Basic
authenticated HTTP CONNECT proxy.

[The sanitized report](peet-proxy-python.json) records **4/4 successful probes**:
two threads sharing a Session, then two asyncio tasks sharing an AsyncSession.
Each call has a 60-second total deadline and a maximum of eight retries only
when HTTP/2 proves the peer did not process it. Observed times were 1.3–2.7 seconds.
The two requests in each API use different header orders: one interleaves
duplicate `x-a` fields; the other groups them and places Cookie first.

Every response passed checks for HTTP 200 with h2, JA3 string and hash, JA4,
Peetprint hash, HTTP/2 Akamai fingerprint, the full 51764 extension payload,
and exact request header sequence including its unique cookie.

This endpoint can close connections with GOAWAY; the local held-stream tests,
not this live probe, establish actual multiplexing on one connection. Matching
the supplied Chrome 152 sample does not implement complete browser behavior:
the profile's explicit advertisement-only TLS limitations still apply.
No proxy credentials, authenticated HTTP CONNECT proxy session ID, client IP or raw capture is stored.

A [second live report](peet-proxy-python-duplicate-cookies.json) specifically
checks two separate Cookie fields with duplicate `x-a` fields. All four probes
(two sync, two async) passed, both when Cookies were adjacent and when separated
by other fields. Observed times were 1.5–3.9 seconds. The report includes only
our known probe fields from the peer's raw HEADERS record. For one request the
peer recorded:

```text
cookie: probe=python-probe-0
x-a: python-probe-0-first
x-b: python-probe-0-middle
cookie: second=python-probe-0
x-a: python-probe-0-last
```

## Sequential requests and TLS resumption

The [resumption report](peet-proxy-resumption.json) compares two sequential
requests with the default enabled setting, then two with resumption disabled,
through the same authenticated HTTP CONNECT proxy arrangement. **All four requests passed**. Comparing
ClientHello randoms in memory confirmed that the second call in each Session
used a different TLS connection; raw randoms and ticket material are omitted.

| Configuration | First connection | Second connection |
| --- | --- | --- |
| Default `session_resumption=True` | No extension 41; original JA3 | Extension 41 last; PSK offered |
| `session_resumption=False` | Original JA3 | Original JA3; no extension 41 |

The default's second JA3 hash was `6dfb00d6b08a6befc5ce930c61f81c2a`, and JA4 was
`t13d1518h2_8daaf6152771_e2d80978ab2e`. Cipher and other extension ordering stayed
unchanged. This is a new connection offering a real cached PSK, not reuse of the
existing connection. The endpoint exposes the client offer rather than the
server's acceptance flag; local TLS/H2 servers independently confirm actual
acceptance with `DidResume=true`.

The original `peetcheck.py` now explicitly sets `session_resumption=False` so
its repeated-fingerprint assertions remain comparable to a cold capture.
Run `examples/resumptioncheck.py` with the same profile/reference/output flags
to reproduce this resumption-specific check.

## Reproduce

Build the shared library and test peer in the Go project (`make shared testpeer`)
or obtain matching artifacts, then run in this project:

```sh
python -m pip install -e '.[test]'
export REQUESTS_UTLS_LIBRARY=/absolute/path/to/librequests_utls.dylib
export REQUESTS_UTLS_TEST_PEER=/absolute/path/to/requests-utls-testpeer
python -m pytest -q
```

Use a `.so` on Linux or `.dll` and `.exe` on Windows. Native artifacts must match
the process OS and architecture. The integration tests require these explicit
paths; they never discover or build a sibling Go checkout.

For an opt-in live check, provide `REQUESTS_UTLS_PROXY`,
`REQUESTS_UTLS_PROXY_USERNAME` and `REQUESTS_UTLS_PROXY_PASSWORD` in the environment.
The profile and sanitized fingerprint baseline are separately supplied engine
artifacts:

```sh
python examples/peetcheck.py \
  --profile /absolute/path/to/chrome_152.json \
  --reference /absolute/path/to/chrome_152_fingerprint.json \
  --output /tmp/requests-utls-peet.json
```

Add `--duplicate-cookies` to repeat the separate Cookie field probe.
