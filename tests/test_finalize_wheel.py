"""Verify post-repair native digests and the complete wheel RECORD."""

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_requests_utls_finalize_wheel", ROOT / "scripts/finalize_wheel.py")
finalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalizer)


def native_bytes(os="darwin", arch="arm64"):
    if os == "darwin":
        cpu = {"arm64": 0x0100000C, "amd64": 0x01000007}[arch]
        return struct.pack("<IIIIIIIIIIIIII", 0xFEEDFACF, cpu, 0, 6, 1, 24, 0, 0, 0x32, 24, 1, 13 << 16, 13 << 16, 0)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", header, 16, 3, {"amd64": 62, "arm64": 183}[arch])
    return bytes(header)


def make_wheel(tmp_path, *, platform="macosx_13_0_arm64", os="darwin", arch="arm64", manifest_changes=None, extra=None, compressed_tags=False):
    path = tmp_path / f"requests_utls-0.1.0-py3-none-{platform}.whl"
    extension = {"darwin": "dylib", "linux": "so"}[os]
    library = "native/librequests_utls." + extension
    original = native_bytes(os, arch)
    manifest = {
        "schema_version": 1, "abi_version": 1, "goos": os, "goarch": arch,
        "engine_commit": "a" * 40, "engine_version": "v0.1.0", "library": library,
        "sha256": hashlib.sha256(original).hexdigest(), "wheel_platform": platform,
        "files_sha256": {library: hashlib.sha256(original).hexdigest()},
    }
    manifest.update(manifest_changes or {})
    tags = [platform] if compressed_tags else platform.split(".")
    content = {
        "requests_utls/engine.json": json.dumps(manifest).encode(),
        "requests_utls/" + library: original + b"modified by repair",
        "requests_utls/licenses/LICENSE": b"license fixture",
        "requests_utls/profiles/chrome_152.json": b'{"schema_version":1}',
        "requests_utls/session.py": b"# Python payload\n",
        "requests_utls-0.1.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: false\n" + "".join(f"Tag: py3-none-{tag}\n" for tag in tags)
        ).encode(),
        "requests_utls-0.1.0.dist-info/METADATA": b"Metadata-Version: 2.4\nName: requests-utls\nVersion: 0.1.0\n",
        "requests_utls-0.1.0.dist-info/RECORD": b"old RECORD replaced after repair\n",
    }
    content.update(extra or {})
    with zipfile.ZipFile(path, "w") as wheel:
        for name, data in content.items():
            if data is not None:
                # ZipInfo normalizes Windows backslashes in its constructor.
                # Preserve the requested raw name so malformed-path fixtures
                # exercise the archive validator on every host platform.
                entry = zipfile.ZipInfo(name)
                entry.filename = name
                entry.orig_filename = name
                wheel.writestr(entry, data)
    with zipfile.ZipFile(path) as wheel:
        assert {entry.orig_filename for entry in wheel.infolist()} == {
            name for name, data in content.items() if data is not None
        }
    return path, content, manifest


def read(path):
    with zipfile.ZipFile(path) as wheel:
        return {name: wheel.read(name) for name in wheel.namelist()}


def assert_record(content):
    record_name = "requests_utls-0.1.0.dist-info/RECORD"
    rows = list(csv.reader(io.StringIO(content[record_name].decode())))
    assert len(rows) == len(content)
    assert {row[0] for row in rows} == set(content)
    for name, encoded, length in rows:
        if name == record_name:
            assert (encoded, length) == ("", "")
        else:
            assert int(length) == len(content[name])
            algorithm, value = encoded.split("=", 1)
            assert algorithm == "sha256"
            assert "=" not in value
            assert base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)) == hashlib.sha256(content[name]).digest()


def test_repaired_bytes_record_dependencies_and_idempotence(tmp_path):
    path, before, original = make_wheel(tmp_path, extra={
        "requests_utls/.dylibs/dependency.dylib": b"mac dependency",
        "requests_utls.libs/dependency.so": b"linux dependency",
    })
    finalizer.finalize(path)
    content = read(path)
    manifest = json.loads(content["requests_utls/engine.json"])
    library_path = "requests_utls/" + original["library"]
    assert manifest["source_sha256"] == original["sha256"]
    assert manifest["sha256"] == hashlib.sha256(before[library_path]).hexdigest()
    assert manifest["sha256"] != original["sha256"]
    assert manifest["files_sha256"][original["library"]] == manifest["sha256"]
    assert manifest["repaired_dependencies_sha256"] == {
        name: hashlib.sha256(before[name]).hexdigest()
        for name in ("requests_utls/.dylibs/dependency.dylib", "requests_utls.libs/dependency.so")
    }
    assert manifest["wheel_tags"] == ["py3-none-macosx_13_0_arm64"]
    assert {name: value for name, value in content.items() if name not in ("requests_utls/engine.json", "requests_utls-0.1.0.dist-info/RECORD")} == {
        name: value for name, value in before.items() if name not in ("requests_utls/engine.json", "requests_utls-0.1.0.dist-info/RECORD")
    }
    assert_record(content)
    finalizer.finalize(path)
    assert read(path) == content


@pytest.mark.parametrize("compressed", [False, True])
def test_multiple_linux_platform_tags(tmp_path, compressed):
    platforms = "manylinux_2_28_x86_64.manylinux_2_34_x86_64"
    path, _, _ = make_wheel(tmp_path, platform=platforms, os="linux", arch="amd64", compressed_tags=compressed)
    finalizer.finalize(path)
    content = read(path)
    manifest = json.loads(content["requests_utls/engine.json"])
    assert manifest["wheel_platform"] == platforms
    assert manifest["wheel_tags"] == [f"py3-none-{tag}" for tag in platforms.split(".")]
    assert_record(content)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX permission bits")
def test_finalized_wheel_is_readable_outside_the_build_container(tmp_path):
    path, _, _ = make_wheel(tmp_path)
    path.chmod(0o600)
    finalizer.finalize(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.parametrize("changes, message", [
    ({"abi_version": True}, "ABI or manifest schema"),
    ({"schema_version": True}, "ABI or manifest schema"),
    ({"abi_version": 2}, "ABI or manifest schema"),
    ({"goarch": "amd64"}, "binary OS/architecture"),
    ({"goos": "linux"}, "library path disagrees"),
    ({"library": "../escape.dylib"}, "unsafe wheel ZIP path"),
    ({"library": ""}, "unsafe wheel ZIP path"),
    ({"sha256": "invalid"}, "source SHA-256"),
])
def test_invalid_manifest_rejected_without_mutation(tmp_path, changes, message):
    path, _, _ = make_wheel(tmp_path, manifest_changes=changes)
    original = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        finalizer.finalize(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("platform, message", [
    ("macosx_13_0_x86_64", "platform OS/architecture"),
    ("macosx_12_0_arm64", "minimum macOS"),
    ("manylinux_2_28_aarch64", "platform OS/architecture"),
    ("any", "platform wheel"),
])
def test_incorrect_compatibility_claim_rejected(tmp_path, platform, message):
    path, _, _ = make_wheel(tmp_path, platform=platform)
    with pytest.raises(ValueError, match=message):
        finalizer.finalize(path)


@pytest.mark.parametrize("name", ["../escape", "requests_utls/../escape", "C:/escape", "requests_utls\\escape", "requests_utls/./escape"])
def test_unsafe_paths_rejected(tmp_path, name):
    path, _, _ = make_wheel(tmp_path, extra={name: b"unexpected"})
    with pytest.raises(ValueError, match="unsafe wheel ZIP path"):
        finalizer.finalize(path)


def test_symlinks_rejected(tmp_path):
    path, _, _ = make_wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as wheel:
        entry = zipfile.ZipInfo("requests_utls/native/alias")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        wheel.writestr(entry, "librequests_utls.dylib")
    with pytest.raises(ValueError, match="symlinks"):
        finalizer.finalize(path)


def test_duplicate_zip_paths_rejected(tmp_path):
    path, _, _ = make_wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as wheel, pytest.warns(UserWarning, match="Duplicate name"):
        wheel.writestr("requests_utls/session.py", b"duplicate")
    with pytest.raises(ValueError, match="duplicate wheel ZIP paths"):
        finalizer.finalize(path)


def test_missing_manifest_rejected(tmp_path):
    path, _, _ = make_wheel(tmp_path, extra={"requests_utls/engine.json": None})
    with pytest.raises(ValueError, match="missing requests_utls/engine.json"):
        finalizer.finalize(path)


def test_internal_tags_must_match_filename(tmp_path):
    path, _, _ = make_wheel(tmp_path, extra={"requests_utls-0.1.0.dist-info/WHEEL": b"Root-Is-Purelib: false\nTag: py3-none-any\n"})
    with pytest.raises(ValueError, match="internal compatibility tags"):
        finalizer.finalize(path)


def test_signed_wheel_is_not_silently_invalidated(tmp_path):
    path, _, _ = make_wheel(tmp_path, extra={"requests_utls-0.1.0.dist-info/RECORD.jws": b"signature"})
    with pytest.raises(ValueError, match="unsigned wheel"):
        finalizer.finalize(path)


def test_dependency_hashes_do_not_retain_removed_files(tmp_path):
    path, _, _ = make_wheel(tmp_path, manifest_changes={"repaired_dependencies_sha256": {"missing.libs/old.so": "a" * 64}})
    finalizer.finalize(path)
    assert "repaired_dependencies_sha256" not in json.loads(read(path)["requests_utls/engine.json"])
