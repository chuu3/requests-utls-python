# Contributing

This repository owns the Python API, CFFI binding and wheels. Transport changes
belong in the separate [Go engine](https://github.com/chuu3/requests-utls).

Use Python 3.11+ and an isolated environment. From the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

Follow the [development guide](docs/development.md) for unit-test commands,
Windows setup and full native integration tests. Native tests must use the exact
Go revision/toolchain in `engine.lock.json` and explicitly supplied artifacts.

Add regressions at the affected boundary, preserve sync/async semantics and native
ownership, and report actual validation. Use local peers and synthetic data;
remove credentials and private captures. Document compatibility changes and
retain license notices. See [releases](docs/releases.md) for wheel validation and
[SECURITY.md](SECURITY.md) for confidential reports.

Changes to `main` go through pull requests and must pass required CI checks.
External contributor workflows require maintainer approval to run. Engine
checkouts use public HTTPS and do not require repository secrets. Published
`v*` tags cannot be moved or deleted.

Use `type(scope): description` for commit messages and PR titles, for example
`fix(proxy): preserve timeout classification` or `docs(design): explain cookie ownership`.
Choose the type for the change: `fix`, `feat`, `docs`, `chore`, `build`, `ci`,
`test` or `refactor`. The final merge or squash commit must use the same format.
