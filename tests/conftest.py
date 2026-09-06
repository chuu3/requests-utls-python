"""Integration fixtures consume explicit native artifacts, never a Go checkout."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading

import pytest


@pytest.fixture(scope="session")
def peer(tmp_path_factory):
    binary = os.environ.get("REQUESTS_UTLS_TEST_PEER")
    library = os.environ.get("REQUESTS_UTLS_LIBRARY")
    bundled = os.environ.get("REQUESTS_UTLS_BUNDLED_TEST") == "1"
    if bundled:
        import requests_utls
        import sys

        assert not library, "bundled wheel tests must not override the native library path"
        filename = {"darwin": "librequests_utls.dylib", "win32": "librequests_utls.dll"}.get(sys.platform, "librequests_utls.so")
        library = str(Path(requests_utls.__file__).parent / "native" / filename)
        assert binary, "bundled wheel integration requires an explicit test peer artifact"
    if not binary or not library:
        pytest.skip("set REQUESTS_UTLS_TEST_PEER and REQUESTS_UTLS_LIBRARY for native integration tests")
    assert Path(binary).is_file(), f"test peer binary does not exist: {binary}"
    assert Path(library).is_file(), f"native library does not exist: {library}"
    directory = tmp_path_factory.mktemp("requests-utls-peer")
    error_path = directory / "peer.stderr"
    with error_path.open("w+") as stderr:
        process = subprocess.Popen([binary], stdout=subprocess.PIPE, stderr=stderr, text=True)
        descriptor_lines = queue.Queue()
        reader = threading.Thread(target=lambda: descriptor_lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            try:
                line = descriptor_lines.get(timeout=10)
            except queue.Empty:
                pytest.fail(f"test peer startup timed out: {error_path.read_text()}")
            reader.join(timeout=1)
            assert line, f"test peer exited during startup: {error_path.read_text()}"
            descriptor = json.loads(line)
            ca_path = directory / "ca.pem"
            ca_path.write_text(descriptor["ca_pem"])
            descriptor["ca_file"] = str(ca_path)
            if "http1_ca_pem" in descriptor:
                http1_ca = directory / "http1-ca.pem"
                http1_ca.write_text(descriptor["http1_ca_pem"])
                descriptor["http1_ca_file"] = str(http1_ca)
            yield descriptor
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            reader.join(timeout=1)


@pytest.fixture
def engine_options(peer):
    return {
        "profile": Path(__file__).parent / "fixtures" / "profile.json",
        "verify": peer["ca_file"],
        "timeout": 10,
    }
