import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError


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


def test_editions_source(client):
    r = client.get("/api/v1/editions")
    eds = {e["edition_id"]: e for e in r.json()["editions"]}
    src = eds["martyrologium_romanum_1749"]["source"]
    assert src["imprint"] == "Eystadii (Eichstätt), 2013"
    assert src["year"] == 2013
    assert src["isbn"] is None
    # No source.json beside the texts, or no texts at all: no source.
    assert eds["martyrologium_romanum_1914_en_unofficial"]["source"] is None
    assert eds["martyrologium_romanum_1584"]["source"] is None


def test_editions_empty_source_is_invalid(make_client, data_paths, tmp_path):
    # An empty source.json is a broken record, not a missing one: it must fail
    # validation (title is required) rather than read as "no source".
    src = data_paths[0] / "martyrologium_romanum_1749"
    ed = tmp_path / "martyrologium_romanum_1749"
    ed.mkdir()
    for f in src.glob("[01]*.json"):
        (ed / f.name).write_bytes(f.read_bytes())
    (ed / "source.json").write_text("{}", encoding="utf-8")
    client = make_client(data_path=str(tmp_path))
    with pytest.raises(ValidationError, match="title"):
        client.get("/api/v1/editions")


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


def test_catalog_built_once_per_edition_and_language(client, monkeypatch):
    # Served from the serialized cache after the first request: the same bytes, and the same
    # ETag, without building the 6000-entry catalog again.
    from martyrology_api.routers import discovery

    builds = []
    build = discovery._catalog
    monkeypatch.setattr(discovery, "_catalog", lambda *a: builds.append(a[2:]) or build(*a))
    url = "/api/v1/elogia?edition=martyrologium_romanum_1749&locale=en"
    first, second = client.get(url), client.get(url)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["content-type"] == "application/json"
    assert len(builds) == 1


def test_catalog_cache_keyed_by_language_not_locale_string(client, monkeypatch):
    # Locales naming the same language share an entry, and so do the languages the registry has
    # no subjects in, so arbitrary ?locale= values cannot grow the cache.
    from martyrology_api.routers import discovery

    builds = []
    build = discovery._catalog
    monkeypatch.setattr(discovery, "_catalog", lambda *a: builds.append(a[2:]) or build(*a))
    en = [client.get(f"/api/v1/elogia?locale={loc}") for loc in ("en", "en-US", "EN-gb")]
    assert len({r.content for r in en}) == 1
    unknown = [client.get(f"/api/v1/elogia?locale={loc}") for loc in ("xx", "zz-ZZ", "qq")]
    assert len({r.content for r in unknown}) == 1
    items = {i["id"]: i for i in unknown[0].json()["elogia"]}
    assert items["mr:0102-concordius"]["subject"] is None
    assert len(builds) == 2
    assert client.app.state.catalog_cache.keys() == {(None, "en"), (None, None)}


def test_catalog_unknown_edition_not_cached(client):
    assert client.get("/api/v1/elogia?edition=nope").status_code == 404
    assert client.app.state.catalog_cache.keys() == set()


def test_catalog_built_once_under_a_cold_burst(client, monkeypatch):
    # Concurrent requests for a catalog not yet built: one builds it, the others wait for it.
    from martyrology_api.routers import discovery

    builds = []
    build = discovery._catalog

    def slow_build(*a):
        builds.append(a[2:])
        time.sleep(0.2)  # long enough for every request to reach the cache before it is filled
        return build(*a)

    monkeypatch.setattr(discovery, "_catalog", slow_build)
    url = "/api/v1/elogia?edition=martyrologium_romanum_1749&locale=la"
    with ThreadPoolExecutor(12) as ex:
        responses = list(ex.map(lambda _: client.get(url), range(12)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.content for r in responses}) == 1
    assert len(builds) == 1


def test_catalog_cache_builds_other_keys_in_parallel():
    # A build in progress for one key does not hold up a miss on another.
    from martyrology_api.routers.discovery import CatalogCache

    cache = CatalogCache()
    both_building = threading.Barrier(2, timeout=5)

    def build(body: bytes):
        def run() -> bytes:
            both_building.wait()  # raises BrokenBarrierError if the builds were serialized
            return body

        return run

    with ThreadPoolExecutor(2) as ex:
        a = ex.submit(cache.get, ("a", None), build(b"A"))
        b = ex.submit(cache.get, ("b", None), build(b"B"))
        assert (a.result(), b.result()) == (b"A", b"B")
    assert cache.get(("a", None), lambda: b"rebuilt") == b"A"
