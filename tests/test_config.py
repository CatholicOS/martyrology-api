import os
from pathlib import Path

from martyrology_api.config import Settings


def test_defaults():
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.data_path == "data/editions"
    assert "martyrologium_romanum_2004" in s.restricted_set
    assert s.auth_enabled is False
    assert s.authz_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("MARTYROLOGY_DATA_PATH", os.pathsep.join(["/a", "/b"]))
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert [str(p) for p in s.data_path_list] == [str(Path("/a")), str(Path("/b"))]


def test_authz_enabled_requires_a_token():
    from martyrology_api.config import Settings

    base = dict(openfga_api_url="https://fga.example", openfga_store_id="s1")
    off = Settings(_env_file=None, **base)  # pyright: ignore[reportCallIssue]
    on = Settings(_env_file=None, **base, openfga_api_token="k")  # pyright: ignore[reportCallIssue]
    assert off.authz_enabled is False
    assert on.authz_enabled is True


def test_zitadel_internal_url_defaults_empty_and_does_not_affect_posture():
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.zitadel_internal_url == ""

    s2 = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        zitadel_internal_url="http://zitadel:8080",
    )
    assert s2.zitadel_internal_url == "http://zitadel:8080"
    assert s2.auth_enabled is False


def test_database_url_defaults_empty():
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.database_url == ""
