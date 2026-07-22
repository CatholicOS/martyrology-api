import subprocess
from pathlib import Path

import pytest

from martyrology_api.writer.base import ConflictError
from martyrology_api.writer.local import LocalGitBackend

REPO = "CatholicOS/martyrology-api"


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_root(tmp_path) -> Path:
    root = tmp_path / "gitroot"
    bare = root / f"{REPO}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", "."], bare)
    seed = tmp_path / "seed"
    run(["git", "clone", str(bare), str(seed)], tmp_path)
    f = seed / "data/editions/martyrologium_romanum_1749/01.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"1": {"titulus": "t", "elogia": {}, "conclusio": "c"}}')
    run(["git", "add", "-A"], seed)
    run(
        [
            "git",
            "-c",
            "user.name=seed",
            "-c",
            "user.email=s@x",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "seed",
        ],
        seed,
    )
    run(["git", "push", "origin", "main"], seed)
    return root


def test_read_file(git_root):
    b = LocalGitBackend(git_root)
    got = b.read_file(REPO, "main", "data/editions/martyrologium_romanum_1749/01.json")
    assert got is not None
    content, sha = got
    assert b'"titulus": "t"' in content and len(sha) == 40
    assert b.read_file(REPO, "main", "nope.json") is None


def test_ensure_branch_and_write(git_root):
    b = LocalGitBackend(git_root)
    b.ensure_branch(REPO, "curation/jdoe/edits")
    commit = b.write_file(
        REPO,
        "curation/jdoe/edits",
        "data/editions/martyrologium_romanum_1749/01.json",
        b'{"1": {"titulus": "T2", "elogia": {}, "conclusio": "c"}}',
        "curation: fix titulus",
        "J. Doe",
        "j@example.org",
    )
    assert len(commit) == 40
    content, _ = b.read_file(
        REPO, "curation/jdoe/edits", "data/editions/martyrologium_romanum_1749/01.json"
    )
    assert b'"titulus": "T2"' in content
    # main untouched
    content_main, _ = b.read_file(REPO, "main", "data/editions/martyrologium_romanum_1749/01.json")
    assert b'"titulus": "t"' in content_main
    # author recorded
    log = subprocess.run(
        [
            "git",
            "-C",
            str(git_root / f"{REPO}.git"),
            "log",
            "-1",
            "--format=%an <%ae>",
            "curation/jdoe/edits",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert log == "J. Doe <j@example.org>"


def test_write_new_file_and_conflict(git_root):
    b = LocalGitBackend(git_root)
    b.write_file(
        REPO, "curation/jdoe/edits", "data/new.json", b"{}", "curation: new file", "J", "j@x"
    )
    _, sha = b.read_file(REPO, "curation/jdoe/edits", "data/new.json")
    b.write_file(
        REPO,
        "curation/jdoe/edits",
        "data/new.json",
        b'{"a": 1}',
        "ok",
        "J",
        "j@x",
        expected_sha=sha,
    )
    with pytest.raises(ConflictError):
        b.write_file(
            REPO,
            "curation/jdoe/edits",
            "data/new.json",
            b'{"b": 2}',
            "stale",
            "J",
            "j@x",
            expected_sha=sha,
        )  # sha now stale


def test_open_pr_local(git_root):
    b = LocalGitBackend(git_root)
    assert b.open_pr(REPO, "curation/jdoe/edits", "title") == f"local://{REPO}/curation/jdoe/edits"
