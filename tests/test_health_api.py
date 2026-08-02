import json
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
