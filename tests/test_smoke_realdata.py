from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.config import Settings

ROOT = Path(__file__).parent.parent
CRMEDR = ROOT.parent / "crmedr"
CLBDR = ROOT.parent / "clbdr"

pytestmark = pytest.mark.skipif(
    not (CRMEDR.is_dir() and CLBDR.is_dir() and (ROOT / "data/editions").is_dir()),
    reason="real registries/data not present",
)


@pytest.fixture(scope="module")
def client():
    settings = Settings(
        _env_file=None, data_path=str(ROOT / "data/editions"), crmedr_path=CRMEDR, clbdr_path=CLBDR
    )
    return TestClient(create_app(settings))


def test_editions_lists_the_clbdr_line(client):
    eds = {e["edition_id"] for e in client.get("/api/v1/editions").json()["editions"]}
    assert "martyrologium_romanum_1749" in eds
    assert "martyrologium_romanum_2004" in eds


def test_1749_january_second(client):
    r = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/02")
    assert r.status_code == 200
    b = r.json()
    assert b["titulus"] and b["conclusio"]
    assert len(b["elogia"]) > 0
    assert all(e["id"].startswith("mr:") for e in b["elogia"])


def test_year_resolver_hits_1749(client):
    r = client.get("/api/v1/elogia/1800/01/02")
    assert r.status_code == 200
    assert r.json()["metadata"]["edition"] == "martyrologium_romanum_1749"


def test_full_year_no_crashes(client):
    from martyrology_api.grammar import DAYS_IN_MONTH

    for month in DAYS_IN_MONTH:
        r = client.get(f"/api/v1/elogia/edition/martyrologium_romanum_1749/{month:02d}")
        assert r.status_code == 200, f"month {month}"
        assert len(r.json()["days"]) > 25


def test_catalog_size_matches_registry(client):
    items = client.get("/api/v1/elogia").json()["elogia"]
    assert len(items) > 3000  # current + deprecated CRMEDR ids
