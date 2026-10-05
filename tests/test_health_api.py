import json
import os
from pathlib import Path

MANIFEST = {
    "bundle_format": 1,
    "api_version": "0.1.0",
    "api_commit": "a" * 40,
    "data": {"texts": "t" * 40, "crmedr": "c" * 40, "clbdr": "l" * 40},
    "python_requires": ">=3.12",
    "files": {},
}


def test_healthz_ok_without_a_manifest(make_client):
    body = make_client().get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["data"] == {"crmedr": None, "clbdr": None, "texts": None}


def test_healthz_lists_available_editions_sorted(make_client):
    body = make_client().get("/healthz").json()
    assert body["editions"], "fixtures should expose at least one edition"
    assert body["editions"] == sorted(body["editions"])


def test_healthz_reports_commits_from_the_manifest(make_client, tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    body = make_client(manifest_path=str(path)).get("/healthz").json()
    assert body["data"] == {"crmedr": "c" * 40, "clbdr": "l" * 40, "texts": "t" * 40}


def test_healthz_survives_a_corrupt_manifest(make_client, tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    response = make_client(manifest_path=str(path)).get("/healthz")
    assert response.status_code == 200
    assert response.json()["data"] == {"crmedr": None, "clbdr": None, "texts": None}


def test_healthz_reports_catalogued_and_attached_counts(make_client):
    client = make_client()
    body = client.get("/healthz").json()
    assert body["editions_attached"] == len(body["editions"])
    assert body["editions_catalogued"] == len(client.app.state.registry.editions)
    # The fixtures catalogue editions with no text attached (a transcription
    # backlog), so the two counts must differ for the gap to be legible.
    assert body["editions_catalogued"] > body["editions_attached"]


def test_healthz_lists_no_uncatalogued_editions_for_consistent_data(make_client):
    body = make_client().get("/healthz").json()
    assert body["editions_uncatalogued"] == []


def test_healthz_surfaces_attached_but_uncatalogued_editions(
    make_client, data_paths: list[Path], tmp_path: Path
):
    stray = tmp_path / "martyrologium_romanum_9999"
    stray.mkdir()
    (stray / "01.json").write_text("{}", encoding="utf-8")
    data_path = os.pathsep.join(str(p) for p in [*data_paths, tmp_path])
    body = make_client(data_path=data_path).get("/healthz").json()
    assert body["editions_uncatalogued"] == ["martyrologium_romanum_9999"]
    assert "martyrologium_romanum_9999" in body["editions"]
    assert body["editions_attached"] == len(body["editions"])


def test_attached_but_uncatalogued_editions_warn_at_startup(
    make_client, data_paths: list[Path], tmp_path: Path, caplog
):
    stray = tmp_path / "martyrologium_romanum_9999"
    stray.mkdir()
    (stray / "01.json").write_text("{}", encoding="utf-8")
    data_path = os.pathsep.join(str(p) for p in [*data_paths, tmp_path])
    with caplog.at_level("WARNING", logger="martyrology_api.app"):
        make_client(data_path=data_path)
    assert "martyrologium_romanum_9999" in caplog.text
