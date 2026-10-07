"""The copyrighted editions' texts shown to anyone ("public", the default), and the switch that
puts them back behind sign-in (MARTYROLOGY_RESTRICTED_TEXTS_ACCESS)."""

import pytest

from martyrology_api.config import Settings
from martyrology_api.writer.service import CurationService

ED = "martyrologium_romanum_2004"


@pytest.fixture
def client(make_client):
    return make_client(restricted_texts_access="public")


def test_the_default_shows_the_texts(monkeypatch):
    monkeypatch.delenv("MARTYROLOGY_RESTRICTED_TEXTS_ACCESS", raising=False)
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.restricted_texts_access == "public"
    assert s.gated_set == set()
    assert ED in s.restricted_set  # still a copyrighted edition


@pytest.mark.parametrize("rule", ["authenticated", "grant"])
def test_the_environment_puts_them_back_behind_sign_in(monkeypatch, rule):
    monkeypatch.setenv("MARTYROLOGY_RESTRICTED_TEXTS_ACCESS", rule)
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.restricted_texts_access == rule and ED in s.gated_set


def test_anonymous_callers_read_the_copyrighted_texts(client):
    r = client.get(f"/api/v1/elogia/edition/{ED}/01/01")
    b = r.json()
    assert b["metadata"]["access"] == "public" and b["metadata"]["access_info"] is None
    assert b["elogia"][0]["text"].startswith("In octava")
    # nothing per caller left in the response: it can be shared-cached
    assert r.headers["cache-control"].startswith("public")


def test_the_shelf_and_the_access_map_show_them_open(client):
    access = client.get("/api/v1/access").json()["editions"]
    assert access[ED] == {"can_read_texts": True}
    eds = client.get("/api/v1/editions").json()["editions"]
    e = next(x for x in eds if x["edition_id"] == ED)
    assert e["availability"]["status"] == "public"


def test_the_gate_still_works_when_switched_on(make_client):
    b = (
        make_client(restricted_texts_access="authenticated")
        .get(f"/api/v1/elogia/edition/{ED}/01/01")
        .json()
    )
    assert b["metadata"]["access"] == "restricted-texts"
    assert all(e["text"] is None for e in b["elogia"])


def test_curation_still_writes_copyrighted_editions_to_the_private_repo():
    s = Settings(_env_file=None, restricted_texts_access="public")  # pyright: ignore[reportCallIssue]
    svc = CurationService(backend=None, registry=None, settings=s)  # pyright: ignore[reportArgumentType]
    assert svc.repo_for(ED) == s.private_repo
    assert svc.repo_for("martyrologium_romanum_1749") == s.public_repo
