"""Cold-handshake acceptance of a directory of tls.peet.ws captures.

Each file receives its own Session, with random_ja3 and ticket reuse disabled.
Reports contain fingerprints and failure classifications, never raw captures,
cookies, proxy credentials, IP addresses, randoms or session tickets.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

from requests_utls import Profile, Session

ENDPOINT = "https://tls.peet.ws/api/all"
FINGERPRINTS = ("ja3", "ja3_hash", "ja4", "ja4_r", "peetprint", "peetprint_hash")


def extension_id(extension):
    name = extension.get("name", "")
    if "GREASE" in name.upper():
        return "GREASE"
    match = re.search(r"(?:\((\d+)\)|\bextension\s+(\d+))$", name, re.I)
    return int(match[1] or match[2]) if match else name


def grease(value):
    if isinstance(value, str):
        if re.fullmatch(r"0x[0-9a-fA-F]{1,4}", value):
            number = int(value, 16)
            if number & 0x0F0F == 0x0A0A and number >> 8 == number & 0xFF:
                return "GREASE"
        return re.sub(r"(?:TLS_)?GREASE(?: \(0x[0-9a-fA-F]+\))?", "GREASE", value)
    if isinstance(value, list):
        return [grease(item) for item in value]
    return value


def resumed_capture(capture):
    for extension in capture.get("tls", {}).get("extensions", []):
        eid = extension_id(extension)
        if eid in (41, 42):
            return "captured_psk" if eid == 41 else "captured_early_data"
        if eid == 35 and any(extension.get(key) for key in ("data", "session_ticket", "ticket")):
            return "captured_session_ticket"
    return None


def comparisons(expected, observed):
    checks, differences = {}, {}
    old, new = expected["tls"], observed["tls"]
    checks["complete_fingerprint_baseline"] = all(isinstance(old.get(key), str) and old[key] for key in FINGERPRINTS)
    for key in FINGERPRINTS:
        # Absent baseline fields cannot substantiate an equality claim.
        if key not in old:
            continue
        checks[key] = key in new and old[key] == new[key]
        if not checks[key]:
            differences[key] = {"expected": old[key], "actual": new.get(key)}
    checks["cipher_order"] = grease(old.get("ciphers")) == grease(new.get("ciphers"))
    before = [extension_id(ext) for ext in old.get("extensions", [])]
    after = [extension_id(ext) for ext in new.get("extensions", [])]
    checks["extension_order"] = before == after
    if not checks["extension_order"]:
        differences["extension_order"] = {"expected": before, "actual": after}
    # Compare stable advertised vectors in addition to the fingerprint hashes.
    fields = ("signature_algorithms", "signature_hash_algorithms", "algorithms",
              "elliptic_curves_point_formats", "PSK_Key_Exchange_Mode",
              "protocols", "versions", "supported_groups", "status_request",
              "record_size_limit", "padding_data_length")
    vectors = True
    for index, ext in enumerate(old.get("extensions", [])):
        if index >= len(new.get("extensions", [])) or extension_id(ext) != extension_id(new["extensions"][index]):
            vectors = False
            continue
        actual = new["extensions"][index]
        for field in fields:
            if field in ext and grease(ext[field]) != grease(actual.get(field)):
                vectors = False
                differences[f"extension_{index}_{field}"] = {"expected": grease(ext[field]), "actual": grease(actual.get(field))}
        if "shared_keys" in ext:
            # Key bytes must be newly generated; only group sequence is stable.
            keys_before = [grease(next(iter(key))) for key in ext["shared_keys"]]
            keys_after = [grease(next(iter(key))) for key in actual.get("shared_keys", [])]
            if keys_before != keys_after:
                vectors = False
                differences[f"extension_{index}_key_share_groups"] = {"expected": keys_before, "actual": keys_after}
        if extension_id(ext) == 51764 and "data" in ext:
            checks["extension_51764_payload"] = ext["data"].lower() == actual.get("data", "").lower()
            if not checks["extension_51764_payload"]:
                differences["extension_51764_payload"] = {"expected_sha256": hashlib.sha256(ext["data"].lower().encode()).hexdigest(),
                                                          "actual_sha256": hashlib.sha256(actual.get("data", "").lower().encode()).hexdigest()}
        elif "data" in ext and extension_id(ext) not in ("GREASE", 0, 35, 41, 42, 44, 51, 65037):
            # Static extension bytes supplement hashes which often encode only
            # an extension ID. Never compare freshly generated key/ECH bytes.
            same = ext["data"].lower() == actual.get("data", "").lower()
            checks[f"extension_{index}_static_payload"] = same
            if not same:
                differences[f"extension_{index}_static_payload"] = {"expected_sha256": hashlib.sha256(ext["data"].lower().encode()).hexdigest(),
                                                                    "actual_sha256": hashlib.sha256(actual.get("data", "").lower().encode()).hexdigest()}
    checks["extension_vectors"] = vectors
    return checks, differences


def diagnose_baseline(expected, observed, checks, differences):
    """Explain the exact historical Peet padding bug without relaxing a check.

    TrackMe PR #40 removed its erroneous exclusion of 0015 from JA4_c. Rebuild
    both b/c sections from the otherwise identical JA3 and PeetPrint, keeping
    the identical a section. A resemblance or an unrelated JA4 mismatch is not
    enough to attribute a failure to that historical implementation.
    """
    required = {"complete_fingerprint_baseline", "cipher_order", "extension_order",
                "extension_vectors", *FINGERPRINTS}
    if not required.issubset(checks) or set(differences) != {"ja4", "ja4_r"}:
        return {}
    if checks["ja4"] is not False or checks["ja4_r"] is not False:
        return {}
    if any(value is not True for key, value in checks.items() if key not in ("ja4", "ja4_r")):
        return {}
    try:
        old, new = expected["tls"], observed["tls"]
        if old["ja3"] != new["ja3"] or old["peetprint"] != new["peetprint"]:
            return {}
        # Also reject internally inconsistent source hashes, even if the same
        # incorrect hash was present on both sides of the comparison.
        for key in ("ja3", "peetprint"):
            digest = hashlib.md5(old[key].encode("ascii"), usedforsecurity=False).hexdigest()
            if old[key + "_hash"] != digest or new[key + "_hash"] != digest:
                return {}
        ja3 = old["ja3"].split(",")
        peetprint = old["peetprint"].split("|")
        if len(ja3) != 5 or len(peetprint) != 8:
            return {}
        ciphers = [int(value) for value in ja3[1].split("-")]
        extensions = [int(value) for value in ja3[2].split("-")]
        signatures = [int(value) for value in peetprint[3].split("-") if value != "GREASE"]
        if 21 not in extensions or not signatures:
            return {}
        if any(not 0 <= value <= 65535 for value in (*ciphers, *extensions, *signatures)):
            return {}
        # JA3 has already excluded GREASE. Do not diagnose malformed baselines.
        if any(value & 0x0F0F == 0x0A0A and value >> 8 == value & 0xFF for value in (*ciphers, *extensions)):
            return {}
        source_raw, current_raw = old["ja4_r"].split("_"), new["ja4_r"].split("_")
        if len(source_raw) != 4 or len(current_raw) != 4 or source_raw[0] != current_raw[0]:
            return {}
        prefix = source_raw[0]
        if not re.fullmatch(r"t(?:13|12|11|10|s3|s2|00)[di][0-9]{4}[A-Za-z0-9]{2}", prefix):
            return {}
        if int(prefix[4:6]) != min(len(ciphers), 99) or int(prefix[6:8]) != min(len(extensions), 99):
            return {}
        if prefix[3] != ("d" if 0 in extensions else "i"):
            return {}
        cipher_raw = ",".join(sorted(f"{value:04x}" for value in ciphers))
        signature_raw = ",".join(f"{value:04x}" for value in signatures)
        old_extensions = ",".join(sorted(f"{value:04x}" for value in extensions if value not in (0, 16, 21)))
        new_extensions = ",".join(sorted(f"{value:04x}" for value in extensions if value not in (0, 16)))
        if source_raw != [prefix, cipher_raw, old_extensions, signature_raw]:
            return {}
        if current_raw != [prefix, cipher_raw, new_extensions, signature_raw]:
            return {}
        cipher_hash = hashlib.sha256(cipher_raw.encode("ascii")).hexdigest()[:12]
        legacy_hash = hashlib.sha256((old_extensions + "_" + signature_raw).encode("ascii")).hexdigest()[:12]
        current_hash = hashlib.sha256((new_extensions + "_" + signature_raw).encode("ascii")).hexdigest()[:12]
        legacy_ja4 = f"{prefix}_{cipher_hash}_{legacy_hash}"
        current_ja4 = f"{prefix}_{cipher_hash}_{current_hash}"
        if old["ja4"] != legacy_ja4 or new["ja4"] != current_ja4:
            return {}
        return {"baseline_issue": "legacy_peet_ja4_omits_padding",
                "baseline_ja4_calculated_legacy": legacy_ja4,
                "baseline_ja4_calculated_current": current_ja4}
    except (KeyError, TypeError, ValueError, UnicodeError):
        return {}


def error_category(exc):
    # Match error classes without persisting endpoints, credentials or peer data.
    message = str(exc).lower()
    for token, category in (("proxy", "proxy_error"), ("timeout", "timeout"),
                            ("deadline", "timeout"), ("goaway", "goaway"),
                            ("certificate", "certificate_error"), ("handshake", "handshake_error"),
                            ("cipher", "cipher_error"), ("extension", "extension_error"),
                            ("eof", "eof"), ("connection", "connection_error")):
        if token in message:
            return category
    return type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--select", help="regular expression applied to relative file identifiers")
    parser.add_argument("--retry-report", type=Path, help="only rerun previously failed files, retaining passed/excluded records")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 32 or not 1 <= args.attempts <= 10 or args.timeout <= 0:
        parser.error("invalid concurrency, attempts or timeout")
    options = dict(proxy=os.environ.get("REQUESTS_UTLS_PROXY"), timeout=args.timeout,
                   random_ja3=False, session_resumption=False, max_unprocessed_retries=8)
    username, password = os.environ.get("REQUESTS_UTLS_PROXY_USERNAME"), os.environ.get("REQUESTS_UTLS_PROXY_PASSWORD")
    if (username is None) != (password is None):
        parser.error("set both proxy authentication variables")
    if username is not None:
        options["proxy_auth"] = username, password
    files = []
    for directory in args.directories:
        if not directory.is_dir():
            parser.error(f"missing directory: {directory}")
        for path in sorted(directory.rglob("*.json")):
            identifier = directory.parent.name + "/" + directory.name + "/" + path.relative_to(directory).as_posix()
            if not args.select or re.search(args.select, identifier):
                files.append((identifier, path))
    previous = {}
    library = os.environ.get("REQUESTS_UTLS_LIBRARY")
    if not library:
        import requests_utls
        manifest_path = Path(requests_utls.__file__).parent / "engine.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            library = str(manifest_path.parent / manifest["library"])
    engine_sha = hashlib.sha256(Path(library).read_bytes()).hexdigest() if library else None
    comparator_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.retry_report:
        old = json.loads(args.retry_report.read_text())
        if not engine_sha or old.get("native_library_sha256") != engine_sha or old.get("comparator_sha256") != comparator_sha:
            parser.error("retry report must use the same native binary and comparison script; run a fresh acceptance after changes")
        previous = {record["file"]: record for record in old["results"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    journal = args.output.with_suffix(".jsonl")

    def probe(item):
        identifier, path = item
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        prior = previous.get(identifier)
        if prior and prior.get("sha256") == digest and prior["outcome"] in ("passed", "excluded_resumption"):
            return {**prior, "retained_from_previous_report": True}
        record = {"file": identifier, "sha256": digest}
        started = time.monotonic()
        try:
            capture = json.loads(raw)
            if not capture.get("tls", {}).get("ciphers"):
                return {**record, "outcome": "invalid_capture", "reason": "missing_tls_cipher_list"}
            reason = resumed_capture(capture)
            if reason:
                return {**record, "outcome": "excluded_resumption", "reason": reason}
            profile = Profile.from_peet(capture, allow_opaque=True)
        except Exception as exc:
            return {**record, "outcome": "import_failed", "error_type": type(exc).__name__, "error_category": error_category(exc)}
        if args.import_only:
            return {**record, "outcome": "imported"}
        attempts = []
        for attempt in range(args.attempts):
            try:
                with Session(profile=profile, **options) as session:
                    response = session.get(ENDPOINT, headers=[("accept", "application/json")])
                    record["profile_hash"] = session.profile_hash
                    if response.status_code != 200:
                        attempts.append({"error_category": "http_status", "status_code": response.status_code})
                        continue
                    observed = response.json()
                    checks, differences = comparisons(capture, observed)
                    record.update(outcome="passed" if checks and all(checks.values()) else "fingerprint_mismatch",
                                  checks=checks, differences=differences, negotiated_http_version=observed.get("http_version"),
                                  fingerprint={key: observed["tls"].get(key) for key in FINGERPRINTS})
                    record.update(diagnose_baseline(capture, observed, checks, differences))
                    break
            except Exception as exc:
                attempts.append({"error_type": type(exc).__name__, "error_category": error_category(exc)})
        else:
            record["outcome"] = "request_failed"
        record["failed_attempts"] = attempts
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return record

    results = []
    with journal.open("w") as log, ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(probe, item) for item in files]
        for future in as_completed(futures):
            record = future.result()
            results.append(record)
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.flush()
            if len(results) % 25 == 0 or len(results) == len(files):
                print(json.dumps({"completed": len(results), "total": len(files), "outcomes": dict(Counter(r["outcome"] for r in results))}), flush=True)
    report = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(), "endpoint": ENDPOINT,
              "session_resumption": False, "random_ja3": False, "concurrency": args.concurrency,
              "attempts": args.attempts, "profile_count": len(files), "import_only": args.import_only,
              "native_library_sha256": engine_sha, "comparator_sha256": comparator_sha,
              "outcomes": dict(Counter(record["outcome"] for record in results)),
              "baseline_issue_counts": dict(Counter(record["baseline_issue"] for record in results if "baseline_issue" in record)),
              "results": sorted(results, key=lambda record: record["file"])}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"report": str(args.output), "outcomes": report["outcomes"],
                      "baseline_issue_counts": report["baseline_issue_counts"]}), flush=True)
    return int(any(record["outcome"] not in ("passed", "excluded_resumption", "imported") for record in results))


if __name__ == "__main__":
    raise SystemExit(main())
