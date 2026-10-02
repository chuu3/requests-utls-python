# Changelog

## 0.3.1 — 2026-10-02

- Fix proxy CONNECT failures being classified as invalid responses when a socket
  deadline fires before the context timer. They now consistently raise Timeout
  with stage `proxy_connect`; raw proxy response text remains suppressed.
- Fetch the public Go engine without a deploy key, including in fork PR checks.
- Bundle engine `8fee11c9e4579426ae059e6d92825972aeba11bb` with clarified third-party notices.
- No public API, TLS profile or timeout-default changes. Native ABI remains 1.

## 0.3.0 — 2026-10-02

- Add `connect_timeout`, `proxy_connect_timeout`, `tls_handshake_timeout`,
  `response_header_timeout` and `body_timeout` to Session and AsyncSession.
- Expose `stage` and `elapsed_ms` on request errors; consistently classify socket
  deadline failures as Timeout and isolate HTTP/2 header/body timeouts per stream.
- Connection setup now uses separate 10-second connect, proxy CONNECT and TLS
  budgets instead of one combined budget. The existing total request timeout
  still bounds all phases; `timeout=None` does not disable these phase defaults.
  Header/body phase limits remain disabled by default.
- Bundle engine `2d2ffad802b09954d88a946c93689cc55e1eb4d0`, with x/net v0.59.0,
  updated compression libraries and unchanged ABI 1.
- Batch ordinary dependency updates monthly, refresh pinned CI actions and
  simplify documentation. Redirects and Cookie state remain caller-owned.

See [timeout semantics](docs/timeouts.md) for defaults and error stage definitions.

## 0.2.2

- Protocol-aware Host and Cookie handling, ordered headers and Chrome 150/152 profiles.
- Improved validation, native CI and bounded response decoding.
- Bundles engine `bfa312493c7fc8f5843444757aa627b91fe2c561`, ABI 1.

See the [full changelog](docs/changelog.md) for release details and earlier versions.
