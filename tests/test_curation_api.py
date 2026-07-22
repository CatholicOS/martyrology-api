import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.auth import Identity
from martyrology_api.config import Settings

PUB = "CatholicOS/martyrology-api"


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def seed_repo(root: Path, repo: str, files: dict[str, str]):
    bare = root / f"{repo}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", "."], bare)
    if not files:
        # Nothing to commit; an empty bare repo is enough for backends that
        # only need the repo directory (and HEAD symref) to exist.
        return
    seed = root / f"seed-{repo.replace('/', '-')}"
    run(["git", "clone", str(bare), str(seed)], root)
    for rel, content in files.items():
        f = seed / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    run(["git", "add", "-A"], seed)
    run(["git", "-c", "user.name=s", "-c", "user.email=s@x",
        "-c", "commit.gpgsign=false", "commit", "-m", "s"], seed)
    run(["git", "push", "origin", "main"], seed)


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe",
                        email="j@example.org") if token == "good" else None


class Grants:
    def __init__(self, relations):  # set of (relation, edition_id)
        self.relations = relations

    async def check(self, user, relation, edition_id):
        return (relation, edition_id) in self.relations


@pytest.fixture
def client(tmp_path, crmedr_path, clbdr_path, data_paths):
    root = tmp_path / "gitroot"
    seed_repo(root, PUB, {
        "data/editions/martyrologium_romanum_1749/edition.json":
            '{"shape": "day-structured"}',
        "data/editions/martyrologium_romanum_1749/01.json": json.dumps({
            "1": {"titulus": "t1", "elogia": {"mr:0102-concordius": "Spoleti."},
                  "conclusio": "c"}})})
    seed_repo(root, "CatholicOS/martyrology-texts", {})
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path,
        local_git_root=str(root))
    app = create_app(settings)
    app.state.authenticator = StaticAuth()
    app.state.authz = Grants({("can_edit", "martyrologium_romanum_1749"),
                              ("can_admin", "martyrologium_romanum_1914_en_unofficial")})
    return TestClient(app)


AUTH = {"Authorization": "Bearer good"}


def test_write_requires_auth(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                     json={"text": "x"})
    assert r.status_code == 401
    r = client.patch("/api/v1/editions/martyrologium_romanum_2004/elogia/mr:0101-basilius",
                     json={"text": "x"}, headers=AUTH)
    assert r.status_code == 403  # no can_edit grant on 2004


def test_patch_elogium_roundtrip(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                     json={"text": "Spoleti, emendatum."}, headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["branch"] == "curation/jdoe/edits"
    assert b["pr_url"].startswith("local://")
    assert len(b["commit_sha"]) == 40


def test_draft_read_via_header(client):
    client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                 json={"text": "Draft only."}, headers=AUTH)
    # published read unchanged (store serves the fixture files on disk)
    pub = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01")
    assert pub.json()["elogia"][0]["text"] != "Draft only."
    assert pub.headers["cache-control"] != "private, max-age=0"
    # draft read sees the branch
    draft = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/01/01",
        headers=AUTH | {"X-Curation-Branch": "curation/jdoe/edits"})
    assert draft.json()["elogia"][0]["text"] == "Draft only."
    # C1: draft reads must never be shared-cached
    assert draft.headers["cache-control"] == "private, max-age=0"
    # anonymous caller: header ignored, and stays public
    anon = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/01/01",
        headers={"X-Curation-Branch": "curation/jdoe/edits"})
    assert anon.json()["elogia"][0]["text"] != "Draft only."
    assert anon.headers["cache-control"] != "private, max-age=0"


def test_put_elogium_happy_path(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0101-basilius",
                   json={"text": "Basilii Magni.", "day": 1, "position": 1},
                   headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["branch"] == "curation/jdoe/edits"
    assert len(b["commit_sha"]) == 40


def test_put_elogium_invalid_day_is_422(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0101-basilius",
                   json={"text": "x", "day": 45}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["type"].endswith("invalid-payload")


def test_put_month_validation_error(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1749/01",
                   json={"1": {"elogia": {"mr:9999-nobody": "x"}}}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["errors"]


def test_patch_day(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/01/01",
                     json={"titulus": "Novus titulus"}, headers=AUTH)
    assert r.status_code == 200


def test_delete_elogium(client):
    r = client.request("DELETE",
                       "/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                       headers=AUTH)
    assert r.status_code == 200
    r2 = client.request("DELETE",
                        "/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                        headers=AUTH)
    assert r2.status_code == 404


def test_create_edition_needs_admin(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1914_en_unofficial",
                   json={"shape": "day-structured"}, headers=AUTH)
    assert r.status_code == 201
    r2 = client.put("/api/v1/editions/martyrologium_romanum_1749",
                    json={"shape": "day-structured"}, headers=AUTH)
    assert r2.status_code == 403  # only can_edit, not can_admin, on 1749
