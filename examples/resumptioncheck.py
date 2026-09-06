"""Opt-in sequential connection-reuse/PSK probe with sanitized output.

Use REQUESTS_UTLS_LIBRARY and optionally REQUESTS_UTLS_PROXY plus
REQUESTS_UTLS_PROXY_USERNAME / REQUESTS_UTLS_PROXY_PASSWORD. The profile and
reference are explicit artifacts; no Go checkout is located automatically.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from requests_utls import Profile, Session


ENDPOINT = "https://tls.peet.ws/api/all"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = Profile.from_file(args.profile)
    reference = json.loads(Path(args.reference).read_text())
    cold_ja3 = reference["ja3"]
    parts = cold_ja3.split(",")
    parts[2] += "-41"
    psk_ja3 = ",".join(parts)
    proxy = os.environ.get("REQUESTS_UTLS_PROXY")
    username = os.environ.get("REQUESTS_UTLS_PROXY_USERNAME")
    password = os.environ.get("REQUESTS_UTLS_PROXY_PASSWORD")
    if (username is None) != (password is None):
        parser.error("set both proxy username and password environment variables")
    options = dict(profile=profile, proxy=proxy, timeout=60, max_unprocessed_retries=8)
    if username is not None:
        options["proxy_auth"] = (username, password)
    results = []
    for enabled in (True, False):
        previous_random = None
        with Session(**options, session_resumption=enabled) as session:
            for index in range(2):
                start = time.monotonic()
                result = {"session_resumption": enabled, "request_index": index}
                try:
                    response = session.get(ENDPOINT, headers=[("x-probe", f"resume-{enabled}-{index}")])
                    capture = response.json()
                    tls, h2 = capture["tls"], capture["http2"]
                    extensions = tls["extensions"]
                    is_psk = lambda extension: "pre_shared_key" in extension["name"] or "(41)" in extension["name"]
                    offered = any(is_psk(extension) for extension in extensions)
                    client_random = tls.get("client_random")
                    same = (client_random == previous_random) if client_random and previous_random else None
                    expected = psk_ja3 if offered else cold_ja3
                    checks = {
                        "http_200_h2": response.status_code == 200 and capture["http_version"] == "h2",
                        "expected_ja3_extension_sequence": tls["ja3"] == expected,
                        "ja3_hash": tls["ja3_hash"] == hashlib.md5(expected.encode()).hexdigest(),
                        "http2_fingerprint": h2["akamai_fingerprint"] == reference["akamai_fingerprint"],
                        "psk_is_last_when_offered": not offered or is_psk(extensions[-1]),
                        "first_request_is_cold": index != 0 or not offered,
                        "disabled_never_offers_psk": enabled or not offered,
                    }
                    result.update({
                        "status": response.status_code, "same_connection_as_previous": same,
                        "pre_shared_key_offered": offered, "checks": checks,
                        "fingerprint": {key: tls[key] for key in ("ja3", "ja3_hash", "ja4", "peetprint_hash")},
                    })
                    previous_random = client_random
                except Exception as error:
                    result["error_type"] = type(error).__name__
                result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
                results.append(result)
    passed = sum("error_type" not in result and all(result["checks"].values()) for result in results)
    report = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": ENDPOINT, "proxy_used": proxy is not None,
        "requests": len(results), "passed": passed,
        "profile_hash": session.profile_hash,
        "demonstrated_psk_on_reconnect": any(result.get("pre_shared_key_offered")
            and result.get("same_connection_as_previous") is False for result in results),
        "server_acceptance": "The endpoint exposes the client offer, not server DidResume; actual acceptance is verified by local TLS server tests.",
        "connection_comparison": "Compare client_random in memory only; raw randoms, tickets, IPs and credentials are omitted.",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, indent=2) + "\n"
    args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
