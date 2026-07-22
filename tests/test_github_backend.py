import base64
import json

import httpx2 as httpx
import pytest

from martyrology_api.writer.base import ConflictError
from martyrology_api.writer.github import GitHubBackend

REPO = "CatholicOS/martyrology-texts"


class FakeGitHub:
    def __init__(self):
        self.files = {("main", "data/x.json"): b'{"a": 1}'}
        self.branches = {"main": "c0ffee"}
        self.prs = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        assert request.headers["authorization"] == "Bearer tok"
        if m == "GET" and p == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        if m == "GET" and p == f"/repos/{REPO}/git/ref/heads/locked":
            return httpx.Response(403, json={"message": "forbidden"})
        if m == "GET" and p.startswith(f"/repos/{REPO}/git/ref/heads/"):
            br = p.rsplit("/", 1)[-1]
            if br in self.branches:
                return httpx.Response(200, json={"object": {"sha": self.branches[br]}})
            return httpx.Response(404, json={})
        if m == "POST" and p == f"/repos/{REPO}/git/refs":
            body = json.loads(request.content)
            self.branches[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return httpx.Response(201, json={})
        if m == "GET" and p.startswith(f"/repos/{REPO}/contents/"):
            path = p.removeprefix(f"/repos/{REPO}/contents/")
            ref = request.url.params.get("ref", "main")
            content = self.files.get((ref, path))
            if content is None:
                return httpx.Response(404, json={})
            return httpx.Response(
                200,
                json={"content": base64.b64encode(content).decode(), "sha": f"blob-{ref}-{path}"},
            )
        if m == "PUT" and p.startswith(f"/repos/{REPO}/contents/"):
            path = p.removeprefix(f"/repos/{REPO}/contents/")
            body = json.loads(request.content)
            branch = body["branch"]
            existing = self.files.get((branch, path))
            if existing is not None and body.get("sha") != f"blob-{branch}-{path}":
                return httpx.Response(409, json={"message": "sha mismatch"})
            self.files[(branch, path)] = base64.b64decode(body["content"])
            return httpx.Response(
                200, json={"commit": {"sha": "abc123"}, "content": {"sha": "newblob"}}
            )
        if m == "GET" and p == f"/repos/{REPO}/pulls":
            return httpx.Response(200, json=self.prs)
        if m == "POST" and p == f"/repos/{REPO}/pulls":
            pr = {"html_url": f"https://github.com/{REPO}/pull/1"}
            self.prs.append(pr)
            return httpx.Response(201, json=pr)
        return httpx.Response(500, json={"unhandled": p})


@pytest.fixture
def gh():
    fake = FakeGitHub()
    backend = GitHubBackend("tok", transport=httpx.MockTransport(fake.handler))
    return fake, backend


def test_read_file(gh):
    _, b = gh
    content, sha = b.read_file(REPO, "main", "data/x.json")
    assert content == b'{"a": 1}' and sha == "blob-main-data/x.json"
    assert b.read_file(REPO, "main", "nope.json") is None


def test_ensure_branch_creates_from_default(gh):
    fake, b = gh
    b.ensure_branch(REPO, "curation/jdoe/edits")
    assert fake.branches["curation/jdoe/edits"] == "c0ffee"
    b.ensure_branch(REPO, "curation/jdoe/edits")  # idempotent


def test_ensure_branch_raises_on_unexpected_status(gh):
    _, b = gh
    with pytest.raises(httpx.HTTPStatusError):
        b.ensure_branch(REPO, "locked")


def test_write_new_and_update_and_conflict(gh):
    fake, b = gh
    b.ensure_branch(REPO, "curation/jdoe/edits")
    sha = b.write_file(REPO, "curation/jdoe/edits", "data/y.json", b"{}", "msg", "J", "j@x")
    assert sha == "abc123"
    # update with correct current sha (auto-fetched)
    b.write_file(REPO, "curation/jdoe/edits", "data/y.json", b'{"b": 2}', "msg2", "J", "j@x")
    with pytest.raises(ConflictError):
        b.write_file(
            REPO,
            "curation/jdoe/edits",
            "data/y.json",
            b'{"c": 3}',
            "msg3",
            "J",
            "j@x",
            expected_sha="stale-sha",
        )


def test_open_pr_idempotent(gh):
    fake, b = gh
    url = b.open_pr(REPO, "curation/jdoe/edits", "Curation")
    assert url == f"https://github.com/{REPO}/pull/1"
    assert b.open_pr(REPO, "curation/jdoe/edits", "Curation") == url
    assert len(fake.prs) == 1
