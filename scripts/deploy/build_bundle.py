#!/usr/bin/env python3
"""Assemble a martyrology-api release bundle.

Takes a staging directory already populated with `wheels/` and `data/`,
writes `manifest.json` into it, and tars the result. Run by
.github/workflows/deploy.yml; the manifest it writes is read at runtime by
src/martyrology_api/manifest.py, and tests/test_build_bundle.py asserts the
two agree.
"""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

BUNDLE_FORMAT = 1
PYTHON_REQUIRES = ">=3.12"
BUNDLE_NAME = "martyrology-{version}-linux-x86_64-cp312.tar.gz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    """sha256 of every regular file under root, keyed by POSIX-style relative
    path and sorted so the manifest is byte-stable across runs."""
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def git_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_manifest(
    staging: Path, api_version: str, api_commit: str, data_commits: dict[str, str]
) -> dict:
    return {
        "bundle_format": BUNDLE_FORMAT,
        "api_version": api_version,
        "api_commit": api_commit,
        "data": data_commits,
        "python_requires": PYTHON_REQUIRES,
        "files": hash_tree(staging),
    }


def write_manifest(
    staging: Path, api_version: str, api_commit: str, data_commits: dict[str, str]
) -> Path:
    """Hash the staged tree, then write the manifest into it. Order matters:
    the manifest cannot contain its own digest, so it is built first."""
    manifest = build_manifest(staging, api_version, api_commit, data_commits)
    path = staging / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def assemble(staging: Path, out_dir: Path, version: str) -> Path:
    """Tar the staged tree. Refuses to build a bundle with no manifest: a
    tarball without provenance would pass deploy.sh's manifest check and
    serve happily with an empty audit trail, which is the one failure this
    design exists to prevent. Call write_manifest() first."""
    if not (staging / "manifest.json").exists():
        raise FileNotFoundError("write_manifest() must run before assemble()")
    tarball = out_dir / BUNDLE_NAME.format(version=version)
    with tarfile.open(tarball, "w:gz") as archive:
        for path in sorted(staging.rglob("*")):
            # recursive=False is load-bearing: tarfile.add() recurses by
            # default, so adding a directory would add its whole subtree and
            # then rglob would yield each of those files again and add them a
            # second (or third) time. rglob already walks the tree, so every
            # entry — directories included, since their modes are what
            # deploy.sh later normalises — is added exactly once, in sorted
            # order.
            archive.add(path, arcname=path.relative_to(staging).as_posix(), recursive=False)
    return tarball


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--api-version", required=True)
    parser.add_argument("--repo-root", default=Path("."), type=Path)
    args = parser.parse_args()

    data_commits = {
        "texts": git_commit(args.repo_root / "vendor" / "texts"),
        "crmedr": git_commit(args.repo_root / "vendor" / "crmedr"),
        "clbdr": git_commit(args.repo_root / "vendor" / "clbdr"),
    }
    write_manifest(args.staging, args.api_version, git_commit(args.repo_root), data_commits)
    tarball = assemble(args.staging, args.out, args.version)
    print(tarball)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
