# Profile acceptance against tls.peet.ws

The acceptance script imports a directory of tls.peet.ws captures and performs
an actual request for every eligible profile. It compares the returned TLS
fingerprints and stable ClientHello fields with the original capture. A
successful import alone does not establish that a handshake works or that its
fingerprint matches.

## Version 0.2.0 live acceptance

The [unchanged strict report](profile-acceptance-0.2.0.json) covers 1,010 source
files with one fixed native artifact and comparison script:

| Strict outcome | Files |
| --- | ---: |
| `passed` | 575 |
| `fingerprint_mismatch` | 35 |
| `excluded_resumption` | 399 |
| `invalid_capture` | 1 |

All 610 valid, non-resumption captures completed an HTTP 200 request: 566 over
HTTP/2 and 44 over HTTP/1.1. The invalid capture has no usable TLS cipher list.
The 35 mismatches all retain equal JA3, PeetPrint and every other strict
static-field/vector check; their only differing fields are JA4 and JA4 raw.

The [independent padding audit](profile-acceptance-padding-audit-0.2.0.json)
recomputes both the historical and current JA4 calculations for all 35, keeping
each source/observed prefix unchanged. All raw vectors and hashes match those
two calculations exactly. The differing result is explained by Peet's historical
removal of padding from the JA4 extension hash, documented below.

The frozen comparator automatically annotated 23 of these failures. The other
12 have additional Peet prefix-format deviations, present identically in the
source and observed fingerprints, so the deliberately strict diagnostic did
not annotate them. Nine lack the required `00` for absent ALPN; the other three
were rejected because their prefix claims SNI despite its absence. Across those
12, five have that SNI issue and four have an extension count without the
required leading zero; these categories overlap.

The independent audit explains the failures; **the strict result remains 575
passed and 35 mismatched**. It neither rewrites the original report nor claims
that an inconsistent historical JA4 value was reproduced by the current server.
The audit binds the exact strict-report SHA-256 and each source-file SHA-256,
and distinguishes its recomputed fingerprint evidence from the static-field
checks inherited from that strict report.

Reproduce it with the same source directories:

```sh
python examples/audit_peet_padding_baselines.py \
  /absolute/path/to/browser-profiles \
  /absolute/path/to/other-captures \
  --report docs/profile-acceptance-0.2.0.json \
  --output /absolute/path/to/reproduced-padding-audit.json
```

This audit is offline. It fails on changed source digests, additional strict
check failures, changed prefixes, or raw/hash differences beyond the exact
padding algorithm change. Its successful exit code means the historical
explanation was verified; it does not replace the strict acceptance exit code.

## Run a complete acceptance

Install the Python client and use either its bundled engine or an explicitly
selected shared library. Run the script from the Python repository:

```sh
# Optional: use a particular native artifact instead of the installed library.
export REQUESTS_UTLS_LIBRARY=/absolute/path/to/librequests_utls.dylib

# Optional: use an HTTP CONNECT proxy.
export REQUESTS_UTLS_PROXY=http://proxy.example:8080
# Set REQUESTS_UTLS_PROXY_USERNAME and REQUESTS_UTLS_PROXY_PASSWORD together
# in the environment when the proxy requires authentication.

python examples/profile_acceptance.py \
  /absolute/path/to/browser-profiles \
  /absolute/path/to/other-captures \
  --output dist/profile-acceptance.json \
  --concurrency 4 \
  --attempts 3 \
  --timeout 45
```

Use the native library filename for the platform. The script recursively selects
JSON files in each supplied directory. `--select` accepts a regular expression
over the reported relative file identifiers. `--concurrency` is between 1 and
32; `--attempts` is between 1 and 10. Timeout values are seconds per attempt.

Each attempt uses a separate Session with `random_ja3=False` and
`session_resumption=False`. Requests go to `https://tls.peet.ws/api/all`.
Transport failures can be retried; a completed fingerprint comparison is
recorded immediately, including a mismatch. The script does not keep retrying a
mismatch to search for a favorable fingerprint.

The output is a JSON report and a sibling `.jsonl` journal, flushed after each
completed file. Reports retain file identifiers, source-file SHA-256 values,
checks, fingerprints and classified failures. Captured request headers, cookies,
IP addresses, random values, key material and tickets are not copied into them.
Stable raw extension payload differences are represented by SHA-256 values.

`--import-only` checks profile import and reports `imported`; it does not make a
request and is not live acceptance.

## Which captures can be replayed

The script excludes captures containing `pre_shared_key` (41), `early_data`
(42), or a nonempty captured session ticket (35). Their previous connection's
secret state is not replayable in a fresh Session. An empty session-ticket
advertisement is allowed. `psk_key_exchange_modes` (45) and `key_share` (51)
alone do not make a capture resumed.

The engine also rejects captures containing a retry cookie (44); these are
reported as import failures instead of being presented as successful initial
handshakes. No captured keys, PSK identities, binders, session tickets or retry
cookies are sent.

An empty or null cipher list is `invalid_capture`. Unreadable, malformed or
unsupported captures cannot establish a match; parse/import failures remain
failures. Missing JA3, JA3 hash, JA4, JA4 raw, PeetPrint or PeetPrint hash prevents
a passing comparison.

## What equality means

All six fingerprint fields must match exactly. The script also compares:

- Cipher ordering and extension ordering.
- Advertised signature algorithms, supported groups and versions, ALPN/ALPS,
  point formats, certificate compression and PSK mode.
- Stable status-request fields, record-size limits and padding lengths when
  provided by the capture.
- Key-share group ordering, excluding newly generated key bytes.
- Fixed extension payloads, including extension 51764.

GREASE values are normalized to a common marker while their positions and
occurrences remain significant. Real algorithm IDs are retained. Dynamic SNI,
key bytes and ECH ciphertext are not treated as fixed payloads.

`allow_opaque=True` permits the engine's documented advertisement-only
limitations during import. A matching fingerprint verifies what was sent; it
does not demonstrate implementation of every extension's later protocol
behavior.

## Historical Peet padding discrepancy

JA4 includes padding extension 21 (`0015`) in its extension hash. Its documented
exclusions are GREASE, SNI (`0000`) and ALPN (`0010`); the official example
explicitly retains `0015`.
[JA4 extension-hash specification](https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md#extension-hash)

TrackMe previously also excluded `0015`. The official
[PR #40](https://github.com/pagpeter/TrackMe/pull/40) corrected that error and was
merged on 2026-08-26 as
[commit c302585](https://github.com/pagpeter/TrackMe/commit/c3025853931c1eba451c7f0bd06e5d59a38241fd).
The merge date does not establish the website's deployment date.

For a known historical capture, the old calculation produces
`t13d1516h2_8daaf6152771_f37e75b10bcc`; retaining the same JA3 and PeetPrint under
the corrected calculation produces
`t13d1516h2_8daaf6152771_e5627efa2ab1`. TrackMe derives the extension and
signature input directly from those two fingerprints.
[Pinned calculation source](https://github.com/pagpeter/TrackMe/blob/c3025853931c1eba451c7f0bd06e5d59a38241fd/pkg/tls/ja4.go#L57-L73)

The acceptance script adds
`baseline_issue="legacy_peet_ja4_omits_padding"` only when every other check
passes and the two JA4 differences are fully explained by that exact change.
It reconstructs both calculations from the common JA3 and PeetPrint, verifies
the raw vectors and hashes, and rejects the diagnosis if anything else differs.
The record includes `baseline_ja4_calculated_legacy` and
`baseline_ja4_calculated_current`.

This diagnosis **does not change acceptance**: the outcome remains
`fingerprint_mismatch`, `checks.ja4` and `checks.ja4_r` remain false, and the
process exits unsuccessfully. `baseline_issue_counts` summarizes these
diagnoses as a subset of failed comparisons. Source captures are never rewritten.

Peet's `padding_data_length` has a separate unit issue: it is the number of
hexadecimal characters in the extension payload, twice the number of wire
bytes. For example, 402 represents 201 padding bytes. The engine's Peet importer
converts that length; ordinary native profiles use byte lengths.
[Hex-length parsing](https://github.com/pagpeter/TrackMe/blob/c3025853931c1eba451c7f0bd06e5d59a38241fd/pkg/tls/parse_client_hello.go#L160-L165),
[padding output field](https://github.com/pagpeter/TrackMe/blob/c3025853931c1eba451c7f0bd06e5d59a38241fd/pkg/tls/parse_client_hello.go#L367-L375)

## Repeatability and interpreting outcomes

Reports include `native_library_sha256` and `comparator_sha256`. A retry can
retain earlier passed/excluded records only when the native artifact, comparison
script and individual source file are unchanged:

```sh
python examples/profile_acceptance.py /absolute/path/to/browser-profiles \
  --retry-report dist/profile-acceptance.json \
  --output dist/profile-acceptance-retry.json
```

Use the same source directories and selection for a complete retry. After an
engine or script change, run a fresh acceptance. Diagnosed historical
discrepancies remain mismatches and are not retained as passed records.

| Outcome | Meaning |
| --- | --- |
| `passed` | Actual request completed and every comparison matched. |
| `excluded_resumption` | Captured connection state cannot be replayed as a fresh handshake. |
| `invalid_capture` | Required TLS capture data is empty or missing. |
| `import_failed` | Capture parsing or native profile validation failed. |
| `request_failed` | No attempt produced a completed comparison. |
| `fingerprint_mismatch` | An actual response was compared and at least one check failed. |
| `imported` | Import-only mode succeeded; no handshake was tested. |

Any invalid capture, import/request failure or fingerprint mismatch produces
exit status 1. Neither an exclusion nor a diagnostic annotation is evidence of
a matching handshake. Live outcome counts belong in the report from a fixed
artifact and comparator, rather than in this process definition.
