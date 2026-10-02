# Development guide

This repository owns the Python API, CFFI binding and platform wheel builds.
Transport, TLS profile compilation and C ABI changes belong in the separate
[Go engine](https://github.com/chuu3/requests-utls). Cross-link related pull
requests; do not copy the Go source into this project.

All shell commands run from the repository root unless stated otherwise.

## Python development

Use Python 3.11 or newer and a virtual environment. From this checkout:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q \
  tests/test_models.py tests/test_session_options.py \
  tests/test_response_cookies.py tests/test_request_headers.py \
  tests/test_profile_acceptance.py tests/test_build_backend.py \
  tests/test_bundled.py tests/test_finalize_wheel.py \
  tests/test_validation.py tests/test_async_lifecycle.py
```

On Windows, activate `.venv\Scripts\Activate.ps1` in PowerShell instead.
Editable installs use the Python source without embedding a native library;
these unit tests need no Go compiler or external service. The installed-wheel
case in `test_bundled.py` is skipped in this mode. Ordinary wheel builds require
an explicit native artifact. `REQUESTS_UTLS_PURE_PYTHON=1` permits a development
wheel for unit CI only; it must not be used for a release.

## Native integration

`engine.lock.json` specifies the engine repository, exact commit, Go toolchain
and ABI. Use that revision to reproduce the Python tests, rather than the
engine's current default branch. Install the specified Go version and a C
compiler for cgo. Use a dedicated engine checkout so checking out the pin does
not disturb unrelated work.

The following example runs from the Python checkout on Linux. Choose an unused
absolute path for the engine checkout:

```sh
export REQUESTS_UTLS_ENGINE_DIR=/absolute/path/to/requests-utls
git clone https://github.com/chuu3/requests-utls.git "$REQUESTS_UTLS_ENGINE_DIR"
requests_utls_engine_commit="$(python -c 'import json; print(json.load(open("engine.lock.json"))["commit"])')"
git -C "$REQUESTS_UTLS_ENGINE_DIR" checkout --detach "$requests_utls_engine_commit"
export GOTOOLCHAIN=local
export CGO_ENABLED=1
python - <<'PY'
import json
import subprocess

with open("engine.lock.json") as source:
    expected = "go" + json.load(source)["go_version"]
actual = subprocess.check_output(["go", "version"], text=True).split()[2]
if actual != expected:
    raise SystemExit(f"Install the locked Go toolchain: expected {expected}, got {actual}")
PY

export REQUESTS_UTLS_ARTIFACT_DIR="$PWD/dist/native-dev"
mkdir -p "$REQUESTS_UTLS_ARTIFACT_DIR"
export REQUESTS_UTLS_LIBRARY="$REQUESTS_UTLS_ARTIFACT_DIR/librequests_utls.so"
export REQUESTS_UTLS_TEST_PEER="$REQUESTS_UTLS_ARTIFACT_DIR/requests-utls-testpeer"
go -C "$REQUESTS_UTLS_ENGINE_DIR" build -mod=readonly -trimpath -buildvcs=false \
  -buildmode=c-shared -o "$REQUESTS_UTLS_LIBRARY" ./cmd/requests-utls-shared
go -C "$REQUESTS_UTLS_ENGINE_DIR" build -mod=readonly -trimpath -buildvcs=false \
  -o "$REQUESTS_UTLS_TEST_PEER" ./cmd/requests-utls-testpeer
python -m pip install -e '.[test]'
python -m pytest -q tests
```

On macOS use `librequests_utls.dylib`. On Windows use `librequests_utls.dll`,
`requests-utls-testpeer.exe`, a supported cgo C compiler, and PowerShell's
`$env:NAME = "value"` syntax for environment variables. The library and peer must
match the Python process's OS and architecture. Tests consume these explicit
artifact paths; they never discover or build a sibling checkout themselves.
The peer serves local TLS, HTTP/1.1, HTTP/2 and authenticated proxy fixtures, so
the suite needs no external proxy or account. The bundled-wheel-only test runs
separately in the release build described in [packaging and releases](../docs/releases.md).

## CI and pull requests

Pushes and pull requests run Python unit tests on Python 3.11–3.14 and a Linux
native integration job using the locked engine and complete test suite. Release
CI additionally builds, audits and tests installed wheels on all five targets.
For a bug fix, add a regression at the boundary affected: Python validation,
async lifecycle, native requests, or actual wire behavior. Include the trigger,
resulting behavior and relevant test results in the PR description.

While the engine repository is private, CI uses the maintainer's read-only
`GO_ENGINE_SSH_KEY` deploy key. Fork pull requests do not receive that secret and
cannot fetch a private engine. Once the engine is public, an empty SSH key uses
HTTPS and external forks can run the same native job without a secret. Checkout
credentials are not persisted; the workflow uses `pull_request`, not
`pull_request_target`, and has read-only repository permissions. Contributors
should not add credentials to a PR to make a private checkout succeed.

## Compatibility and reports

These projects use independent 0.x versions. Patch releases should preserve
public API and profile semantics except for documented correctness and security
fixes. Planned breaking changes belong in a minor release with migration notes.
An engine adoption updates `engine.lock.json` to an exact reachable commit and
verifies all supported wheels. Incompatible C contract changes require an
explicit ABI revision and coordinated binding changes; ABI 1 must not silently
change incompatibly. Release version and tag policy is in
[packaging and releases](../docs/releases.md#versions-and-source-tags).

Reports should include the Python version, engine commit, ABI, platform and a
minimal reproducer. Use local fixtures and synthetic credentials. Remove proxy
credentials, cookies, personal addresses, connection identifiers, keys and
tickets from captures and logs. Only contribute profiles with a documented
source and permission to redistribute their sanitized protocol configuration.
Retain license notices in source and binary artifacts. Report vulnerabilities
using [SECURITY.md](../SECURITY.md).
