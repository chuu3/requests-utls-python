"""Independently explain strict Peet padding mismatches; never change acceptance."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


FIELDS = ("ja3", "ja3_hash", "ja4", "ja4_r", "peetprint", "peetprint_hash")
VERSIONS = {"769": "10", "770": "11", "771": "12", "772": "13"}
ALPN = {"2": "h2", "1.1": "h1", "1.0": "h1", "0.9": "h1", "3": "h3", "": ""}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def audit_record(capture, record):
    """Recompute old/current JA4 b/c using the identical unmodified prefix.

    Stable wire comparisons are evidence inherited from the strict report;
    their observed payloads were intentionally not persisted. Fingerprint
    calculations below are independent of the frozen live comparator.
    """
    _require(record["outcome"] == "fingerprint_mismatch", "not_a_strict_mismatch")
    checks, differences = record["checks"], record["differences"]
    required = {"complete_fingerprint_baseline", "cipher_order", "extension_order",
                "extension_vectors", *FIELDS}
    _require(required.issubset(checks), "missing_strict_checks")
    _require({key for key, value in checks.items() if value is not True} == {"ja4", "ja4_r"},
             "additional_strict_check_failure")
    _require(checks["ja4"] is False and checks["ja4_r"] is False, "invalid_ja4_checks")
    _require(set(differences) == {"ja4", "ja4_r"}, "additional_strict_difference")
    old, new = capture["tls"], record["fingerprint"]
    for key in FIELDS:
        _require(isinstance(old[key], str) and bool(old[key]), "missing_source_fingerprint")
        _require(isinstance(new[key], str) and bool(new[key]), "missing_observed_fingerprint")
        if key not in ("ja4", "ja4_r"):
            _require(old[key] == new[key], "other_fingerprint_changed")
        else:
            _require(differences[key] == {"expected": old[key], "actual": new[key]},
                     "report_difference_inconsistent")
    for key in ("ja3", "peetprint"):
        digest = hashlib.md5(old[key].encode("ascii"), usedforsecurity=False).hexdigest()
        _require(digest == old[key + "_hash"], "source_fingerprint_hash_inconsistent")
    ja3, peetprint = old["ja3"].split(","), old["peetprint"].split("|")
    _require(len(ja3) == 5 and len(peetprint) == 8, "invalid_fingerprint_structure")
    ciphers = [int(value) for value in ja3[1].split("-")]
    extensions = [int(value) for value in ja3[2].split("-")]
    signatures = [int(value) for value in peetprint[3].split("-") if value != "GREASE"]
    _require(21 in extensions and bool(signatures), "missing_padding_or_signatures")
    _require(all(0 <= value <= 65535 for value in (*ciphers, *extensions, *signatures)), "invalid_tls_id")
    _require(not any(value & 0x0F0F == 0x0A0A and value >> 8 == value & 0xFF
                     for value in (*ciphers, *extensions)), "grease_in_ja3")
    before, after = old["ja4_r"].split("_"), new["ja4_r"].split("_")
    _require(len(before) == 4 and len(after) == 4, "invalid_ja4_raw_structure")
    prefix = before[0]
    _require(prefix == after[0], "prefix_changed")

    # Match the published Peet implementation, including its known a-section
    # deviations: hardcoded d, decimal counts without padding, and empty ALPN.
    version = VERSIONS[str(old["tls_version_negotiated"])]
    protocol = ALPN[peetprint[1].split("-")[0]]
    peet_prefix = f"t{version}d{len(ciphers)}{len(extensions)}{protocol}"
    _require(prefix == peet_prefix, "prefix_not_explained_by_peet_source")
    prefix_issues = []
    if not protocol:
        prefix_issues.append("missing_alpn_00")
    if 0 not in extensions:
        prefix_issues.append("sni_flag_d_without_sni")
    if not 10 <= len(ciphers) <= 99:
        prefix_issues.append("cipher_count_not_two_digits")
    if not 10 <= len(extensions) <= 99:
        prefix_issues.append("extension_count_not_two_digits")
    advertised = [int(value) for value in peetprint[0].split("-") if value in VERSIONS]
    if advertised and VERSIONS[str(max(advertised))] != version:
        prefix_issues.append("negotiated_version_instead_of_highest_advertised")

    cipher_raw = ",".join(sorted(f"{value:04x}" for value in ciphers))
    signature_raw = ",".join(f"{value:04x}" for value in signatures)
    legacy_extensions = ",".join(sorted(f"{value:04x}" for value in extensions if value not in (0, 16, 21)))
    current_extensions = ",".join(sorted(f"{value:04x}" for value in extensions if value not in (0, 16)))
    _require(before == [prefix, cipher_raw, legacy_extensions, signature_raw], "source_not_exact_legacy_padding_algorithm")
    _require(after == [prefix, cipher_raw, current_extensions, signature_raw], "observed_not_exact_current_padding_algorithm")
    cipher_hash = _sha256(cipher_raw.encode("ascii"))[:12]
    legacy_hash = _sha256((legacy_extensions + "_" + signature_raw).encode("ascii"))[:12]
    current_hash = _sha256((current_extensions + "_" + signature_raw).encode("ascii"))[:12]
    calculated_legacy = f"{prefix}_{cipher_hash}_{legacy_hash}"
    calculated_current = f"{prefix}_{cipher_hash}_{current_hash}"
    _require(calculated_legacy == old["ja4"], "source_ja4_hash_inconsistent")
    _require(calculated_current == new["ja4"], "observed_ja4_hash_inconsistent")
    return {
        "audit_result": "verified_legacy_padding_difference",
        "strict_outcome": record["outcome"],
        "strict_baseline_issue": record.get("baseline_issue"),
        "strict_failed_checks": ["ja4", "ja4_r"],
        "other_strict_checks_passed": True,
        "source_prefix": prefix,
        "observed_prefix": after[0],
        "prefix_issues": prefix_issues,
        "calculated_cipher_hash": cipher_hash,
        "calculated_legacy_extension_hash": legacy_hash,
        "calculated_current_extension_hash": current_hash,
        "source_ja4": old["ja4"],
        "observed_ja4": new["ja4"],
        "calculated_legacy_ja4": calculated_legacy,
        "calculated_current_ja4": calculated_current,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.report.resolve() == args.output.resolve():
        parser.error("audit output must not replace the strict report")
    report_bytes = args.report.read_bytes()
    report = json.loads(report_bytes)
    if len(report["results"]) != report["profile_count"] or dict(Counter(r["outcome"] for r in report["results"])) != report["outcomes"]:
        parser.error("strict report must be complete and internally consistent")
    files = {}
    for directory in args.directories:
        if not directory.is_dir():
            parser.error("a source directory is unavailable")
        for path in directory.rglob("*.json"):
            identifier = directory.parent.name + "/" + directory.name + "/" + path.relative_to(directory).as_posix()
            if identifier in files:
                parser.error("source identifiers must be unique")
            files[identifier] = path
    results = []
    for record in report["results"]:
        if record["outcome"] != "fingerprint_mismatch":
            continue
        result = {"file": record["file"], "source_sha256": record["sha256"]}
        try:
            raw = files[record["file"]].read_bytes()
            _require(_sha256(raw) == record["sha256"], "source_digest_changed")
            result.update(audit_record(json.loads(raw), record))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            result.update(audit_result="unexplained", reason=str(exc) if type(exc) is ValueError else type(exc).__name__)
        results.append(result)
    output = {
        "schema_version": 1,
        "strict_report_file": args.report.name,
        "strict_report_sha256": _sha256(report_bytes),
        "native_library_sha256": report.get("native_library_sha256"),
        "comparator_sha256": report.get("comparator_sha256"),
        "audit_script_sha256": _sha256(Path(__file__).read_bytes()),
        "strict_report_outcomes": report["outcomes"],
        "audited_mismatch_count": len(results),
        "audit_results": dict(Counter(r["audit_result"] for r in results)),
        "prefix_issue_counts": dict(Counter(issue for r in results for issue in r.get("prefix_issues", []))),
        "interpretation": "Independent historical-algorithm diagnosis only. Strict outcomes and checks remain unchanged. Stable wire-field checks are inherited from the strict report.",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"audited_mismatch_count": len(results), "audit_results": output["audit_results"],
                      "prefix_issue_counts": output["prefix_issue_counts"]}))
    return int(not results or any(r["audit_result"] != "verified_legacy_padding_difference" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
