# Full changelog

## Unreleased

- Pin the Go engine to `b1311497c6bf72514aadfd3656087619d8485b69` (ABI 1), including x/net v0.59.0, compression updates and phase timeouts.

- Add connection, proxy CONNECT, TLS, response-header and body phase timeouts
  with structured error stages; normalize socket deadline errors consistently.
- Batch ordinary Dependabot updates monthly and refresh compression/CI dependencies.

- Shorten root documentation; move detailed usage, development and release history to `docs/`.

- Clarify that redirect decisions and Cookie state management belong to callers;
  response Cookie parsing and existing request/connection behavior are unchanged.

## 0.2.2 — 2026-09-07

- Normalize request headers in Go after actual protocol selection: HTTP/2 maps
  Host to `:authority`, removes connection-specific and Connection-nominated
  fields, and retains only `TE: trailers`. Other legal duplicates keep their
  ordering. HTTP/1.1 preserves allowed fields and sends Host first unless the
  request's order explicitly places it. Generic validation and unsupported
  HTTP/1.1 framing/upgrade checks remain enforced.
- Bundle Chrome 150 and 152 from a single maintained engine profile index;
  validate every declared profile and its hash while retaining legacy ABI 1
  artifact compatibility. Both profiles can be loaded offline with Profile.builtin.
- Combine multiple Cookie fields with `; ` only after actual HTTP/1.1 selection,
  retaining the first ordered occurrence's spelling and position. HTTP/2 keeps
  separate Cookie fields. Ordering refers to occurrences before combining.
- Normalize malformed headers, proxy authentication, excessive timeouts and
  invalid request encoding inputs to InvalidRequestError. Add regressions for
  asynchronous lifecycle and certificate trust through H1/H2 CONNECT routes.
- Handle generated H1 field ordering after ALPN when the peer selects H2.
- Run Linux native integration tests on pushes and pull requests using the exact
  engine lock, alongside the Python 3.11–3.14 unit matrix. Include validation and
  async lifecycle tests in the unit job; public engine checkouts need no secret.
- Document contributions, compatibility and source tags, confidential security
  reporting, and Charles proxy/certificate behavior. Retain historical evidence
  and record the published 0.2.1 wheel checks separately.
- Bound automatic response decoding to four non-identity layers in the updated
  engine, while retaining limits on encoded, intermediate and final body bytes.

This release bundles Go engine `bfa312493c7fc8f5843444757aa627b91fe2c561`
and retains ABI version 1. Protocol normalization is implemented in Go; the
Python request API remains unchanged.

## 0.2.1

- Calculate request Content-Length from the final body bytes for HTTP/1.1 and
  HTTP/2, replacing a supplied value while preserving its position and HTTP/1.1
  field name spelling. Multiple Content-Length fields remain invalid.
- Include automatically generated Content-Length in request-level
  `headers_order`, even when absent from the supplied headers. Nonempty bodies
  and POST/PUT/PATCH requests receive a length, including zero for empty bodies.
- Verify ordering among repeated cookie fields, connection reuse, and concurrent
  requests without modifying caller headers or Session defaults. HTTP/1.1 keeps
  persistent connections by default without injecting a Connection header;
  explicitly supplied Connection fields follow `headers_order`.

This release bundles the updated engine and retains ABI version 1.

## 0.2.0

- Decode gzip, zlib/raw deflate, Brotli and Zstandard responses by default in the
  native engine, including stacked codings. Limits cover encoded, decoded and
  intermediate bytes. `decode_content=False` returns original bytes;
  `Response.decoded` identifies decoded bodies without replacing wire headers.
- Expose independent immutable `Response.cookies`, including attributes and
  same-name cookies with different domains or paths. Responses never mutate a
  shared Session cookie jar.
- Add `random_ja3=False` and `force_http1=False`. Extension shuffling applies to
  new connections; open connections retain their original handshake.
- Support HTTP/1.1 with automatic ALPN fallback, ordered duplicate fields,
  original field name spelling, authenticated CONNECT, concurrent pooling and
  TLS ticket resumption. Request-level header order remains isolated.
- Expand Peet import with the IANA cipher registry, numeric cipher IDs,
  additional typed and explicitly opted-in static extensions, HTTP/1 captures,
  preserved repeated signatures, and corrections for historical Peet Ed448
  labels and padding length units. Algorithm advertisement does not implement
  cryptography missing from uTLS; profile limitations remain explicit.
- Add the [bulk profile acceptance command](../docs/profile-acceptance.md), which
  checks cold handshakes, complete TLS fingerprints and stable extension data.
  Captured PSK state is excluded; contradictory legacy JA4 values remain failed
  comparisons with a documented cause.

Existing sessions must use the updated bundled engine. This release retains ABI
version 1 and adds JSON configuration/response fields that old engines reject.

## 0.1.0

Initial public platform wheels with the concurrent Go engine, HTTP/2 duplicate
headers, explicit authenticated proxies, immutable profiles and TLS resumption.
