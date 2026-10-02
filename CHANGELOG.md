# Changelog

## Unreleased

- Pin the Go engine to `b1311497c6bf72514aadfd3656087619d8485b69` (ABI 1), including x/net v0.59.0, compression updates and phase timeouts.

- Add connection, proxy CONNECT, TLS, response-header and body phase timeouts
  with structured error stages; normalize socket deadline errors consistently.
- Batch ordinary Dependabot updates monthly and refresh compression/CI dependencies.

- Shorten root documentation; move detailed usage, development and release history to `docs/`.

- Clarify caller-owned redirects and Cookie state management; runtime behavior is unchanged.

## 0.2.2

- Protocol-aware Host and Cookie handling, ordered headers and Chrome 150/152 profiles.
- Improved validation, native CI and bounded response decoding.
- Bundles engine `bfa312493c7fc8f5843444757aa627b91fe2c561`, ABI 1.

See the [full changelog](docs/changelog.md) for release details and earlier versions.
