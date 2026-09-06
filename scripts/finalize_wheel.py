"""Refresh the bundled engine manifest after native dependency repair.

auditwheel/delocate/delvewheel can change native bytes and add dependency
libraries. This final step records the bytes actually shipped and rebuilds the
wheel RECORD without modifying the shared libraries or loading native code.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile

from packaging.tags import parse_tag
from packaging.utils import parse_wheel_filename

# This is a repository build tool, not an installed package/runtime dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _build_support import _binary_target


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(name, *, directory=False):
    if not isinstance(name, str) or not name:
        raise ValueError("unsafe wheel ZIP path")
    value = name.removesuffix("/") if directory else name
    if "\\" in value or ":" in value or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("unsafe wheel ZIP path")
    return PurePosixPath(value)


def platform_target(value):
    mac = re.fullmatch(r"macosx_(\d+)_(\d+)_(x86_64|arm64)", value)
    if mac:
        major, minor, arch = mac.groups()
        return "darwin", {"x86_64": "amd64", "arm64": "arm64"}[arch], (int(major), int(minor), 0)
    linux = re.fullmatch(r"(?:linux|manylinux(?:1|2010|2014|_\d+_\d+))_(x86_64|aarch64)", value)
    if linux:
        return "linux", {"x86_64": "amd64", "aarch64": "arm64"}[linux.group(1)], None
    if value in ("win_amd64", "win_arm64"):
        return "windows", value.removeprefix("win_"), None
    raise ValueError(f"unsupported repaired wheel platform: {value}")


def finalize(path):
    name, version, _, tags = parse_wheel_filename(path.name)
    if name != "requests-utls" or any(tag.interpreter != "py3" or tag.abi != "none" or tag.platform == "any" for tag in tags):
        raise ValueError(f"expected a requests-utls py3-none platform wheel: {path.name}")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ValueError("duplicate wheel ZIP paths")
        for member in members:
            # ZipInfo normalizes Windows backslashes in filename on read;
            # validate the original archive path before that normalization too.
            safe_path(member.orig_filename, directory=member.orig_filename.endswith("/"))
            safe_path(member.filename, directory=member.is_dir())
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("wheel ZIP paths must not be symlinks")
        content = {member.filename: archive.read(member) for member in members}
    manifest_path = "requests_utls/engine.json"
    if manifest_path not in content:
        raise ValueError("wheel is missing requests_utls/engine.json")
    manifest = json.loads(content[manifest_path])
    if not isinstance(manifest, dict) or any(type(manifest.get(key)) is not int or manifest[key] != 1 for key in ("abi_version", "schema_version")):
        raise ValueError("unexpected engine ABI or manifest schema")
    if not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"]):
        raise ValueError("manifest must retain a valid source SHA-256 digest")
    if "source_sha256" in manifest and (not isinstance(manifest["source_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["source_sha256"])):
        raise ValueError("invalid original source SHA-256 digest")
    library = safe_path(manifest.get("library"))
    expected_filename = {"linux": "librequests_utls.so", "darwin": "librequests_utls.dylib", "windows": "librequests_utls.dll"}.get(manifest.get("goos"))
    if expected_filename is None or library.as_posix() != "native/" + expected_filename:
        raise ValueError("engine library path disagrees with its target OS")
    library_path = "requests_utls/" + library.as_posix()
    if library_path not in content:
        raise ValueError("wheel is missing its native engine library")
    with tempfile.TemporaryDirectory(prefix="requests-utls-verify-") as temporary:
        binary = Path(temporary) / expected_filename
        binary.write_bytes(content[library_path])
        binary_os, binary_arch, minimum = _binary_target(binary)
    if (binary_os, binary_arch) != (manifest.get("goos"), manifest.get("goarch")):
        raise ValueError("native binary OS/architecture disagrees with the engine manifest")
    for tag in tags:
        target_os, target_arch, target_minimum = platform_target(tag.platform)
        if (target_os, target_arch) != (binary_os, binary_arch):
            raise ValueError("wheel platform OS/architecture disagrees with its native engine")
        if binary_os == "darwin" and minimum > target_minimum:
            raise ValueError("wheel advertises support below the engine's minimum macOS version")
    records = [name for name in content if name.endswith(".dist-info/RECORD")]
    wheels = [name for name in content if name.endswith(".dist-info/WHEEL")]
    if len(records) != 1 or len(wheels) != 1:
        raise ValueError("wheel must contain exactly one RECORD and WHEEL")
    expected_dist_info = f"requests_utls-{version}.dist-info"
    if records != [expected_dist_info + "/RECORD"] or wheels != [expected_dist_info + "/WHEEL"]:
        raise ValueError("wheel filename and dist-info directory differ")
    if any(name.endswith((".dist-info/RECORD.jws", ".dist-info/RECORD.p7s")) for name in content):
        raise ValueError("finalize an unsigned wheel before signing its RECORD")
    metadata = content[wheels[0]].decode()
    if [line for line in metadata.splitlines() if line.startswith("Root-Is-Purelib:")] != ["Root-Is-Purelib: false"]:
        raise ValueError("native wheel must install into platlib")
    metadata_tags = set().union(*(parse_tag(line.removeprefix("Tag: ")) for line in metadata.splitlines() if line.startswith("Tag: ")))
    if metadata_tags != tags:
        raise ValueError("wheel filename and internal compatibility tags differ")
    manifest.setdefault("source_sha256", manifest["sha256"])
    manifest["sha256"] = digest(content[library_path])
    manifest["wheel_platform"] = ".".join(sorted({tag.platform for tag in tags}))
    manifest["wheel_tags"] = sorted(str(tag) for tag in tags)
    # Keep artifact-relative checksums, updating any bytes modified by repair.
    manifest["files_sha256"] = {
        name.removeprefix("requests_utls/"): digest(data)
        for name, data in sorted(content.items())
        if name.startswith(("requests_utls/native/", "requests_utls/licenses/", "requests_utls/profiles/"))
        and not name.endswith("/")
    }
    dependencies = {
        name: digest(data) for name, data in sorted(content.items())
        if any(marker in name for marker in (".libs/", ".dylibs/")) and not name.endswith("/")
    }
    if dependencies:
        manifest["repaired_dependencies_sha256"] = dependencies
    else:
        manifest.pop("repaired_dependencies_sha256", None)
    content[manifest_path] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    record = records[0]
    rows = []
    for name, data in sorted(content.items()):
        if name != record and not name.endswith("/"):
            encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append((name, "sha256=" + encoded, str(len(data))))
    rows.append((record, "", ""))
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    content[record] = buffer.getvalue().encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".whl", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for member in members:
                archive.writestr(member, content[member.filename])
        # Linux builds run as root inside manylinux. A NamedTemporaryFile starts
        # at 0600, so make the completed distribution readable by the host's
        # artifact uploader before replacing the original wheel.
        temporary_path.chmod(0o644)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"Finalized {path.name}: ABI 1, {len(manifest['files_sha256'])} payload files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", required=True, type=Path)
    args = parser.parse_args()
    wheels = sorted(args.wheel_dir.glob("*.whl"))
    if not wheels:
        parser.error("no wheel files found")
    for path in wheels:
        finalize(path)


if __name__ == "__main__":
    main()
