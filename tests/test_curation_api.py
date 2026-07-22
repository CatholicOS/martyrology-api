import json
import subprocess
from pathlib import Path

import pytest

from martyrology_api.auth import Identity

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
def client(tmp_path, make_client):
    root = tmp_path / "gitroot"
    seed_repo(root, PUB, {
        "data/editions/martyrologium_romanum_1749/edition.json":
            '{"shape": "day-structured"}',
        "data/editions/martyrologium_romanum_1749/01.json": json.dumps({
            "1": {"titulus": "t1", "elogia": {"mr:0102-concordius": "Spoleti."},
                  "conclusio": "c"}})})
    seed_repo(root, "CatholicOS/martyrology-texts", {})
    c = make_client(local_git_root=str(root))
    c.app.state.authenticator = StaticAuth()
    c.app.state.authz = Grants({("can_edit", "martyrologium_romanum_1749"),
                                ("can_admin", "martyrologium_romanum_1914_en_unofficial")})
    return c


AUTH = {"Authorization": "Bearer good"}


def test_curation_unconfigured_is_503(make_client):
    c = make_client()  # no local_git_root, no github_token
    c.app.state.authenticator = StaticAuth()
    c.app.state.authz = Grants({("can_edit", "martyrologium_romanum_1749")})
    r = c.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
               json={"text": "x"}, headers=AUTH)
    assert r.status_code == 503
    assert r.json()["type"].endswith("curation-unconfigured")


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


def test_draft_falls_back_to_published_when_month_file_absent(client):
    # Create the curation branch via a write that only touches month 01;
    # month 02 is never written on this branch (it was never seeded on
    # main either), so it must fall back to the published fixture data
    # instead of appearing empty.
    client.patch("/api/v1/editions/martyrologium_romanum_1749/01/01",
                 json={"titulus": "X"}, headers=AUTH)
    pub = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/02")
    draft = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/02",
        headers=AUTH | {"X-Curation-Branch": "curation/jdoe/edits"})
    assert pub.status_code == 200 and draft.status_code == 200
    assert pub.json()["days"] != {}
    assert draft.json()["days"] == pub.json()["days"]


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


def test_invalid_topic_is_422(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                     json={"text": "x"}, headers=AUTH,
                     params={"topic": "../../evil"})
    assert r.status_code == 422
    assert r.json()["type"].endswith("invalid-topic")


def test_branch_header_traversal_is_ignored(client):
    client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                 json={"text": "Draft only."}, headers=AUTH)
    r = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/01/01",
        headers=AUTH | {"X-Curation-Branch": "curation/x/../../evil"})
    assert r.status_code == 200
    assert r.json()["elogia"][0]["text"] != "Draft only."


def test_create_edition_needs_admin(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1914_en_unofficial",
                   json={"shape": "day-structured"}, headers=AUTH)
    assert r.status_code == 201
    r2 = client.put("/api/v1/editions/martyrologium_romanum_1749",
                    json={"shape": "day-structured"}, headers=AUTH)
    assert r2.status_code == 403  # only can_edit, not can_admin, on 1749


def test_patch_edition_explicit_null_removes_key(client):
    edition_id = "martyrologium_romanum_1914_en_unofficial"
    client.put(f"/api/v1/editions/{edition_id}",
              json={"shape": "day-structured", "note": "orig"}, headers=AUTH)
    r = client.patch(f"/api/v1/editions/{edition_id}",
                     json={"note": "temp"}, headers=AUTH)
    assert r.status_code == 200
    r2 = client.patch(f"/api/v1/editions/{edition_id}",
                      json={"note": None}, headers=AUTH)
    assert r2.status_code == 200
    svc = client.app.state.curation
    raw = json.loads(svc.backend.read_file(
        "CatholicOS/martyrology-api", "curation/jdoe/edits",
        svc.edition_meta_path(edition_id))[0])
    assert "note" not in raw
