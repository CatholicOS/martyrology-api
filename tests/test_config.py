from martyrology_api.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.data_path == "data/editions"
    assert "martyrologium_romanum_2004" in s.restricted_set
    assert s.auth_enabled is False
    assert s.authz_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("MARTYROLOGY_DATA_PATH", "/a:/b")
    s = Settings(_env_file=None)
    assert [str(p) for p in s.data_path_list] == ["/a", "/b"]
