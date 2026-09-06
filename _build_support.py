"""Validate and stage independently built Go engine artifacts into wheels."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct

from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py
from setuptools.dist import Distribution


class ArtifactError(ValueError):
    """An artifact is missing, inconsistent, or incompatible with its wheel tag."""


@dataclass(frozen=True)
class Artifact:
    root: Path
    platform: str
    files: tuple[str, ...]


def _require(condition, message):
    if not condition:
        raise ArtifactError(message)


def _safe_path(value):
    _require(isinstance(value, str) and value, "artifact paths must be nonempty strings")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and "\\" not in value
        and ":" not in value
        and all(part not in ("", ".", "..") for part in value.split("/")),
        f"unsafe artifact path: {value!r}",
    )
    return path


def _target(platform):
    match = re.fullmatch(r"macosx_(\d+)_(\d+)_(x86_64|arm64)", platform)
    if match:
        major, minor, arch = match.groups()
        return "darwin", {"x86_64": "amd64", "arm64": "arm64"}[arch], (int(major), int(minor), 0)
    targets = {
        "linux_x86_64": ("linux", "amd64", None),
        "linux_aarch64": ("linux", "arm64", None),
        "win_amd64": ("windows", "amd64", None),
        "win_arm64": ("windows", "arm64", None),
    }
    _require(
        platform in targets,
        "unsupported wheel platform; use linux_x86_64/linux_aarch64 before auditwheel repair, "
        "macosx_<minimum>_<x86_64|arm64>, or win_<amd64|arm64>",
    )
    return targets[platform]


def _binary_target(path):
    """Inspect a 64-bit shared library without executing untrusted native code."""
    data = path.read_bytes()
    if data.startswith(b"\x7fELF"):
        _require(len(data) >= 64 and data[4:6] == b"\x02\x01", "expected a 64-bit little-endian ELF library")
        kind, machine = struct.unpack_from("<HH", data, 16)
        _require(kind == 3, "ELF artifact must be a shared library")
        arch = {62: "amd64", 183: "arm64"}.get(machine)
        _require(arch is not None, "unsupported ELF architecture")
        return "linux", arch, None
    if data.startswith(b"\xcf\xfa\xed\xfe"):
        _require(len(data) >= 32, "truncated Mach-O library")
        cpu, _, kind, count, size = struct.unpack_from("<IIIII", data, 4)
        arch = {0x01000007: "amd64", 0x0100000C: "arm64"}.get(cpu)
        _require(kind == 6 and arch is not None, "expected an amd64/arm64 Mach-O dynamic library")
        _require(size <= len(data) - 32, "truncated Mach-O load commands")
        offset, minimum = 32, None
        for _ in range(count):
            _require(offset + 8 <= 32 + size, "truncated Mach-O load command")
            command, length = struct.unpack_from("<II", data, offset)
            _require(length >= 8 and offset + length <= 32 + size, "invalid Mach-O load command")
            version = None
            if command == 0x32:  # LC_BUILD_VERSION
                _require(length >= 24, "truncated LC_BUILD_VERSION")
                platform, version = struct.unpack_from("<II", data, offset + 8)
                _require(platform == 1, "Mach-O artifact is not built for macOS")
            elif command == 0x24:  # LC_VERSION_MIN_MACOSX
                _require(length >= 16, "truncated LC_VERSION_MIN_MACOSX")
                version = struct.unpack_from("<I", data, offset + 8)[0]
            if version is not None:
                candidate = (version >> 16, (version >> 8) & 255, version & 255)
                minimum = max(minimum or candidate, candidate)
            offset += length
        _require(minimum is not None, "Mach-O library does not declare its minimum macOS version")
        return "darwin", arch, minimum
    if data.startswith(b"MZ"):
        _require(len(data) >= 64, "truncated PE library")
        offset = struct.unpack_from("<I", data, 60)[0]
        _require(offset + 24 <= len(data) and data[offset:offset + 4] == b"PE\x00\x00", "invalid PE header")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        flags = struct.unpack_from("<H", data, offset + 22)[0]
        arch = {0x8664: "amd64", 0xAA64: "arm64"}.get(machine)
        _require(flags & 0x2000 and arch is not None, "expected an amd64/arm64 Windows DLL")
        return "windows", arch, None
    raise ArtifactError("native artifact is not a supported ELF, Mach-O, or PE shared library")


def validate_artifact(root, platform):
    root = Path(root).expanduser().resolve()
    _require(root.is_dir(), "REQUESTS_UTLS_NATIVE_DIR must name an existing artifact directory")
    _require(isinstance(platform, str) and platform, "REQUESTS_UTLS_WHEEL_PLATFORM is required for bundled wheels")
    target_os, target_arch, target_minimum = _target(platform)
    manifest_path = root / "engine.json"
    _require(manifest_path.is_file() and not manifest_path.is_symlink(), "artifact engine.json is missing or is a symlink")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactError("artifact engine.json must contain valid UTF-8 JSON") from exc
    _require(isinstance(manifest, dict), "artifact engine.json must be an object")
    _require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1, "unsupported engine manifest schema")
    _require(type(manifest.get("abi_version")) is int and manifest["abi_version"] == 1, "engine artifact must declare ABI 1")
    _require((manifest.get("goos"), manifest.get("goarch")) == (target_os, target_arch), "engine target disagrees with the wheel platform")
    _require(manifest.get("wheel_platform") == platform, "engine wheel_platform disagrees with REQUESTS_UTLS_WHEEL_PLATFORM")
    _require(isinstance(manifest.get("engine_version"), str) and re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", manifest["engine_version"]), "engine_version must be an explicit semantic version")
    _require(isinstance(manifest.get("engine_commit"), str) and re.fullmatch(r"[0-9a-f]{40}", manifest["engine_commit"]), "engine_commit must be a full Git commit hash")
    filename = {"linux": "librequests_utls.so", "darwin": "librequests_utls.dylib", "windows": "librequests_utls.dll"}[target_os]
    library = str(_safe_path(manifest.get("library")))
    _require(library == f"native/{filename}", "engine library path does not match its target OS")
    expected_hash = manifest.get("sha256")
    _require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash), "engine sha256 must be a lowercase SHA-256 digest")

    files = []
    for path in sorted(root.rglob("*")):
        _require(not path.is_symlink(), "engine artifact must not contain symlinks")
        if path.is_dir():
            continue
        _require(path.is_file(), "engine artifact contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        _safe_path(relative)
        _require(
            relative in ("engine.json", library) or relative.startswith(("licenses/", "profiles/")),
            f"unexpected artifact file: {relative}",
        )
        files.append(relative)
    _require(library in files, "engine shared library is missing")
    _require("profiles/chrome_152.json" in files, "engine artifact must include profiles/chrome_152.json")
    _require(any(name.startswith("licenses/") for name in files), "engine artifact must include its licenses")
    actual_hash = hashlib.sha256((root / library).read_bytes()).hexdigest()
    _require(actual_hash == expected_hash, "engine shared library SHA-256 mismatch")
    binary_os, binary_arch, binary_minimum = _binary_target(root / library)
    _require((binary_os, binary_arch) == (target_os, target_arch), "native binary OS/architecture disagrees with the manifest and wheel tag")
    if target_os == "darwin":
        _require(binary_minimum <= target_minimum, "wheel advertises macOS support below the native library's minimum macOS version")
    try:
        profile = json.loads((root / "profiles/chrome_152.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactError("bundled Chrome profile must contain valid UTF-8 JSON") from exc
    _require(isinstance(profile, dict), "bundled Chrome profile must be an object")
    if "files_sha256" in manifest:
        hashes = manifest["files_sha256"]
        _require(isinstance(hashes, dict), "files_sha256 must be an object")
        _require(set(hashes) == set(files) - {"engine.json"}, "files_sha256 must cover every artifact payload file exactly once")
        for relative, digest in hashes.items():
            _safe_path(relative)
            _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "files_sha256 contains an invalid SHA-256 digest")
            _require(hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, f"artifact SHA-256 mismatch: {relative}")
    return Artifact(root, platform, tuple(files))


def validate_build():
    if os.environ.get("_REQUESTS_UTLS_EDITABLE_BUILD") == "1":
        return None
    native_dir = os.environ.get("REQUESTS_UTLS_NATIVE_DIR")
    pure = os.environ.get("REQUESTS_UTLS_PURE_PYTHON") == "1"
    _require(not (native_dir and pure), "choose either a bundled native artifact or the development-only pure Python opt-out")
    if pure:
        return None
    _require(
        native_dir,
        "A prebuilt Go engine artifact is required. Set REQUESTS_UTLS_NATIVE_DIR and "
        "REQUESTS_UTLS_WHEEL_PLATFORM before building a release wheel. Source archives do not "
        "download or compile Go. For source development use pip install -e . or explicitly set "
        "REQUESTS_UTLS_PURE_PYTHON=1 and supply an external engine at runtime.",
    )
    return validate_artifact(native_dir, os.environ.get("REQUESTS_UTLS_WHEEL_PLATFORM"))


class BundledDistribution(Distribution):
    def has_ext_modules(self):
        # Ensure setuptools installs the complete package in the wheel's platform
        # root. No CPython extension is compiled: the Go artifact already exists.
        return bool(os.environ.get("REQUESTS_UTLS_NATIVE_DIR")) and not (
            os.environ.get("REQUESTS_UTLS_PURE_PYTHON") == "1"
            or os.environ.get("_REQUESTS_UTLS_EDITABLE_BUILD") == "1"
        )


class BundledBuildPy(build_py):
    def run(self):
        artifact = validate_build()
        super().run()
        destination = Path(self.build_lib) / "requests_utls"
        # Do not let a previous platform build contaminate a later wheel.
        for name in ("native", "licenses", "profiles", "engine.json"):
            stale = destination / name
            if stale.is_dir():
                shutil.rmtree(stale)
            elif stale.exists():
                stale.unlink()
        if artifact:
            for relative in artifact.files:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact.root / relative, target)


class BundledWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.artifact = validate_build()
        if self.artifact:
            self.root_is_pure = False
            self.plat_name = self.artifact.platform
            self.plat_name_supplied = True

    def get_tag(self):
        if self.artifact:
            # ABI-mode CFFI loads a Go C ABI, independent of CPython's ABI.
            return "py3", "none", self.artifact.platform
        return super().get_tag()
