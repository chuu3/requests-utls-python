"""Keep live acceptance strict while ignoring genuinely random GREASE values."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("profile_acceptance", Path(__file__).parents[1] / "examples/profile_acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def test_grease_normalization_preserves_real_algorithms():
    assert acceptance.grease(["TLS_GREASE (0xaaaa)", "0x8a8a", "0x0808", "X25519 (29)"]) == ["GREASE", "GREASE", "0x0808", "X25519 (29)"]


def test_only_captured_resumption_is_excluded():
    def capture(*extensions):
        return {"tls": {"extensions": list(extensions)}}
    assert acceptance.resumed_capture(capture({"name": "pre_shared_key (41)"})) == "captured_psk"
    assert acceptance.resumed_capture(capture({"name": "session_ticket (35)", "data": "01"})) == "captured_session_ticket"
    assert acceptance.resumed_capture(capture({"name": "session_ticket (35)"}, {"name": "key_share (51)"})) is None
    assert acceptance.resumed_capture(capture({"name": "cookie (44)"})) is None


def test_unknown_extension_payload_is_checked():
    old = {"tls": {"ja3": "771,4865,51764,,", "ciphers": ["TLS_AES_128_GCM_SHA256"],
                   "extensions": [{"name": "Unknown extension 51764", "data": "01"}]}}
    new = copy.deepcopy(old)
    new["tls"]["extensions"][0]["data"] = "ff"
    checks, differences = acceptance.comparisons(old, new)
    assert not checks["extension_51764_payload"]
    assert differences["extension_51764_payload"]["expected_sha256"] != differences["extension_51764_payload"]["actual_sha256"]


def test_vector_checks_preserve_duplicates_and_check_order():
    capture = {"tls": {"ja3": "771,4865,13,29,0", "ciphers": ["TLS_GREASE (0xaaaa)", "TLS_AES_128_GCM_SHA256"],
                       "extensions": [{"name": "signature_algorithms (13)", "signature_algorithms": ["0x8a8a", "0x0805", "0x0805"]}]}}
    observed = copy.deepcopy(capture)
    for key in acceptance.FINGERPRINTS:
        capture["tls"].setdefault(key, "baseline")
        observed["tls"].setdefault(key, "baseline")
    observed["tls"]["ciphers"][0] = "TLS_GREASE (0x1a1a)"
    observed["tls"]["extensions"][0]["signature_algorithms"][0] = "0x3a3a"
    checks, _ = acceptance.comparisons(capture, observed)
    assert all(checks.values())
    observed["tls"]["extensions"][0]["signature_algorithms"].pop()
    checks, differences = acceptance.comparisons(capture, observed)
    assert checks["ja3"] and not checks["extension_vectors"]
    assert "extension_0_signature_algorithms" in differences


def test_missing_fingerprints_cannot_pass():
    capture = {"tls": {"ciphers": ["TLS_AES_128_GCM_SHA256"], "extensions": []}}
    checks, _ = acceptance.comparisons(capture, capture)
    assert not checks["complete_fingerprint_baseline"]


@pytest.fixture
def legacy_padding_pair():
    # Actual stable fingerprints from capture 01888cf52d4510caecf90c3288445ac5.
    # No request metadata, cookies, IPs, randoms, keys or tickets are included.
    tls = {
        "ja3": "771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,35-11-27-10-13-17513-23-16-43-5-45-18-65281-0-51-21,29-23-24,0",
        "ja3_hash": "01888cf52d4510caecf90c3288445ac5",
        "ja4": "t13d1516h2_8daaf6152771_f37e75b10bcc",
        "ja4_r": "t13d1516h2_002f,0035,009c,009d,1301,1302,1303,c013,c014,c02b,c02c,c02f,c030,cca8,cca9_0005,000a,000b,000d,0012,0017,001b,0023,002b,002d,0033,4469,ff01_0403,0804,0401,0503,0805,0501,0806,0601",
        "peetprint": "GREASE-772-771|2-1.1|GREASE-29-23-24|1027-2052-1025-1283-2053-1281-2054-1537|1|2|GREASE-4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53|0-10-11-13-16-17513-18-21-23-27-35-43-45-5-51-65281-GREASE-GREASE",
        "peetprint_hash": "22a4f858cc83b9144c829ca411948a88",
        "ciphers": ["TLS_GREASE (0xaaaa)", "TLS_AES_128_GCM_SHA256"],
        "extensions": [{"name": "padding (21)", "padding_data_length": 402}],
    }
    old = {"http_version": "h2", "tls": tls}
    new = copy.deepcopy(old)
    new["tls"]["ja4_r"] = tls["ja4_r"].replace("0012,0017", "0012,0015,0017")
    new["tls"]["ja4"] = "t13d1516h2_8daaf6152771_e5627efa2ab1"
    return old, new


def _diagnosis(old, new):
    checks, differences = acceptance.comparisons(old, new)
    return acceptance.diagnose_baseline(old, new, checks, differences)


def _set_raw_and_hash(tls, raw):
    prefix, ciphers, extensions, signatures = raw.split("_")
    b = hashlib.sha256(ciphers.encode()).hexdigest()[:12]
    c = hashlib.sha256((extensions + "_" + signatures).encode()).hexdigest()[:12]
    tls["ja4_r"] = raw
    tls["ja4"] = f"{prefix}_{b}_{c}"


def test_known_legacy_padding_diagnosis_keeps_both_ja4_checks_failed(legacy_padding_pair):
    old, new = legacy_padding_pair
    snapshot = copy.deepcopy((old, new))
    checks, differences = acceptance.comparisons(old, new)
    original_checks, original_differences = copy.deepcopy((checks, differences))
    diagnosis = acceptance.diagnose_baseline(old, new, checks, differences)
    assert diagnosis == {
        "baseline_issue": "legacy_peet_ja4_omits_padding",
        "baseline_ja4_calculated_legacy": "t13d1516h2_8daaf6152771_f37e75b10bcc",
        "baseline_ja4_calculated_current": "t13d1516h2_8daaf6152771_e5627efa2ab1",
    }
    assert checks["ja4"] is False and checks["ja4_r"] is False
    assert checks == original_checks and differences == original_differences
    assert (old, new) == snapshot


@pytest.mark.parametrize("change", ["padding_length", "cipher_order", "extension_order", "extra_payload"])
def test_other_wire_differences_prevent_legacy_padding_attribution(legacy_padding_pair, change):
    old, new = legacy_padding_pair
    if change == "padding_length":
        new["tls"]["extensions"][0]["padding_data_length"] = 804
    elif change == "cipher_order":
        new["tls"]["ciphers"].reverse()
    elif change == "extension_order":
        new["tls"]["extensions"].append({"name": "other (28)"})
    else:
        old["tls"]["extensions"].append({"name": "Unknown extension 51764", "data": "01"})
        new["tls"]["extensions"].append({"name": "Unknown extension 51764", "data": "ff"})
    assert _diagnosis(old, new) == {}


@pytest.mark.parametrize("change", [
    "source_hash", "observed_hash", "source_omits_another_extension",
    "observed_omits_another_extension", "observed_signature", "observed_prefix",
    "source_contains_padding", "source_ja3_omits_padding", "bad_shared_source_hash",
])
def test_only_the_exact_historical_algorithm_is_diagnosed(legacy_padding_pair, change):
    old, new = legacy_padding_pair
    if change == "source_hash":
        old["tls"]["ja4"] = old["tls"]["ja4"][:-1] + "0"
    elif change == "observed_hash":
        new["tls"]["ja4"] = new["tls"]["ja4"][:-1] + "0"
    elif change == "source_omits_another_extension":
        _set_raw_and_hash(old["tls"], old["tls"]["ja4_r"].replace(",0017", ""))
    elif change == "observed_omits_another_extension":
        _set_raw_and_hash(new["tls"], new["tls"]["ja4_r"].replace(",0017", ""))
    elif change == "observed_signature":
        _set_raw_and_hash(new["tls"], new["tls"]["ja4_r"].replace("_0403,", "_0402,"))
    elif change == "observed_prefix":
        _set_raw_and_hash(new["tls"], new["tls"]["ja4_r"].replace("t13", "t12", 1))
    elif change == "source_contains_padding":
        _set_raw_and_hash(old["tls"], new["tls"]["ja4_r"])
    elif change == "source_ja3_omits_padding":
        for capture in (old, new):
            tls = capture["tls"]
            tls["ja3"] = tls["ja3"].replace("-21,", ",")
            tls["ja3_hash"] = hashlib.md5(tls["ja3"].encode(), usedforsecurity=False).hexdigest()
    else:
        old["tls"]["ja3_hash"] = new["tls"]["ja3_hash"] = "0" * 32
    assert _diagnosis(old, new) == {}


def test_legacy_padding_diagnosis_requires_all_other_checks(legacy_padding_pair):
    old, new = legacy_padding_pair
    checks, differences = acceptance.comparisons(old, new)
    checks["extra_static_payload"] = False
    assert acceptance.diagnose_baseline(old, new, checks, differences) == {}
    del checks["extra_static_payload"], checks["cipher_order"]
    assert acceptance.diagnose_baseline(old, new, checks, differences) == {}


def test_cli_reports_legacy_padding_without_turning_failure_into_success(tmp_path, monkeypatch, legacy_padding_pair):
    old, new = legacy_padding_pair
    directory = tmp_path / "captures"
    directory.mkdir()
    (directory / "old.json").write_text(json.dumps(old))
    output = tmp_path / "report.json"

    class FakeProfile:
        @staticmethod
        def from_peet(capture, *, allow_opaque):
            assert capture == old and allow_opaque is True
            return object()

    class FakeResponse:
        status_code = 200

        def json(self):
            return new

    class FakeSession:
        profile_hash = "profile-hash"

        def __init__(self, **options):
            assert options["random_ja3"] is False
            assert options["session_resumption"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, *, headers):
            assert url == acceptance.ENDPOINT
            return FakeResponse()

    monkeypatch.setattr(acceptance, "Profile", FakeProfile)
    monkeypatch.setattr(acceptance, "Session", FakeSession)
    for name in ("REQUESTS_UTLS_LIBRARY", "REQUESTS_UTLS_PROXY", "REQUESTS_UTLS_PROXY_USERNAME", "REQUESTS_UTLS_PROXY_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["profile_acceptance.py", str(directory), "--output", str(output), "--attempts", "1"])
    assert acceptance.main() == 1
    report = json.loads(output.read_text())
    assert report["outcomes"] == {"fingerprint_mismatch": 1}
    assert report["baseline_issue_counts"] == {"legacy_peet_ja4_omits_padding": 1}
    record = report["results"][0]
    assert record["outcome"] == "fingerprint_mismatch"
    assert record["checks"]["ja4"] is False and record["checks"]["ja4_r"] is False
    assert record["baseline_ja4_calculated_current"] == new["tls"]["ja4"]
    assert json.loads(output.with_suffix(".jsonl").read_text()) == record


audit_spec = importlib.util.spec_from_file_location("audit_peet_padding_baselines", Path(__file__).parents[1] / "examples/audit_peet_padding_baselines.py")
padding_audit = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(padding_audit)


def _audit_inputs(pair):
    old, new = copy.deepcopy(pair)
    old["tls"]["tls_version_negotiated"] = "772"
    checks, differences = acceptance.comparisons(old, new)
    record = {"file": "captures/source.json", "outcome": "fingerprint_mismatch",
              "checks": checks, "differences": differences,
              "fingerprint": {key: new["tls"][key] for key in acceptance.FINGERPRINTS}}
    return old, record


def test_independent_audit_recomputes_known_hashes_without_modifying_record(legacy_padding_pair):
    old, record = _audit_inputs(legacy_padding_pair)
    snapshot = copy.deepcopy((old, record))
    result = padding_audit.audit_record(old, record)
    assert result["audit_result"] == "verified_legacy_padding_difference"
    assert result["calculated_legacy_extension_hash"] == "f37e75b10bcc"
    assert result["calculated_current_extension_hash"] == "e5627efa2ab1"
    assert result["source_prefix"] == result["observed_prefix"] == "t13d1516h2"
    assert result["prefix_issues"] == []
    assert (old, record) == snapshot


def test_independent_audit_explains_peet_prefix_deviations_without_normalizing_them(legacy_padding_pair):
    old, new = copy.deepcopy(legacy_padding_pair)
    extensions = [10, 11, 13, 15, 35, 51, 21]
    for capture, exclude in ((old, {0, 16, 21}), (new, {0, 16})):
        tls = capture["tls"]
        ja3 = tls["ja3"].split(",")
        ja3[2] = "-".join(map(str, extensions))
        tls["ja3"] = ",".join(ja3)
        tls["ja3_hash"] = hashlib.md5(tls["ja3"].encode(), usedforsecurity=False).hexdigest()
        peet = tls["peetprint"].split("|")
        peet[1] = ""
        peet[7] = "-".join(sorted(map(str, extensions)))
        tls["peetprint"] = "|".join(peet)
        tls["peetprint_hash"] = hashlib.md5(tls["peetprint"].encode(), usedforsecurity=False).hexdigest()
        raw = tls["ja4_r"].split("_")
        raw[0] = "t13d157"  # Peet: fixed d, one-digit extension count, no ALPN.
        raw[2] = ",".join(sorted(f"{value:04x}" for value in extensions if value not in exclude))
        _set_raw_and_hash(tls, "_".join(raw))
    old, record = _audit_inputs((old, new))
    result = padding_audit.audit_record(old, record)
    assert result["prefix_issues"] == ["missing_alpn_00", "sni_flag_d_without_sni", "extension_count_not_two_digits"]
    assert result["source_prefix"] == result["observed_prefix"] == "t13d157"
    assert result["calculated_current_ja4"].startswith("t13d157_")
    assert record["outcome"] == "fingerprint_mismatch"


@pytest.mark.parametrize("change", ["prefix_changed", "other_check_failed", "source_hash_changed"])
def test_independent_audit_rejects_unrelated_or_inconsistent_evidence(legacy_padding_pair, change):
    old, record = _audit_inputs(legacy_padding_pair)
    if change == "prefix_changed":
        record["fingerprint"]["ja4_r"] = record["fingerprint"]["ja4_r"].replace("t13", "t12", 1)
        record["differences"]["ja4_r"]["actual"] = record["fingerprint"]["ja4_r"]
    elif change == "other_check_failed":
        record["checks"]["extension_vectors"] = False
    else:
        old["tls"]["ja3_hash"] = record["fingerprint"]["ja3_hash"] = "0" * 32
    with pytest.raises(ValueError):
        padding_audit.audit_record(old, record)


def test_independent_audit_cli_binds_report_and_source_digests(tmp_path, monkeypatch, legacy_padding_pair):
    old, record = _audit_inputs(legacy_padding_pair)
    directory = tmp_path / "captures"
    directory.mkdir()
    raw = json.dumps(old).encode()
    source = directory / "source.json"
    source.write_bytes(raw)
    record["file"] = tmp_path.name + "/captures/source.json"
    record["sha256"] = hashlib.sha256(raw).hexdigest()
    report = tmp_path / "strict.json"
    report_bytes = json.dumps({"profile_count": 1, "outcomes": {"fingerprint_mismatch": 1},
                              "results": [record], "native_library_sha256": "native-sha",
                              "comparator_sha256": "comparator-sha"}).encode()
    report.write_bytes(report_bytes)
    output = tmp_path / "audit.json"
    monkeypatch.setattr(sys, "argv", ["audit_peet_padding_baselines.py", str(directory),
                                    "--report", str(report), "--output", str(output)])
    assert padding_audit.main() == 0
    result = json.loads(output.read_text())
    assert result["strict_report_sha256"] == hashlib.sha256(report_bytes).hexdigest()
    assert result["audit_results"] == {"verified_legacy_padding_difference": 1}
    assert result["results"][0]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report.read_bytes() == report_bytes and source.read_bytes() == raw
    source.write_bytes(raw + b" ")
    assert padding_audit.main() == 1
    assert json.loads(output.read_text())["results"][0]["reason"] == "source_digest_changed"
