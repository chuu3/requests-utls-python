# Packaging and releases

The Python package can be distributed publicly on PyPI while the Python and Go
GitHub repositories remain private. After publication, end users install with
`python -m pip install requests-utls` and
do not need credentials or a Go toolchain. The installed wheel contains the
Python client, the platform's Go shared library, Chrome 150 and 152 profiles, an
`engine.json` manifest, and the engine and dependency licenses.

## Independent engine versions

`engine.lock.json` pins the separate Go repository, full commit SHA, exact Go
toolchain, and ABI. The release workflow checks out that revision and invokes
the Go project's `scripts/pack_native.py`. Go source is never vendored into the
Python source tree or shipped in wheels. Python and Go versions may evolve
independently as long as their ABI remains compatible.

For a private engine repository, the Python repository needs the Actions secret
`GO_ENGINE_SSH_KEY`: a dedicated SSH deploy key with read-only access to the Go
repository. The workflow disables persisted checkout credentials. This key is
only for build-time source access; users never need it. Once the engine is
public, checkouts with no SSH key use HTTPS. External fork PRs can then run the
ordinary Linux native integration job without repository secrets. While the
engine remains private, a fork PR without that key cannot fetch it.

## Platform wheels

The manually dispatched `release.yml` workflow builds five wheels:

| Platform | Build and audit |
| --- | --- |
| Linux x86_64 | manylinux 2.28 container, auditwheel |
| Linux ARM64 | native ARM64 runner, manylinux 2.28 container, auditwheel |
| macOS Apple Silicon | native runner, macOS 13 deployment target, delocate |
| macOS Intel | native runner, macOS 13 deployment target, delocate |
| Windows x64 | MinGW-w64 UCRT compiler, delvewheel |

Wheel tags use `py3-none-<platform>` because Python uses the stable C interface
through CFFI, rather than a CPython-specific extension ABI. They install into
platlib and must never carry `py3-none-any`. Linux wheels are repaired against
the glibc 2.28 policy before distribution. A pure-Python wheel or sdist is not
published: no installation hook downloads or builds a missing engine.

The build backend checks manifest ABI, binary architecture, deployment target,
payload paths and hashes. Native packaging records linked-module licenses and
system dependencies. After dependency repair, `scripts/finalize_wheel.py`
refreshes binary hashes, compatibility tags and the wheel RECORD so the
manifest describes the bytes actually shipped.

Each wheel is installed into a fresh virtual environment with no
`REQUESTS_UTLS_LIBRARY` override. The full test suite exercises the bundled
engine, HTTPS/HTTP2, repeated headers, concurrency, authenticated proxies,
connection reuse and session resumption against an independent local Go test
peer. Both built-in profiles are also tested. The test peer is not included in the
wheel. All five jobs must pass before publishing can begin.

For a local build, put the pinned Go toolchain and the platform's C compiler on
PATH, install `build`, `twine` and the platform's audit tool, and use a clean
checkout of the Go commit in `engine.lock.json`:

```sh
python scripts/ci_wheel.py build \
  --engine-dir /absolute/path/to/requests-utls \
  --output-dir /absolute/path/to/new-wheel-output \
  --platform macosx_13_0_arm64
```

Use a fresh output directory. The output wheel is under `wheelhouse/`. Linux
builds must run inside the corresponding manylinux container as in the workflow.

## PyPI Trusted Publishing

Configure a PyPI pending publisher for the first release at
<https://pypi.org/manage/account/publishing/> with these exact values:

| Field | Value |
| --- | --- |
| PyPI project | `requests-utls` |
| GitHub owner | `chuu3` |
| Repository | `requests-utls-python` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

The GitHub environment `pypi` must exist. Publication uses GitHub OIDC and PyPI
Trusted Publishing; no long-lived PyPI API token belongs in this repository.
Pending publisher configuration does not reserve a project name; the first
successful upload creates it.

Dispatch **Build and publish bundled wheels** from `main` with `publish=false`
to build, audit and test without uploading. The five wheels remain downloadable
as Actions artifacts subject to the repository's visibility. After configuring the PyPI publisher, dispatch
with `publish=true`. The publishing job downloads exactly five tested wheels,
validates their versions and platforms, runs strict Twine checks, and uploads
them with attestations. It does not publish GitHub repositories or source
archives.

## Versions and source tags

For subsequent releases, update the Python version in `pyproject.toml` and
`src/requests_utls/__init__.py`; update `engine.lock.json` when adopting a new
engine revision. The native artifact's
release label follows the Python package version in `scripts/ci_wheel.py`; the
exact engine identity remains its independent commit SHA. A published PyPI
file cannot be overwritten: fixes need a new package version.

When preparing a release, update the README's published-version and builtin
availability notes as well as the changelog. Profiles declared by the engine's
`profiles/builtin.json` are copied into its artifact and listed in
`engine.json`; all declared profiles must survive wheel repair with valid hashes.

Patch releases in the 0.x series preserve public API and profile semantics except
for documented correctness and security fixes. Planned breaking changes require
a minor version and migration notes. ABI-incompatible engine changes require an
explicit ABI revision and coordinated Python binding support; an engine version
label alone is not an ABI compatibility guarantee.

For each new release, retain the tested Python commit and exact engine pin in
the release notes. After the five-platform build and publication succeed, create
an annotated `vX.Y.Z` tag at that tested Python commit, rather than whatever
`main` points to later. Tags are maintained separately from the manually
dispatched publishing workflow; creating a tag does not publish to PyPI. Do not
move or reuse a published version tag. The Go project may maintain its own
version tags independently; `engine.lock.json` continues to pin its full commit.

Historical release evidence may refer to commits that predate this tag policy.
Do not infer that a historical source tag exists from a PyPI version number.
See [verification](verification.md) for the recorded 0.2.1 source identities and
installed-wheel results, and [Contributing](../CONTRIBUTING.md) for source tests.
