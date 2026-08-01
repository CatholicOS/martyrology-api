import importlib.util
import json
import tarfile
from pathlib import Path

from martyrology_api import manifest as runtime_manifest
from martyrology_api.manifest import Manifest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "build_bundle.py"
_spec = importlib.util.spec_from_file_location("build_bundle", _PATH)
assert _spec is not None and _spec.loader is not None
build_bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_bundle)

COMMITS = {"texts": "t" * 40, "crmedr": "c" * 40, "clbdr": "l" * 40}


def _staging(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    (root / "data" / "crmedr").mkdir(parents=True)
    (root / "wheels").mkdir()
    (root / "data" / "crmedr" / "ids.json").write_text("{}", encoding="utf-8")
    (root / "wheels" / "fake.whl").write_bytes(b"PK\x03\x04")
    return root


def test_sha256_file_matches_known_digest(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"abc")
    assert build_bundle.sha256_file(target) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_tree_uses_sorted_posix_relative_keys(tmp_path: Path):
    root = _staging(tmp_path)
    tree = build_bundle.hash_tree(root)
    assert list(tree) == sorted(tree)
    assert "data/crmedr/ids.json" in tree
    assert "wheels/fake.whl" in tree
    assert not any(key.startswith("/") for key in tree)


def test_build_manifest_records_format_and_commits(tmp_path: Path):
    manifest = build_bundle.build_manifest(_staging(tmp_path), "0.1.0", "a" * 40, COMMITS)
    assert manifest["bundle_format"] == 1
    assert manifest["api_commit"] == "a" * 40
    assert manifest["data"] == COMMITS


def test_build_manifest_validates_against_the_runtime_model(tmp_path: Path):
    """The writer and the reader must agree; this is the contract between
    scripts/deploy/build_bundle.py and src/martyrology_api/manifest.py."""
    manifest = build_bundle.build_manifest(_staging(tmp_path), "0.1.0", "a" * 40, COMMITS)
    parsed = Manifest.model_validate(manifest)
    assert parsed.api_version == "0.1.0"


def test_bundle_format_constants_agree():
    """BUNDLE_FORMAT is declared in both modules. If they ever drift, the
    deploy script's manifest check rejects every bundle CI produces, so
    pin them together here rather than discovering it on the VPS."""
    assert build_bundle.BUNDLE_FORMAT == runtime_manifest.BUNDLE_FORMAT


def test_assemble_writes_a_tarball_with_a_manifest_at_the_root(tmp_path: Path):
    root = _staging(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    tarball = build_bundle.assemble(root, out, "1.2.3")
    assert tarball.name == "martyrology-1.2.3-linux-x86_64-cp312.tar.gz"
    with tarfile.open(tarball) as archive:
        names = archive.getnames()
    assert "manifest.json" in names
    assert "data/crmedr/ids.json" in names


def test_assemble_manifest_does_not_hash_itself(tmp_path: Path):
    root = _staging(tmp_path)
    build_bundle.write_manifest(root, "0.1.0", "a" * 40, COMMITS)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "manifest.json" not in manifest["files"]
