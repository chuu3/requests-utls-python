"""Versioned CFFI ABI. This module never locates or builds Go source code."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import threading

from cffi import FFI

from .exceptions import ForkSafetyError, NativeLibraryError, native_error

_CDEF = """
typedef struct {
    int32_t code;
    uint64_t handle;
    unsigned char *data;
    size_t length;
} RUTS_Result;
uint32_t ruts_abi_version(void);
RUTS_Result ruts_session_create(const unsigned char *, size_t);
RUTS_Result ruts_request_submit(uint64_t, const unsigned char *, size_t, const unsigned char *, size_t);
RUTS_Result ruts_session_poll(uint64_t, int32_t);
RUTS_Result ruts_request_body(uint64_t);
int32_t ruts_request_release(uint64_t);
int32_t ruts_request_cancel(uint64_t);
int32_t ruts_session_close(uint64_t);
void ruts_buffer_free(void *);
RUTS_Result ruts_profile_import(const unsigned char *, size_t, int32_t);
"""

_libraries = {}
_loaded_handles = []
_load_lock = threading.Lock()
_loaded_pid = None


def check_process(pid):
    if pid != os.getpid():
        raise ForkSafetyError("Go engines cannot be used after fork; use multiprocessing with spawn")


def result_error(code, payload, redact=lambda value: value):
    try:
        info = json.loads(payload)
        message = info.get("message", f"native error {code}")
    except (ValueError, AttributeError, TypeError):
        info = {}
        message = f"native error {code}"
    error = native_error(code, redact(str(message)))
    stage = info.get("stage")
    elapsed = info.get("elapsed_ms")
    if stage in {"request", "queue", "connect", "proxy_connect", "tls", "write", "response_headers", "body"}:
        error.stage = stage
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(elapsed) and elapsed >= 0:
            error.elapsed_ms = elapsed
    return error


def _open_process_library(ffi, path):
    """Keep one OS loader reference until process termination, including at exit.

    A normal ffi.dlopen(filename) calls dlclose/FreeLibrary when Python collects
    its wrapper. Module globals are also collected during interpreter shutdown,
    while Go still has runtime threads. CFFI >= 1.14 documents that a borrowed
    void* handle is never automatically closed. We deliberately retain the raw
    OS reference (no dlclose/FreeLibrary anywhere), and use NODELETE as an extra
    safeguard on systems that expose it. This also protects rejected ABI loads.
    """
    loader_ffi = FFI()
    if sys.platform == "win32":
        loader_ffi.cdef("""
            void * __stdcall LoadLibraryExW(const wchar_t *, void *, unsigned long);
        """)
        loader = loader_ffi.dlopen("kernel32.dll")
        # Search the explicit DLL's directory and normal safe dependency paths.
        # This matches modern CFFI's handling of absolute Windows library paths.
        handle = loader.LoadLibraryExW(str(path), loader_ffi.NULL, 0x100 | 0x1000)
    else:
        loader_ffi.cdef("void *dlopen(const char *, int);")
        loader = loader_ffi.dlopen(None)
        flags = ffi.RTLD_NOW | ffi.RTLD_LOCAL | getattr(ffi, "RTLD_NODELETE", 0)
        handle = loader.dlopen(os.fsencode(path), flags)
    if handle == loader_ffi.NULL:
        raise NativeLibraryError("cannot load requests-utls native engine; provide a compatible ABI 1 shared library")
    # No owning CFFI library ever wraps the OS load. Even when every Python
    # object is collected, this reference remains in the operating-system loader.
    return ffi.dlopen(ffi.cast("void *", handle))


class Native:
    def __init__(self, path):
        self.pid = os.getpid()
        self.ffi = FFI()
        self.ffi.cdef(_CDEF)
        try:
            self.lib = _open_process_library(self.ffi, path)
            _loaded_handles.append((self.ffi, self.lib))
            version = self.lib.ruts_abi_version()
        except (OSError, AttributeError) as exc:
            raise NativeLibraryError("cannot load requests-utls native engine; provide an ABI 1 shared library") from exc
        if version != 1:
            raise NativeLibraryError(f"native ABI mismatch: expected 1, received {version}")

    def call(self, name, *args):
        check_process(self.pid)
        result = getattr(self.lib, name)(*args)
        # Every result owns its C allocation, including error results.
        try:
            payload = bytes(self.ffi.buffer(result.data, result.length)) if result.data != self.ffi.NULL else b""
            return int(result.code), int(result.handle), payload
        finally:
            if result.data != self.ffi.NULL:
                self.lib.ruts_buffer_free(result.data)

    def status(self, name, *args):
        check_process(self.pid)
        return int(getattr(self.lib, name)(*args))


def get_native(library_path=None):
    global _loaded_pid
    # Check before acquiring a lock which another thread may have held at fork.
    if _loaded_pid is not None:
        check_process(_loaded_pid)
    explicit = library_path or os.environ.get("REQUESTS_UTLS_LIBRARY")
    if explicit is None:
        filename = {"darwin": "librequests_utls.dylib", "win32": "librequests_utls.dll"}.get(sys.platform, "librequests_utls.so")
        explicit = Path(__file__).parent / "native" / filename
    path = Path(explicit).expanduser().resolve()
    if not path.is_file():
        raise NativeLibraryError("native engine is missing; set REQUESTS_UTLS_LIBRARY or pass library_path to an ABI 1 library")
    # A held loader lock must always have an owning process marker. If fork
    # occurs after this assignment the child fails before acquiring that lock.
    _loaded_pid = os.getpid()
    with _load_lock:
        if path not in _libraries:
            _libraries[path] = Native(path)
        return _libraries[path]
