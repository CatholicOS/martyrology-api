import os

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.auth import Identity
from martyrology_api.config import Settings


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe") if token == "good" else None


class GrantReaders:
    def __init__(self, allowed_editions):
        self.allowed = allowed_editions

    async def check(self, user, relation, edition_id):
        return relation == "can_read_texts" and edition_id in self.allowed


@pytest.fixture
def client(crmedr_path, clbdr_path, data_paths):
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path)
    app = create_app(settings)
    app.state.authenticator = StaticAuth()
    app.state.authz = GrantReaders({"martyrologium_romanum_2004"})
    return TestClient(app)


def test_anonymous_restricted_is_redacted_200(client):
    r = client.get("/api/v1/elogia/01/01")
    assert r.status_code == 200
    b = r.json()
    assert b["metadata"]["access"] == "restricted-texts"
    assert b["metadata"]["access_info"]
    assert all(e["text"] is None for e in b["elogia"])
    assert [e["id"] for e in b["elogia"]] == ["mr:0101-maria-dei-genetrix",
                                              "mr:0101-basilius"]  # skeleton stays public


def test_public_edition_untouched(client):
    b = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    assert b["metadata"]["access"] == "public"
    assert b["elogia"][0]["text"] is not None


def test_authorized_gets_texts(client):
    r = client.get("/api/v1/elogia/01/01", headers={"Authorization": "Bearer good"})
    b = r.json()
    assert b["metadata"]["access"] == "public"
    assert b["elogia"][0]["text"].startswith("In octava")


def test_authorized_but_ungranted_edition_still_redacted(client):
    b = client.get("/api/v1/elogia/nation/IT/01/01",
                   headers={"Authorization": "Bearer good"}).json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004_it_IT"
    assert b["metadata"]["access"] == "restricted-texts"


def test_bad_token_is_401(client):
    r = client.get("/api/v1/elogia/01/01", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_elogium_redacts_per_edition(client):
    b = client.get("/api/v1/elogium/mr:0102-concordius").json()
    assert b["editions"]["martyrologium_romanum_1749"]["text"] is not None
    assert b["editions"]["martyrologium_romanum_2004"]["text"] is None
    assert b["editions"]["martyrologium_romanum_2004_it_IT"]["text"] is None


def test_month_redaction(client):
    b = client.get("/api/v1/elogia/01").json()
    assert b["metadata"]["access"] == "restricted-texts"
    assert all(e["text"] is None
               for day in b["days"].values() for e in day["elogia"])
