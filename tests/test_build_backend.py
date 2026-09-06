"""Build-time guarantees for separately produced native artifacts."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_requests_utls_test_build_support", ROOT / "_build_support.py")
support = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = support
spec.loader.exec_module(support)


def macho(minimum=(13, 0, 0), cpu=0x0100000C):
    version = (minimum[0] << 16) | (minimum[1] << 8) | minimum[2]
    return struct.pack("<IIIIIIIIIIIIII", 0xFEEDFACF, cpu, 0, 6, 1, 24, 0, 0, 0x32, 24, 1, version, version, 0)


@pytest.fixture
def artifact(tmp_path):
    root = tmp_path / "artifact"
    (root / "native").mkdir(parents=True)
    (root / "profiles").mkdir()
    (root / "licenses").mkdir()
    (root / "native/librequests_utls.dylib").write_bytes(macho())
    (root / "profiles/chrome_152.json").write_text('{"schema_version":1}', encoding="utf-8")
    (root / "licenses/LICENSE").write_text("Test license fixture", encoding="utf-8")
    payload = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }
    manifest = {
        "schema_version": 1,
        "abi_version": 1,
        "goos": "darwin",
        "goarch": "arm64",
        "engine_version": "v0.1.0",
        "engine_commit": "a" * 40,
        "library": "native/librequests_utls.dylib",
        "sha256": payload["native/librequests_utls.dylib"],
        "wheel_platform": "macosx_13_0_arm64",
        "files_sha256": payload,
    }
    (root / "engine.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def mutate_manifest(root, **changes):
    path = root / "engine.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(changes)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_release_requires_explicit_artifact(monkeypatch):
    for name in ("REQUESTS_UTLS_NATIVE_DIR", "REQUESTS_UTLS_PURE_PYTHON", "_REQUESTS_UTLS_EDITABLE_BUILD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(support.ArtifactError, match="prebuilt Go engine artifact is required"):
        support.validate_build()
    monkeypatch.setenv("REQUESTS_UTLS_PURE_PYTHON", "1")
    assert support.validate_build() is None


def test_valid_artifact(artifact):
    result = support.validate_artifact(artifact, "macosx_13_0_arm64")
    assert result.root == artifact.resolve()
    assert result.files == ("engine.json", "licenses/LICENSE", "native/librequests_utls.dylib", "profiles/chrome_152.json")


@pytest.mark.parametrize("changes, message", [
    ({"abi_version": 2}, "ABI 1"),
    ({"abi_version": True}, "ABI 1"),
    ({"schema_version": 2}, "manifest schema"),
    ({"goarch": "amd64"}, "engine target"),
    ({"engine_commit": "main"}, "full Git commit"),
    ({"library": "../escape.dylib"}, "unsafe artifact path"),
    ({"sha256": "0" * 64}, "SHA-256 mismatch"),
    ({"wheel_platform": "macosx_12_0_arm64"}, "wheel_platform disagrees"),
    ({"files_sha256": {}}, "every artifact payload file"),
])
def test_manifest_rejections(artifact, changes, message):
    mutate_manifest(artifact, **changes)
    with pytest.raises(support.ArtifactError, match=message):
        support.validate_artifact(artifact, "macosx_13_0_arm64")


def test_does_not_overstate_macos_support(artifact):
    mutate_manifest(artifact, wheel_platform="macosx_12_0_arm64")
    with pytest.raises(support.ArtifactError, match="minimum macOS"):
        support.validate_artifact(artifact, "macosx_12_0_arm64")


def test_does_not_assign_unaudited_manylinux_tag(artifact):
    with pytest.raises(support.ArtifactError, match="before auditwheel repair"):
        support.validate_artifact(artifact, "manylinux_2_28_x86_64")


def test_rejects_binary_arch_mismatch(artifact):
    binary = artifact / "native/librequests_utls.dylib"
    binary.write_bytes(macho(cpu=0x01000007))
    mutate_manifest(artifact, sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    with pytest.raises(support.ArtifactError, match="binary OS/architecture"):
        support.validate_artifact(artifact, "macosx_13_0_arm64")


def test_rejects_missing_license(artifact):
    (artifact / "licenses/LICENSE").unlink()
    with pytest.raises(support.ArtifactError, match="include its licenses"):
        support.validate_artifact(artifact, "macosx_13_0_arm64")


def test_rejects_symlink(artifact):
    path = artifact / "profiles/alias.json"
    try:
        path.symlink_to("chrome_152.json")
    except OSError:
        pytest.skip("symlink creation is not permitted by this Windows account")
    with pytest.raises(support.ArtifactError, match="symlinks"):
        support.validate_artifact(artifact, "macosx_13_0_arm64")


def test_rejects_nonlibrary_payload(artifact):
    (artifact / "native/extra.so").write_bytes(b"untracked artifact")
    with pytest.raises(support.ArtifactError, match="unexpected artifact file"):
        support.validate_artifact(artifact, "macosx_13_0_arm64")


def test_binary_elf_and_pe(tmp_path):
    path = tmp_path / "library"
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", elf, 16, 3, 183)
    path.write_bytes(elf)
    assert support._binary_target(path) == ("linux", "arm64", None)
    pe = bytearray(88)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 60, 64)
    pe[64:68] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 68, 0x8664)
    struct.pack_into("<H", pe, 86, 0x2000)
    path.write_bytes(pe)
    assert support._binary_target(path) == ("windows", "amd64", None)


@pytest.fixture
def project(tmp_path):
    destination = tmp_path / "project"
    destination.mkdir()
    for name in ("pyproject.toml", "setup.py", "build_backend.py", "_build_support.py", "MANIFEST.in", "LICENSE", "README.md"):
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "src/requests_utls", destination / "src/requests_utls")
    return destination


def run_backend(project, code, extra_env=None):
    env = {name: value for name, value in os.environ.items() if not name.startswith("REQUESTS_UTLS_") and name != "_REQUESTS_UTLS_EDITABLE_BUILD"}
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", code], cwd=project, env=env, text=True, capture_output=True, timeout=90)


def test_bundled_wheel_layout_and_stale_artifact_cleanup(project, artifact):
    result = run_backend(project, "from build_backend import build_wheel; build_wheel('dist')", {
        "REQUESTS_UTLS_NATIVE_DIR": str(artifact),
        "REQUESTS_UTLS_WHEEL_PLATFORM": "macosx_13_0_arm64",
    })
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list((project / "dist").glob("*-py3-none-macosx_13_0_arm64.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        assert "requests_utls/native/librequests_utls.dylib" in names
        assert "requests_utls/engine.json" in names
        assert "requests_utls/profiles/chrome_152.json" in names
        assert "requests_utls/licenses/LICENSE" in names
        assert "requests_utls/session.py" in names
        assert not any(name.endswith(".go") for name in names)
        metadata = wheel.read(next(name for name in names if name.endswith(".dist-info/WHEEL"))).decode()
        assert "Root-Is-Purelib: false" in metadata
        assert "Tag: py3-none-macosx_13_0_arm64" in metadata
    result = run_backend(project, "from build_backend import build_wheel; build_wheel('dist')", {"REQUESTS_UTLS_PURE_PYTHON": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next((project / "dist").glob("*-py3-none-any.whl"))) as wheel:
        assert not any("/native/" in name or name.endswith("engine.json") for name in wheel.namelist())


def test_sdist_contains_backend_and_fails_clearly_without_engine(project, tmp_path):
    result = run_backend(project, "from build_backend import build_sdist; build_sdist('dist')")
    assert result.returncode == 0, result.stdout + result.stderr
    with tarfile.open(next((project / "dist").glob("*.tar.gz"))) as source:
        names = source.getnames()
        assert any(name.endswith("/build_backend.py") for name in names)
        assert any(name.endswith("/_build_support.py") for name in names)
        assert not any(name.endswith((".dylib", ".so", ".dll")) for name in names)
        unpacked = tmp_path / "unpacked"
        source.extractall(unpacked, filter="data")
    result = run_backend(next(unpacked.iterdir()), "from build_backend import build_wheel; build_wheel('dist')")
    assert result.returncode != 0
    assert "prebuilt Go engine artifact is required" in result.stderr


def test_editable_build_does_not_require_engine(project):
    result = run_backend(project, "from build_backend import build_editable; build_editable('dist')")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((project / "dist").glob("*editable*py3-none-any.whl"))) == 1
