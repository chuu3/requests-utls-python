"""Opt-in live probe; emits fingerprints and checks, never raw service captures.

The profile, baseline and native engine are explicitly supplied artifacts.
Proxy credentials are read only from environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from requests_utls import AsyncSession, Profile, Session


ENDPOINT = "https://tls.peet.ws/api/all"


def request_fields(index, duplicate_cookies=False):
    marker = f"python-probe-{index}"
    a1, a2 = ("x-a", marker + "-first"), ("x-a", marker + "-last")
    b = ("x-b", marker + "-middle")
    cookie = ("cookie", "probe=" + marker)
    if duplicate_cookies:
        second_cookie = ("cookie", "second=" + marker)
        fields = [a1, cookie, a2, second_cookie, b]
        if index % 2:
            order = ["x-b", "cookie", "x-a"]
            expected = [b, cookie, second_cookie, a1, a2]
        else:
            order = ["cookie", "x-a", "x-b", "cookie", "x-a"]
            expected = [cookie, a1, b, second_cookie, a2]
        return {"headers": fields, "headers_order": order}, [
            f"{name}: {value}" for name, value in expected
        ]
    fields = [a1, a2, b]
    if index % 2:
        order, expected = ["cookie", "x-b", "x-a"], [cookie, b, a1, a2]
    else:
        order, expected = ["x-a", "x-b", "x-a", "cookie"], [a1, b, a2, cookie]
    return {
        "headers": fields,
        "headers_order": order,
        "cookies": {"probe": marker},
    }, [f"{name}: {value}" for name, value in expected]


def inspect(response, expected_headers, baseline, payload, duplicate_cookies=False):
    capture = response.json()
    tls, h2 = capture["tls"], capture["http2"]
    fingerprint = {key: tls[key] for key in ("ja3", "ja3_hash", "ja4", "peetprint_hash")}
    fingerprint["akamai_fingerprint"] = h2["akamai_fingerprint"]
    checks = {key: value == baseline[key] for key, value in fingerprint.items()}
    checks["http_200_h2"] = response.status_code == 200 and capture["http_version"] == "h2"
    checks["extension_51764_payload"] = any(
        ("51764" in extension["name"] or "trust_anchor" in extension["name"])
        and extension.get("data", "").lower() == payload.lower()
        for extension in tls["extensions"]
    )
    checks["request_order_duplicates_cookie_isolation"] = any(
        [header for header in frame.get("headers", []) if not header.startswith(":")] == expected_headers
        for frame in h2["sent_frames"] if frame["frame_type"] == "HEADERS"
    )
    result = {"status": response.status_code, "fingerprint": fingerprint, "checks": checks}
    if duplicate_cookies:
        frames = [frame for frame in h2["sent_frames"] if frame["frame_type"] == "HEADERS"]
        expected_cookies = [header for header in expected_headers if header.startswith("cookie:")]
        captured = [[header for header in frame.get("headers", []) if header.startswith("cookie:")]
                    for frame in frames]
        checks["two_separate_cookie_fields"] = any(fields == expected_cookies for fields in captured)
        # Keep only our known probe fields; never save service metadata or IPs.
        result["observed_probe_headers"] = [
            [header for header in frame.get("headers", []) if header in expected_headers]
            for frame in frames
        ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--reference", required=True, help="sanitized expected fingerprint JSON")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--count", type=int, default=2, help="requests per API, sync and async")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--duplicate-cookies", action="store_true", help="send and check two separate Cookie fields")
    args = parser.parse_args()
    if args.count < 1 or args.concurrency < 1:
        parser.error("count and concurrency must be positive")
    profile = Profile.from_file(args.profile)
    baseline = json.loads(Path(args.reference).read_text())
    payload = next(extension["data_hex"] for extension in profile.to_dict()["tls"]["extensions"]
                   if extension.get("id") == 51764)
    proxy = os.environ.get("REQUESTS_UTLS_PROXY")
    username, password = os.environ.get("REQUESTS_UTLS_PROXY_USERNAME"), os.environ.get("REQUESTS_UTLS_PROXY_PASSWORD")
    if (username is None) != (password is None):
        parser.error("set both REQUESTS_UTLS_PROXY_USERNAME and REQUESTS_UTLS_PROXY_PASSWORD")
    options = dict(profile=profile, proxy=proxy, timeout=60, max_unprocessed_retries=8,
                   max_concurrent_requests=args.concurrency)
    if username is not None:
        options["proxy_auth"] = (username, password)

    def checked(index, start, response=None, error=None):
        record = {"id": index, "elapsed_ms": round((time.monotonic() - start) * 1000)}
        if error is not None:
            record["error_type"] = type(error).__name__
        else:
            try:
                record.update(inspect(response, request_fields(index, args.duplicate_cookies)[1],
                                      baseline, payload, args.duplicate_cookies))
            except Exception as exc:
                record["error_type"] = type(exc).__name__
        return record

    with Session(**options) as session:
        profile_hash, limitations = session.profile_hash, session.limitations

        def sync_probe(index):
            start = time.monotonic()
            try:
                return checked(index, start, session.get(ENDPOINT, **request_fields(index, args.duplicate_cookies)[0]))
            except Exception as exc:
                return checked(index, start, error=exc)

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            sync_results = list(pool.map(sync_probe, range(args.count)))

    async def async_probes():
        semaphore = asyncio.Semaphore(args.concurrency)
        async with AsyncSession(**options) as session:
            async def probe(index):
                async with semaphore:
                    start = time.monotonic()
                    try:
                        return checked(index, start, await session.get(ENDPOINT, **request_fields(index, args.duplicate_cookies)[0]))
                    except Exception as exc:
                        return checked(index, start, error=exc)
            return await asyncio.gather(*(probe(index) for index in range(args.count)))

    results = [{"api": api, **record} for api, records in
               (("sync", sync_results), ("async", asyncio.run(async_probes()))) for record in records]
    passed = sum("error_type" not in record and all(record["checks"].values()) for record in results)
    report = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": ENDPOINT, "proxy_used": proxy is not None,
        "profile_hash": profile_hash, "limitations": limitations,
        "requests": len(results), "concurrency_per_api": args.concurrency,
        "timeout_ms": 60000, "max_unprocessed_retries": 8,
        "duplicate_cookie_fields": args.duplicate_cookies,
        "passed": passed, "results": results,
    }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
