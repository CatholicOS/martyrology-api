import pytest

from martyrology_api.auth import Identity


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe") if token == "good" else None


class GrantAll:
    async def check(self, user, relation, edition_id):
        return True


@pytest.fixture
def client(make_client):
    c = make_client()
    c.app.state.authenticator = StaticAuth()
    c.app.state.authz = GrantAll()
    return c


def test_edition_path_is_immutable(client):
    r = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "etag" in r.headers
    assert "Authorization" in r.headers["vary"]


def test_resolver_path_is_daily(client):
    r = client.get("/api/v1/elogia/nation/IT/1970/01/01")
    assert r.headers["cache-control"] == "public, max-age=86400"


def test_authorized_restricted_is_private(client):
    r = client.get("/api/v1/elogia/01/01", headers={"Authorization": "Bearer good"})
    assert r.headers["cache-control"] == "private, max-age=0"


def test_authorized_restricted_elogium_is_private(client):
    r = client.get("/api/v1/elogium/mr:0102-concordius", headers={"Authorization": "Bearer good"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, max-age=0"


def test_304_on_if_none_match(client):
    r1 = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01")
    etag = r1.headers["etag"]
    r2 = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/01/01", headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304
    assert r2.headers["etag"] == etag


def test_errors_not_cached(client):
    r = client.get("/api/v1/elogia/03/05")
    assert r.status_code == 404
    assert "cache-control" not in r.headers or "max-age=8" not in r.headers.get("cache-control", "")
