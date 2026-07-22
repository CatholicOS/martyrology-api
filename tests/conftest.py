import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def crmedr_path() -> Path:
    return FIXTURES / "crmedr"


@pytest.fixture
def clbdr_path() -> Path:
    return FIXTURES / "clbdr"


@pytest.fixture
def data_paths() -> list[Path]:
    return [FIXTURES / "editions_public", FIXTURES / "editions_private"]


@pytest.fixture
def make_client(crmedr_path, clbdr_path, data_paths):
    """Factory fixture building a TestClient over a freshly created app.
    Defaults to the standard fixture registries/data; pass Settings
    keyword overrides (e.g. local_git_root=...) to customize. Auth/authz
    stubbing stays per-test-file: set `client.app.state.authenticator`
    and `.authz` on the returned TestClient as needed."""

    def _make(**settings_overrides) -> TestClient:
        kwargs: dict[str, object] = dict(
            data_path=os.pathsep.join(str(p) for p in data_paths),
            crmedr_path=crmedr_path,
            clbdr_path=clbdr_path,
        )
        kwargs.update(settings_overrides)
        settings = Settings(_env_file=None, **kwargs)  # pyright: ignore[reportCallIssue]
        return TestClient(create_app(settings))

    return _make


def test_fixture_sanity():
    ids = json.loads((FIXTURES / "crmedr/data/martyrology_ids.json").read_text())
    assert any(e["id"] == "mr:0102-concordius" for e in ids["entries"])
