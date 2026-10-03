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


def test_anonymous_restricted_is_redacted_200(client):
    r = client.get("/api/v1/elogia/01/01")
    assert r.status_code == 200
    b = r.json()
    assert b["metadata"]["access"] == "restricted-texts"
    assert b["metadata"]["access_info"]
    assert all(e["text"] is None for e in b["elogia"])
    assert [e["id"] for e in b["elogia"]] == [
        "mr:0101-maria-dei-genetrix",
        "mr:0101-basilius",
    ]  # skeleton stays public


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
    b = client.get(
        "/api/v1/elogia/nation/IT/01/01", headers={"Authorization": "Bearer good"}
    ).json()
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
    assert all(e["text"] is None for day in b["days"].values() for e in day["elogia"])


FOOTNOTE = {
    "mark": "1",
    "after": "sociorum",
    "text": "Quorum nomina: sancti Narcissus et Marcellinus.",
}


def _argeus(body):
    return next(e for e in body["elogia"] if e["id"] == "mr:0102-argeus-et-socii")


def test_authorized_day_carries_footnotes(client):
    b = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_2004/01/02",
        headers={"Authorization": "Bearer good"},
    ).json()
    assert _argeus(b)["footnotes"] == [FOOTNOTE]
    assert all(e["footnotes"] == [] for e in b["elogia"] if e["id"] != "mr:0102-argeus-et-socii")


def test_anonymous_day_and_month_footnotes_redacted(client):
    day = client.get("/api/v1/elogia/edition/martyrologium_romanum_2004/01/02").json()
    assert day["metadata"]["access"] == "restricted-texts"
    assert _argeus(day)["footnotes"] == []
    month = client.get("/api/v1/elogia/01").json()  # the universal route resolves to 2004
    assert month["metadata"]["edition"] == "martyrologium_romanum_2004"
    assert all(e["footnotes"] == [] for d in month["days"].values() for e in d["elogia"])


def test_authorized_month_carries_footnotes(client):
    month = client.get("/api/v1/elogia/01", headers={"Authorization": "Bearer good"}).json()
    assert _argeus(month["days"]["02"])["footnotes"] == [FOOTNOTE]


def test_elogium_placement_footnotes_follow_access(client):
    url = "/api/v1/elogium/mr:0102-argeus-et-socii"
    authorized = client.get(url, headers={"Authorization": "Bearer good"}).json()
    assert authorized["editions"]["martyrologium_romanum_2004"]["footnotes"] == [FOOTNOTE]
    anonymous = client.get(url).json()
    assert anonymous["editions"]["martyrologium_romanum_2004"]["footnotes"] == []


def test_public_edition_without_footnotes_has_empty_lists(client):
    b = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    assert all(e["footnotes"] == [] for e in b["elogia"])
