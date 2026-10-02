import pytest

from martyrology_api.auth import Identity


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe") if token == "good" else None


class GrantReaders:
    def __init__(self, allowed_editions):
        self.allowed = allowed_editions

    async def check(self, user, relation, edition_id):
        return relation == "can_read_texts" and edition_id in self.allowed


@pytest.fixture
def client(make_client):
    c = make_client()
    c.app.state.authenticator = StaticAuth()
    c.app.state.authz = GrantReaders({"martyrologium_romanum_2004"})
    return c


def test_anonymous_can_read_only_unrestricted_editions(client):
    r = client.get("/api/v1/access")
    assert r.status_code == 200
    editions = r.json()["editions"]
    assert editions["martyrologium_romanum_1749"] == {"can_read_texts": True}
    # Not restricted, so readable — the shelf separately shows it as unavailable.
    assert editions["martyrologium_romanum_1584"] == {"can_read_texts": True}
    assert editions["martyrologium_romanum_2004"] == {"can_read_texts": False}
    assert editions["martyrologium_romanum_2004_it_IT"] == {"can_read_texts": False}


def test_covers_every_registered_edition(client):
    ids = {e["edition_id"] for e in client.get("/api/v1/editions").json()["editions"]}
    assert set(client.get("/api/v1/access").json()["editions"]) == ids


def test_authorized_caller_gets_granted_edition_only(client):
    editions = client.get("/api/v1/access", headers={"Authorization": "Bearer good"}).json()[
        "editions"
    ]
    assert editions["martyrologium_romanum_2004"] == {"can_read_texts": True}
    assert editions["martyrologium_romanum_2004_it_IT"] == {"can_read_texts": False}


def test_response_is_private(client):
    r = client.get("/api/v1/access")
    assert r.headers["cache-control"] == "private, max-age=0"


def test_bad_token_is_401(client):
    r = client.get("/api/v1/access", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
