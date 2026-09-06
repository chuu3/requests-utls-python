# Changelog

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
- Add the [bulk profile acceptance command](docs/profile-acceptance.md), which
  checks cold handshakes, complete TLS fingerprints and stable extension data.
  Captured PSK state is excluded; contradictory legacy JA4 values remain failed
  comparisons with a documented cause.

Existing sessions must use the updated bundled engine. This release retains ABI
version 1 and adds JSON configuration/response fields that old engines reject.

## 0.1.0

Initial public platform wheels with the concurrent Go engine, HTTP/2 duplicate
headers, explicit authenticated proxies, immutable profiles and TLS resumption.
