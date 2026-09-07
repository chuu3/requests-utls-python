# Verification

Results below identify the release or prototype actually checked. They are
historical evidence for those source revisions, not a claim that later changes
have already passed the same checks. See [packaging and releases](releases.md)
for the current build procedure.

## Development checks: Chrome 150 and HTTP/1.1 fallback

The maintenance changes were checked with Python source
`1ec9031063a8e8aa85464b6d7c45c61bd68c423e` and its locked Go engine
`0de755f312aef3019857efa15c47bc72cf3d2602`. These are development checks;
the latest published package remains 0.2.1.

[Python CI 34101668331](https://github.com/chuu3/requests-utls-python/actions/runs/34101668331)
passed on Python 3.11, 3.12, 3.13 and 3.14, each with 231 unit tests passed and
one installed-wheel-only check skipped. Linux native integration passed 336
tests, with that same installed-wheel-only check skipped.

[Five-platform wheel run 34101719141](https://github.com/chuu3/requests-utls-python/actions/runs/34101719141)
built and audited the same source revision, then installed every wheel in a
fresh environment and tested its bundled engine and both builtin profiles:

| Wheel target | Installed package tests |
| --- | --- |
| manylinux_2_28_x86_64 | 337 passed |
| manylinux_2_28_aarch64 | 337 passed |
| macosx_13_0_arm64 | 337 passed |
| macosx_13_0_x86_64 | 337 passed |
| win_amd64 | 335 passed, 2 POSIX-only checks skipped |

This run used `publish=false`; the publishing job was skipped. These artifacts
are development builds, not a replacement for the published 0.2.1 files.

[Go CI 34101337617](https://github.com/chuu3/requests-utls/actions/runs/34101337617)
passed on Linux, macOS and Windows; all three native artifacts include both
Chrome profiles and dependency license notices.
[Security and upstream checks 34101337667](https://github.com/chuu3/requests-utls/actions/runs/34101337667)
also passed. The local Go race suite and vet checks passed.

The added protocol checks cover H2-capable profiles negotiating HTTP/1.1,
Cookie combination after occurrence-based ordering, original field spelling
and position, computed Content-Length, empty Cookie values, connection reuse,
request isolation, and unchanged separate Cookie fields on HTTP/2. Local
untrusted TLS peers establish that default verification rejects their
certificates, while the explicit test CA or `verify=False` succeeds, for both
HTTP versions and authenticated CONNECT routes.

A live Chrome 150 capture comparison passed at `https://tls.peet.ws/api/all`;
direct requests with both builtins returned HTTP/2 JSON. A separate forced
HTTP/1.1 request also returned valid Peet JSON. With local Charles interception,
an H2-capable profile negotiated HTTP/1.1 with `verify=False`, but the proxy
route returned HTML for both minimal headers and duplicate Cookie headers.
The Charles raw request inspection then confirmed one
`Cookie: first=one; second=two` field at the first ordered Cookie position,
followed by `X-Order`, User-Agent, Accept and the generated Host. This verifies
Cookie combination on the client-to-Charles connection; a Peet Cookie echo
through that proxy was unavailable. The automated Cookie wire assertions above
use independent local peers.

## Published 0.2.1 wheels

[Release run 34096309485](https://github.com/chuu3/requests-utls-python/actions/runs/34096309485)
built, audited and tested all five wheels, then successfully published version
0.2.1 to PyPI through Trusted Publishing. The exact source identities were:

- Python: `3d4e84a7ca20dfb3f6d409c5e8c835b403b52515`.
- Go engine: `13ea2f55c4fed7505c2933cfe8dbd65ec64cdcb7`.
- Go toolchain: 1.27.1; native ABI: 1.

Each job installed its wheel in a fresh virtual environment and ran the suite
against the bundled engine and an independent local test peer, without an
external native library override.

| Wheel target | Installed package tests |
| --- | --- |
| manylinux_2_28_x86_64 | 245 passed |
| manylinux_2_28_aarch64 | 245 passed |
| macosx_13_0_arm64 | 245 passed |
| macosx_13_0_x86_64 | 245 passed |
| win_amd64 | 243 passed, 2 POSIX-only checks skipped |

The Windows skips were
`test_finalized_wheel_is_readable_outside_the_build_container` (POSIX permission
bits) and `test_inherited_session_fails_before_entering_go` (POSIX `fork`). Their
names and reasons are established by the release's explicit `skipif`
declarations; the quiet pytest log reports the aggregate count.

The five-wheel release set passed strict Twine checks and the publishing job
succeeded. It contains no sdist or universal wheel. Before release, the Python
3.11–3.14 unit matrix and Go Linux/macOS/Windows CI also passed. These checks
exercise local protocol behavior and packaging; the separate historical
[0.2.0 profile acceptance](profile-acceptance.md) records the bulk live TLS
fingerprint comparison and its limitations.

## Historical bundled-wheel dry run

The earlier release build 34031300350 passed all five platform jobs using Python commit
`0dc71d7`, Go commit `29e5d709117dac8086d68ae24d1f33c97930d789`, Go 1.27.1 and
native ABI 1. Each job built and audited a wheel, installed it into a new
virtual environment, and exercised the bundled engine with no external library
override.

| Wheel target | Installed package tests |
| --- | --- |
| manylinux_2_28_x86_64 | 109 passed |
| manylinux_2_28_aarch64 | 109 passed |
| macosx_13_0_arm64 | 109 passed |
| macosx_13_0_x86_64 | 109 passed |
| win_amd64 | 107 passed, 2 POSIX-only checks skipped |

All five wheels passed strict Twine metadata checks and were uploaded as private
Actions artifacts. They contain the engine, built-in Chrome 152 profile,
licenses and matching provenance/checksums. Linux wheels advertise only the
tested manylinux 2.28 policy. No sdist or universal wheel is included in the
release set.

The Python unit matrix passed on Python 3.11–3.14. The Go Linux/macOS/Windows
matrix also passed, including 10 native packaging guard tests per system.
The release workflow was dispatched with `publish=false`; this verification
does not imply that the files have been uploaded to PyPI.

## Original prototype: local tests and independent installation

The original checks used macOS arm64, CPython 3.11.9 and the separately built
Go 1.27.1 engine, native ABI 1. They predate bundled platform wheels.

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

In that initial prototype check, both sdist and a development-only
`requests_utls-0.1.0-py3-none-any.whl` built successfully. That universal wheel is
not a release artifact. It was inspected for absence of Go source or platform
libraries. It was installed
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
33-case runs preceded the additional resumption tests; the later 55-case
prototype suite retained the same lifecycle checks.

## Live TLS fingerprint and ordering

Endpoint: `https://tls.peet.ws/api/all`, reached through an authenticated HTTP
CONNECT proxy.

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
No proxy credentials, proxy session identifiers, client IP or raw capture is stored.

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

Follow the [contribution guide](../CONTRIBUTING.md#native-integration) to build
the shared library and test peer from the current `engine.lock.json`, or obtain
matching artifacts. Then run in this project:

```sh
python -m pip install -e '.[test]'
export REQUESTS_UTLS_LIBRARY=/absolute/path/to/librequests_utls.dylib
export REQUESTS_UTLS_TEST_PEER=/absolute/path/to/requests-utls-testpeer
python -m pytest -q
```

Use a `.so` on Linux or `.dll` and `.exe` on Windows. Native artifacts must match
the process OS and architecture. The integration tests require these explicit
paths; they never discover or build a sibling Go checkout.
Testing today's lock is a new verification; reproducing a historical record
requires that record's Python and Go revisions.

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
