import pytest


@pytest.fixture
def client(make_client):
    return make_client()


def test_day_universal_default(client):
    r = client.get("/api/v1/elogia/01/01")
    assert r.status_code == 200
    b = r.json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004"
    assert b["metadata"]["resolved_from"] == {}
    assert [e["id"] for e in b["elogia"]] == ["mr:0101-maria-dei-genetrix", "mr:0101-basilius"]
    assert b["elogia"][0]["text"] is None
    assert b["metadata"]["access"] == "restricted-texts"
    assert b["titulus"] is None


def test_month_universal(client):
    r = client.get("/api/v1/elogia/01")
    assert r.status_code == 200
    b = r.json()
    assert set(b["days"]) == {"01", "02"}
    assert b["metadata"]["day"] is None


def test_nation_resolution(client):
    b = client.get("/api/v1/elogia/nation/IT/01/01").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004_it_IT"
    assert b["metadata"]["resolved_from"] == {"nation": "IT"}


def test_year_resolution(client):
    b = client.get("/api/v1/elogia/nation/IT/1970/01/01").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_1749"
    assert b["metadata"]["resolved_from"] == {"nation": "IT", "year": 1970}
    assert b["titulus"].startswith("1 Januarii")


def test_explicit_edition_path(client):
    b = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_1749"
    assert b["metadata"]["resolved_from"] is None


def test_edition_metadata_matches_discovery_vocabulary(client):
    b = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    em = b["metadata"]["edition_metadata"]
    assert em["book"] == "martyrologium_romanum"
    assert em["year"] == 1749
    assert em["nature"] == "editio_typica_recognita"
    assert em["scope"] == {"type": "universal"}
    assert em["locale"] == "la"
    assert em["promulgation"] == {"decree": None, "date": "1749"}
    assert em["predecessor"] == "martyrologium_romanum_1584"
    assert em["successor"] == "martyrologium_romanum_2004"
    assert em["translation_of"] is None

    d = client.get("/api/v1/editions").json()
    disc = next(e for e in d["editions"] if e["edition_id"] == "martyrologium_romanum_1749")
    assert em["scope"] == disc["scope"]
    assert em["promulgation"] == disc["promulgation"]
    assert em["year"] == disc["year"]
    assert em["locale"] == disc["locale"]


def test_edition_query_overrides(client):
    b = client.get("/api/v1/elogia/01/01?edition=martyrologium_romanum_1749").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_1749"


def test_cross_day_slug_on_printed_day(client):
    r = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01/concordius")
    assert r.status_code == 200
    b = r.json()
    assert b["elogia"][0]["id"] == "mr:0102-concordius"
    assert b["elogia"][0]["anchor_day"] == "01-02"
    r2 = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/02/concordius")
    assert r2.status_code == 404
    assert r2.json()["type"].endswith("unknown-eulogy")


def test_accept_language_influences_resolution(client):
    b = client.get("/api/v1/elogia/nation/IT/01/01", headers={"Accept-Language": "la"}).json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004"


def test_elogium_cross_edition(client):
    r = client.get("/api/v1/elogium/mr:0102-concordius")
    assert r.status_code == 200
    b = r.json()
    assert b["subject"]["la"] == "Sanctus Concordius"
    assert b["editions"]["martyrologium_romanum_1749"]["day_printed"] == "01-01"
    assert b["editions"]["martyrologium_romanum_2004"]["day_printed"] == "01-02"


def test_elogium_editions_filter(client):
    b = client.get("/api/v1/elogium/mr:0102-concordius?editions=martyrologium_romanum_1749").json()
    assert list(b["editions"]) == ["martyrologium_romanum_1749"]


def test_errors(client):
    assert client.get("/api/v1/elogia/13/01").status_code == 400
    assert client.get("/api/v1/elogia/edition/nope_1000/01/01").status_code == 404
    assert client.get("/api/v1/elogia/03/05").status_code == 404  # no data
    assert client.get("/api/v1/elogia/1500/01/01").status_code == 404  # pre-1584
    assert client.get("/api/v1/elogium/mr:9999-nobody").status_code == 404
    r = client.get("/api/v1/elogia/nation/IT/1600/01/01")  # -> 1584, textless
    assert r.status_code == 404 and r.json()["edition"] == "martyrologium_romanum_1584"
