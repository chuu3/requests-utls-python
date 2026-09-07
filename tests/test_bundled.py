"""Acceptance checks that specifically require an installed platform wheel."""

import hashlib
import json
import os
from importlib.resources import files

import pytest

from requests_utls import InvalidRequestError, Profile, Session


@pytest.mark.parametrize("name", ["../chrome_152", "chrome_152.json", "", None])
def test_builtin_profile_rejects_paths(name):
    with pytest.raises(InvalidRequestError):
        Profile.builtin(name)


def test_builtin_profile_rejects_unknown_name():
    with pytest.raises(InvalidRequestError, match="unavailable in this installation"):
        Profile.builtin("profile_that_is_not_bundled")


@pytest.mark.skipif(os.environ.get("REQUESTS_UTLS_BUNDLED_TEST") != "1", reason="requires an installed bundled wheel")
def test_installed_engine_and_builtin_profile_work_without_library_configuration(peer):
    assert not os.environ.get("REQUESTS_UTLS_LIBRARY")
    metadata = json.loads(files("requests_utls").joinpath("engine.json").read_bytes())
    assert metadata["abi_version"] == 1
    assert len(metadata["engine_commit"]) == 40
    names = metadata["builtin_profiles"]
    assert {"chrome_150", "chrome_152"} <= set(names)
    for name in names:
        resource = files("requests_utls").joinpath("profiles", name + ".json")
        assert metadata["files_sha256"]["profiles/" + name + ".json"] == hashlib.sha256(resource.read_bytes()).hexdigest()
        profile = Profile.builtin(name)
        assert profile.to_dict() == json.loads(resource.read_bytes())
        with Session(profile=profile, verify=peer["ca_file"], timeout=10) as session:
            response = session.get(peer["url"] + "/echo", headers=[("x-installed-wheel", "yes")])
            assert response.status_code == 200
            assert response.json()["headers"] == [["x-installed-wheel", "yes"]]
            assert session.limitations  # Both captures retain advertised-only notes.
