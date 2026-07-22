import json
from pathlib import Path

import pytest

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


def test_fixture_sanity():
    ids = json.loads((FIXTURES / "crmedr/data/martyrology_ids.json").read_text())
    assert any(e["id"] == "mr:0102-concordius" for e in ids["entries"])
