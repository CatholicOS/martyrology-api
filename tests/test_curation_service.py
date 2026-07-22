import json
import subprocess
from pathlib import Path

import pytest

from martyrology_api.auth import Identity
from martyrology_api.config import Settings
from martyrology_api.problems import ApiProblem
from martyrology_api.registry import Registry
from martyrology_api.writer.local import LocalGitBackend
from martyrology_api.writer.service import CurationService

PUB = "CatholicOS/martyrology-api"
PRIV = "CatholicOS/martyrology-texts"
IDENT = Identity(subject="u123", username="jdoe", email="j@example.org")


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def seed_repo(root: Path, repo: str, files: dict[str, str]):
    bare = root / f"{repo}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", "."], bare)
    seed = root / f"seed-{repo.replace('/', '-')}"
    run(["git", "clone", str(bare), str(seed)], root)
    for rel, content in files.items():
        f = seed / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    run(["git", "add", "-A"], seed)
    run(
        [
            "git",
            "-c",
            "user.name=s",
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


@pytest.fixture
def service(tmp_path, crmedr_path, clbdr_path):
    root = tmp_path / "gitroot"
    seed_repo(
        root,
        PUB,
        {
            "data/editions/martyrologium_romanum_1749/edition.json": '{"shape": "day-structured"}',
            "data/editions/martyrologium_romanum_1749/01.json": json.dumps(
                {
                    "1": {
                        "titulus": "t1",
                        "elogia": {
                            "mr:0101-circumcisio-domini": "Circumcisio.",
                            "mr:0102-concordius": "Spoleti Concordii.",
                        },
                        "conclusio": "c",
                    },
                    "2": {
                        "titulus": "t2",
                        "elogia": {"mr:0102-argeus-et-socii": "Tomis Argei."},
                        "conclusio": "c",
                    },
                }
            ),
        },
    )
    seed_repo(
        root,
        PRIV,
        {
            "data/editions/martyrologium_romanum_2004/edition.json": '{"shape": "flat"}',
            "data/editions/martyrologium_romanum_2004/01.json": '{"mr:0101-basilius": "Basilii."}',
        },
    )
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        crmedr_path=crmedr_path,
        clbdr_path=clbdr_path,
        local_git_root=str(root),
    )
    registry = Registry.load(crmedr_path, clbdr_path)
    return CurationService(LocalGitBackend(root), registry, settings)


def read_branch_json(service, repo, branch, path):
    content, _ = service.backend.read_file(repo, branch, path)
    return json.loads(content)


def test_routing_and_branch_naming(service):
    assert service.repo_for("martyrologium_romanum_1749") == PUB
    assert service.repo_for("martyrologium_romanum_2004") == PRIV
    assert service.branch_for(IDENT, None) == "curation/jdoe/edits"
    assert service.branch_for(IDENT, "ocr-fixes") == "curation/jdoe/ocr-fixes"


def test_patch_elogium_day_structured(service):
    r = service.patch_elogium(
        IDENT,
        "martyrologium_romanum_1749",
        "mr:0102-concordius",
        "Spoleti sancti Concordii, emend.",
        topic=None,
        if_match=None,
    )
    assert r.branch == "curation/jdoe/edits" and r.pr_url.startswith("local://")
    raw = read_branch_json(
        service, PUB, r.branch, "data/editions/martyrologium_romanum_1749/01.json"
    )
    assert raw["1"]["elogia"]["mr:0102-concordius"].endswith("emend.")


def test_patch_elogium_unregistered_edition_is_422(service):
    with pytest.raises(ApiProblem) as ei:
        service.patch_elogium(
            IDENT, "not_registered_x", "mr:0102-concordius", "x", topic=None, if_match=None
        )
    assert ei.value.status == 422
    assert ei.value.type_slug == "unknown-edition"


@pytest.mark.parametrize("cid", ["not-even-canonical", "mr:9999-nobody"])
def test_patch_elogium_invalid_canonical_id_is_422(service, cid):
    with pytest.raises(ApiProblem) as ei:
        service.patch_elogium(
            IDENT, "martyrologium_romanum_1749", cid, "x", topic=None, if_match=None
        )
    assert ei.value.status == 422
    assert ei.value.type_slug == "invalid-payload"


@pytest.mark.parametrize("cid", ["not-even-canonical", "mr:9999-nobody"])
def test_delete_elogium_invalid_canonical_id_is_422(service, cid):
    with pytest.raises(ApiProblem) as ei:
        service.delete_elogium(IDENT, "martyrologium_romanum_1749", cid, topic=None, if_match=None)
    assert ei.value.status == 422
    assert ei.value.type_slug == "invalid-payload"


def test_put_elogium_with_position(service):
    service.put_elogium(
        IDENT,
        "martyrologium_romanum_1749",
        "mr:0101-basilius",
        "Basilii text.",
        day=1,
        position=1,
        topic=None,
        if_match=None,
    )
    raw = read_branch_json(
        service, PUB, "curation/jdoe/edits", "data/editions/martyrologium_romanum_1749/01.json"
    )
    assert list(raw["1"]["elogia"])[0] == "mr:0101-basilius"


def test_put_elogium_day_required(service):
    with pytest.raises(ApiProblem) as ei:
        service.put_elogium(
            IDENT,
            "martyrologium_romanum_1749",
            "mr:0101-basilius",
            "x",
            day=None,
            position=None,
            topic=None,
            if_match=None,
        )
    assert ei.value.status == 422


def test_delete_elogium_cross_day(service):
    service.delete_elogium(
        IDENT, "martyrologium_romanum_1749", "mr:0102-concordius", topic=None, if_match=None
    )
    raw = read_branch_json(
        service, PUB, "curation/jdoe/edits", "data/editions/martyrologium_romanum_1749/01.json"
    )
    assert "mr:0102-concordius" not in raw["1"]["elogia"]  # was printed under day 1
    with pytest.raises(ApiProblem) as ei:
        service.delete_elogium(
            IDENT, "martyrologium_romanum_1749", "mr:0102-concordius", topic=None, if_match=None
        )
    assert ei.value.status == 404


def test_patch_day_order_and_fields(service):
    service.patch_day(
        IDENT,
        "martyrologium_romanum_1749",
        1,
        1,
        {"titulus": "Nova", "order": ["mr:0102-concordius", "mr:0101-circumcisio-domini"]},
        topic=None,
        if_match=None,
    )
    raw = read_branch_json(
        service, PUB, "curation/jdoe/edits", "data/editions/martyrologium_romanum_1749/01.json"
    )
    assert raw["1"]["titulus"] == "Nova"
    assert list(raw["1"]["elogia"])[0] == "mr:0102-concordius"
    with pytest.raises(ApiProblem) as ei:
        service.patch_day(
            IDENT,
            "martyrologium_romanum_1749",
            1,
            1,
            {"order": ["mr:0101-basilius"]},
            topic=None,
            if_match=None,
        )
    assert ei.value.status == 422  # not a permutation


def test_flat_edition_write(service):
    service.patch_elogium(
        IDENT,
        "martyrologium_romanum_2004",
        "mr:0101-basilius",
        "Basilii Magni, emend.",
        topic=None,
        if_match=None,
    )
    raw = read_branch_json(
        service, PRIV, "curation/jdoe/edits", "data/editions/martyrologium_romanum_2004/01.json"
    )
    assert raw["mr:0101-basilius"].endswith("emend.")


def test_put_month_validates(service):
    with pytest.raises(ApiProblem) as ei:
        service.put_month(
            IDENT,
            "martyrologium_romanum_1749",
            1,
            {"1": {"elogia": {"mr:9999-nobody": "x"}}},
            topic=None,
            if_match=None,
        )
    assert ei.value.status == 422


def test_create_edition(service):
    service.create_edition(
        IDENT,
        "martyrologium_romanum_1914_en_unofficial",
        shape="day-structured",
        note=None,
        topic="digitize",
    )
    raw = read_branch_json(
        service,
        PUB,
        "curation/jdoe/digitize",
        "data/editions/martyrologium_romanum_1914_en_unofficial/edition.json",
    )
    assert raw["shape"] == "day-structured"
    with pytest.raises(ApiProblem) as ei:
        service.create_edition(IDENT, "not_registered_2050", "flat", None, None)
    assert ei.value.status == 422


def test_create_edition_probes_actual_default_branch(tmp_path, crmedr_path, clbdr_path):
    # A repo whose default branch is NOT "main": create_edition's
    # already-exists check must probe the real default branch, not a
    # hardcoded "main", or it will silently miss an existing edition.
    root = tmp_path / "gitroot-trunk"
    bare = root / f"{PUB}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "trunk", "."], bare)
    seed = root / "seed"
    run(["git", "clone", str(bare), str(seed)], root)
    f = seed / "data/editions/martyrologium_romanum_1914_en_unofficial/edition.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"shape": "day-structured"}')
    run(["git", "add", "-A"], seed)
    run(
        [
            "git",
            "-c",
            "user.name=s",
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
    run(["git", "push", "origin", "trunk"], seed)

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        crmedr_path=crmedr_path,
        clbdr_path=clbdr_path,
        local_git_root=str(root),
    )
    registry = Registry.load(crmedr_path, clbdr_path)
    svc = CurationService(LocalGitBackend(root), registry, settings)
    with pytest.raises(ApiProblem) as ei:
        svc.create_edition(
            IDENT,
            "martyrologium_romanum_1914_en_unofficial",
            shape="day-structured",
            note=None,
            topic=None,
        )
    assert ei.value.status == 409


def test_read_month_draft(service):
    service.patch_elogium(
        IDENT,
        "martyrologium_romanum_1749",
        "mr:0102-argeus-et-socii",
        "Draft text.",
        topic=None,
        if_match=None,
    )
    days = service.read_month_draft("martyrologium_romanum_1749", 1, "curation/jdoe/edits")
    assert days[2].elogia[0].text == "Draft text."
