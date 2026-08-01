import json
from pathlib import Path

from martyrology_api.manifest import load_manifest

GOOD = {
    "bundle_format": 1,
    "api_version": "0.1.0",
    "api_commit": "a" * 40,
    "data": {"texts": "t" * 40, "crmedr": "c" * 40, "clbdr": "l" * 40},
    "python_requires": ">=3.12",
    "files": {"data/crmedr/x.json": "0" * 64},
}


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_none_path_yields_none():
    assert load_manifest(None) is None


def test_missing_file_yields_none(tmp_path: Path):
    assert load_manifest(tmp_path / "absent.json") is None


def test_malformed_json_yields_none(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_manifest(path) is None


def test_missing_required_field_yields_none(tmp_path: Path):
    payload = {k: v for k, v in GOOD.items() if k != "api_commit"}
    assert load_manifest(_write(tmp_path, payload)) is None


def test_unknown_bundle_format_yields_none(tmp_path: Path):
    assert load_manifest(_write(tmp_path, {**GOOD, "bundle_format": 99})) is None


def test_good_manifest_parses(tmp_path: Path):
    manifest = load_manifest(_write(tmp_path, GOOD))
    assert manifest is not None
    assert manifest.api_commit == "a" * 40
    assert manifest.data["texts"] == "t" * 40
    assert manifest.files["data/crmedr/x.json"] == "0" * 64
