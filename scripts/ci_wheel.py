#!/usr/bin/env python3
"""Release CI orchestration; native source is an explicit, pinned checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "linux_x86_64": ("manylinux_2_28_x86_64", "librequests_utls.so"),
    "linux_aarch64": ("manylinux_2_28_aarch64", "librequests_utls.so"),
    "macosx_13_0_arm64": ("macosx_13_0_arm64", "librequests_utls.dylib"),
    "macosx_13_0_x86_64": ("macosx_13_0_x86_64", "librequests_utls.dylib"),
    "win_amd64": ("win_amd64", "librequests_utls.dll"),
}


def run(*command, env=None, cwd=ROOT):
    print("+", *map(str, command), flush=True)
    subprocess.run([str(value) for value in command], cwd=cwd, env=env, check=True)


def read_lock():
    lock = json.loads((ROOT / "engine.lock.json").read_text())
    if set(lock) != {"repository", "commit", "go_version", "abi_version"}:
        raise ValueError("engine.lock.json must contain repository, commit, go_version, abi_version")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", lock["repository"]):
        raise ValueError("engine repository must be an owner/repository name")
    if not re.fullmatch(r"[a-f0-9]{40}", lock["commit"]):
        raise ValueError("engine commit must be an exact 40-character SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", lock["go_version"]):
        raise ValueError("Go version must be an exact stable version")
    if type(lock["abi_version"]) is not int or lock["abi_version"] != 1:
        raise ValueError("this Python client requires native ABI 1")
    return lock


def lock_outputs():
    lock = read_lock()
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for name in ("repository", "commit", "go_version"):
            output.write(f"{name}={lock[name]}\n")


def wheel_files(directory):
    files = sorted(Path(directory).glob("*.whl"))
    if not files:
        raise ValueError(f"no wheels in {directory}")
    return files


def verify_wheel(wheel, expected_target):
    from packaging.utils import parse_wheel_filename

    lock = read_lock()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    name, version, _, tags = parse_wheel_filename(wheel.name)
    platform_tag, library_name = TARGETS[expected_target]
    if str(name) != "requests-utls" or str(version) != project["version"]:
        raise ValueError(f"unexpected release identity: {wheel.name}")
    if not tags or any(tag.interpreter != "py3" or tag.abi != "none" or tag.platform == "any" for tag in tags):
        raise ValueError(f"release wheel must use py3-none-platform tags: {wheel.name}")
    if platform_tag not in {tag.platform for tag in tags}:
        raise ValueError(f"wheel has not passed the required platform repair: {wheel.name}")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        expected_library = f"requests_utls/native/{library_name}"
        if expected_library not in names or "requests_utls/engine.json" not in names:
            raise ValueError("release wheel does not include its native engine")
        if "requests_utls/profiles/chrome_152.json" not in names:
            raise ValueError("release wheel does not include the built-in profile")
        if not any(name.startswith("requests_utls/licenses/") for name in names):
            raise ValueError("release wheel does not include engine licenses")
        manifest = json.loads(archive.read("requests_utls/engine.json"))
        if manifest.get("engine_commit") != lock["commit"]:
            raise ValueError("release wheel engine commit does not match engine.lock.json")
        if manifest.get("go_version") != f"go{lock['go_version']}":
            raise ValueError("release wheel Go toolchain does not match engine.lock.json")
        if manifest.get("source_dirty") is not False:
            raise ValueError("release wheel must originate from a clean engine checkout")
        if type(manifest.get("abi_version")) is not int or manifest["abi_version"] != lock["abi_version"]:
            raise ValueError("release wheel engine ABI does not match engine.lock.json")
    return platform_tag


def repair(wheel, directory, target, env):
    if target.startswith("linux_"):
        run(sys.executable, "-m", "auditwheel", "show", wheel, env=env)
        run(sys.executable, "-m", "auditwheel", "repair", "--plat", TARGETS[target][0], "-w", directory, wheel, env=env)
    elif target.startswith("macosx_"):
        arch = "arm64" if target.endswith("arm64") else "x86_64"
        run("delocate-listdeps", wheel, env=env)
        run("delocate-wheel", "--require-archs", arch, "-v", "-w", directory, wheel, env=env)
    else:
        # The engine is loaded dynamically through CFFI, so explicitly analyze
        # DLLs already present in the wheel, not only Python extension modules.
        run(sys.executable, "-m", "delvewheel", "show", "--analyze-existing", wheel, env=env)
        run(sys.executable, "-m", "delvewheel", "repair", "--analyze-existing", "-w", directory, wheel, env=env)


def build_and_test(engine_directory, output_directory, target):
    lock = read_lock()
    engine = Path(engine_directory).resolve()
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    head = subprocess.check_output(["git", "-C", str(engine), "rev-parse", "HEAD"], text=True).strip()
    if head != lock["commit"]:
        raise ValueError("Go checkout does not match engine.lock.json")
    version = subprocess.check_output(["go", "version"], text=True).split()[2]
    if version != f"go{lock['go_version']}":
        raise ValueError("Go toolchain does not match engine.lock.json")
    build_env = os.environ.copy()
    build_env.pop("REQUESTS_UTLS_LIBRARY", None)
    build_env.pop("REQUESTS_UTLS_PURE_PYTHON", None)
    build_env["GOTOOLCHAIN"] = "local"
    build_env["CGO_ENABLED"] = "1"
    if target.startswith("macosx_"):
        build_env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
    with tempfile.TemporaryDirectory(prefix="native-wheel-", dir=output) as temporary:
        work = Path(temporary)
        native = work / "payload"
        peer = work / ("requests-utls-testpeer.exe" if os.name == "nt" else "requests-utls-testpeer")
        pack = [sys.executable, engine / "scripts" / "pack_native.py", "--output", native,
                "--wheel-platform", target, "--engine-version", "v0.1.0", "--peer-output", peer, "--require-clean"]
        if target.startswith("linux_"):
            pack.extend(["--glibc-baseline", "2.28"])
        run(*pack, env=build_env)
        build_env["REQUESTS_UTLS_NATIVE_DIR"] = str(native)
        build_env["REQUESTS_UTLS_WHEEL_PLATFORM"] = target
        raw = work / "raw"
        run(sys.executable, "-m", "build", "--wheel", "--outdir", raw, ROOT, env=build_env)
        candidates = wheel_files(raw)
        if len(candidates) != 1:
            raise ValueError("expected exactly one freshly built wheel")
        repaired = output / "wheelhouse"
        if repaired.exists() and list(repaired.iterdir()):
            raise ValueError("wheelhouse must be empty before repair")
        repaired.mkdir(exist_ok=True)
        repair(candidates[0], repaired, target, build_env)
        # Repair tools may rewrite ELF/Mach-O/PE binaries. Refresh the embedded
        # manifest hashes and wheel RECORD before installing or publishing.
        run(sys.executable, ROOT / "scripts" / "finalize_wheel.py", "--wheel-dir", repaired, env=build_env)
        wheels = wheel_files(repaired)
        if len(wheels) != 1:
            raise ValueError("expected exactly one repaired wheel")
        verify_wheel(wheels[0], target)
        run(sys.executable, "-m", "twine", "check", "--strict", wheels[0], env=build_env)

        clean_env = os.environ.copy()
        for name in ("REQUESTS_UTLS_LIBRARY", "REQUESTS_UTLS_NATIVE_DIR", "REQUESTS_UTLS_WHEEL_PLATFORM",
                     "REQUESTS_UTLS_PURE_PYTHON", "PYTHONPATH", "GOROOT", "GOPATH"):
            clean_env.pop(name, None)
        clean_env["REQUESTS_UTLS_BUNDLED_TEST"] = "1"
        clean_env["REQUESTS_UTLS_TEST_PEER"] = str(peer)
        venv = work / "installed"
        run(sys.executable, "-m", "venv", venv, env=clean_env)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(python, "-m", "pip", "install", f"{wheels[0]}[test]", env=clean_env)
        run(python, "-c", "import pathlib, sys, requests_utls; "
            "assert pathlib.Path(requests_utls.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); "
            "from requests_utls._native import get_native; assert get_native().lib.ruts_abi_version() == 1", env=clean_env)
        run(python, "-m", "pytest", "-q", "tests", env=clean_env)


def verify_release(directory):
    from packaging.utils import parse_wheel_filename

    files = wheel_files(directory)
    if len(files) != len(TARGETS) or any(path.suffix != ".whl" for path in Path(directory).iterdir()):
        raise ValueError("release must contain exactly five wheels and no sdist or other files")
    remaining = set(TARGETS)
    for wheel in files:
        tags = {tag.platform for tag in parse_wheel_filename(wheel.name)[3]}
        matching = [target for target in remaining if TARGETS[target][0] in tags]
        if len(matching) != 1:
            raise ValueError(f"duplicate or unexpected release platform: {wheel.name}")
        verify_wheel(wheel, matching[0])
        remaining.remove(matching[0])
    if remaining:
        raise ValueError("release is missing supported platforms")
    run(sys.executable, "-m", "twine", "check", "--strict", *files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("lock-outputs")
    build = commands.add_parser("build")
    build.add_argument("--engine-dir", required=True)
    build.add_argument("--output-dir", required=True)
    build.add_argument("--platform", choices=TARGETS, required=True)
    verify = commands.add_parser("verify-release")
    verify.add_argument("--wheel-dir", required=True)
    args = parser.parse_args()
    if args.command == "lock-outputs":
        lock_outputs()
    elif args.command == "build":
        build_and_test(args.engine_dir, args.output_dir, args.platform)
    else:
        verify_release(args.wheel_dir)


if __name__ == "__main__":
    main()
