# requests-utls — Python client

Python 3.11+ HTTP client with configurable TLS profiles, concurrent sync/async
Sessions, and ordered HTTP/1.1 and HTTP/2 headers. Platform wheels bundle the
separate [Go engine](https://github.com/chuu3/requests-utls); no Go compiler is
needed to install or use the package.

## Quick start

```sh
python -m pip install requests-utls
```

```python
from requests_utls import Profile, Session

with Session(profile=Profile.builtin("chrome_152")) as session:
    response = session.get("https://example.com/")
    print(response.status_code)
```

Built-in profiles: `chrome_150` and `chrome_152`. Custom profiles can be loaded
with `Profile.from_file(...)` or `Profile.from_dict(...)`.

| Platform | Supported wheels |
| --- | --- |
| Linux, glibc 2.28+ | x86_64, ARM64 |
| macOS 13+ | Apple Silicon, Intel |
| Windows 10 / Server 2016+ | x64 |

Unsupported systems do not fall back to compiling Go during installation.

## Behavior

- Request-level `headers_order`, repeated headers, ALPN fallback, connection
  reuse, TLS resumption, proxy authentication and bounded response decoding.
- **No automatic redirects:** `allow_redirects=False` is the default;
  `True` raises `InvalidRequestError`. Inspect 3xx responses and Location yourself.
- **No automatic Cookie maintenance:** Set-Cookie and read-only `response.cookies`
  never change later requests. Session cookies are static defaults, not a jar.
  Use explicit Cookie headers or `cookies=`, not both with nonempty cookie defaults.
- The Session still manages connections, cancellation and resource cleanup.
  Sync and async share these rules.

TLS profile support has [documented limits](docs/usage.md#profile-import-and-current-boundaries);
a fingerprint match is not full browser equivalence. Streaming, multipart and
HTTPS/SOCKS proxy connections are not supported.

## Documentation

| Task | Guide |
| --- | --- |
| Why redirects and Cookie state are caller-owned | [Design rationale (中文)](https://github.com/chuu3/requests-utls/blob/main/docs/redirects-and-cookies.md) |
| Sync/async requests, headers, cookies and profiles | [Usage](docs/usage.md) |
| Configure proxies and certificate trust | [Proxies](docs/proxies.md) |
| Review test evidence | [Verification](docs/verification.md), [profile acceptance](docs/profile-acceptance.md) |
| Develop or build wheels | [Contributing](CONTRIBUTING.md), [releases](docs/releases.md) |
| Review changes or report a vulnerability | [Changelog](CHANGELOG.md), [security](SECURITY.md) |
