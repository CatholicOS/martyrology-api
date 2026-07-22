import pytest


@pytest.fixture
def client(make_client):
    return make_client()


def test_editions_discovery(client):
    r = client.get("/api/v1/editions")
    assert r.status_code == 200
    eds = {e["edition_id"]: e for e in r.json()["editions"]}
    assert "missale_romanum_1570" not in eds
    e2004 = eds["martyrologium_romanum_2004"]
    assert e2004["availability"]["status"] == "restricted-texts"
    assert e2004["governance"]["type"] == "dicastery"
    assert e2004["promulgation"]["decree"].startswith("Congregatio")
    it = eds["martyrologium_romanum_2004_it_IT"]
    assert it["governance"] == {
        "governing_body": "Conferenza Episcopale Italiana",
        "type": "bishops_conference",
        "nation": "IT",
    }
    assert it["scope"] == {"type": "nation", "nation": "IT"}
    assert eds["martyrologium_romanum_1584"]["availability"]["status"] == "unavailable"
    assert eds["martyrologium_romanum_1749"]["availability"]["status"] == "public"


def test_editions_aligned_flag(client):
    r = client.get("/api/v1/editions")
    eds = {e["edition_id"]: e for e in r.json()["editions"]}
    assert eds["martyrologium_romanum_1749"]["aligned"] is True
    assert eds["martyrologium_romanum_1914_en_unofficial"]["aligned"] is False
    assert eds["martyrologium_romanum_1584"]["aligned"] is None


def test_catalog_default(client):
    r = client.get("/api/v1/elogia")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["elogia"]}
    assert items["mr:0102-concordius"]["subject"] == "Sanctus Concordius"
    assert items["mr:0102-concordius"]["anchor_day"] == "01-02"
    assert items["mr:0101-circumcisio-domini"]["deprecated"] is True
    assert items["mr:0101-basilius"]["present"] is None


def test_catalog_locale(client):
    items = {i["id"]: i for i in client.get("/api/v1/elogia?locale=en").json()["elogia"]}
    assert items["mr:0102-concordius"]["subject"] == "Saint Concordius"
    assert items["mr:0101-basilius"]["subject"] is None  # no en subject in fixture


def test_catalog_with_edition(client):
    r = client.get("/api/v1/elogia?edition=martyrologium_romanum_1749")
    items = {i["id"]: i for i in r.json()["elogia"]}
    assert items["mr:0102-concordius"]["present"] is True
    assert items["mr:0102-concordius"]["day_printed"] == "01-01"
    assert items["mr:0101-basilius"]["present"] is False
    assert client.get("/api/v1/elogia?edition=nope").status_code == 404
