"""PEP 517 backend: bundle an explicit engine artifact without compiling Go."""

from contextlib import contextmanager
import os

from setuptools import build_meta as _backend

from _build_support import validate_build


get_requires_for_build_wheel = _backend.get_requires_for_build_wheel
get_requires_for_build_sdist = _backend.get_requires_for_build_sdist
get_requires_for_build_editable = _backend.get_requires_for_build_editable
build_sdist = _backend.build_sdist


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    validate_build()
    return _backend.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    validate_build()
    return _backend.build_wheel(wheel_directory, config_settings, metadata_directory)


@contextmanager
def _editable():
    # Editable installs are for source development and use an external engine.
    key = "_REQUESTS_UTLS_EDITABLE_BUILD"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    with _editable():
        return _backend.prepare_metadata_for_build_editable(metadata_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    with _editable():
        return _backend.build_editable(wheel_directory, config_settings, metadata_directory)
