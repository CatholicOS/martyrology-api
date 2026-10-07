import json
import shutil

import pytest

from martyrology_api import lunar as L

EDITION = "martyrologium_romanum_1749"


@pytest.fixture
def lunar_client(tmp_path, make_client, data_paths):
    """The fixture 1749 edition, declared to print the Gregorian lunar table, with a misprint."""
    public = tmp_path / "public"
    shutil.copytree(data_paths[0], public)
    ed = public / EDITION
    src = json.loads((ed / "source.json").read_text(encoding="utf-8"))
    (ed / "source.json").write_text(json.dumps(src | {"lunar_table": "gregorian"}), "utf-8")
    (ed / "lunar_misprints.json").write_text(json.dumps({"01-02": {"xxv": 26}}), "utf-8")
    return make_client(data_path=f"{public}:{data_paths[1]}")


def test_a_day_carries_its_table_margin_and_announcement(lunar_client):
    b = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01/02?year=2026").json()
    luna = b["luna"]
    assert luna["dominical_letter"] == "B" and luna["epactae"] == ["xxix"]
    assert [c["letter"] for c in luna["tabula"]] == L.LETTERS
    assert [c["age"] for c in luna["tabula"]] == L.table()[1]
    f, F = luna["tabula"][24], luna["tabula"][25]
    assert (f["epact"], F["epact"]) == ("25", "xxv")
    assert F["printed"] == 26 and f["printed"] is None  # the misprint, beside the age
    assert luna["annuntiatio"] == {
        "year": 2026,
        "golden_number": 13,
        "epact": "xj",
        "letter": "l",
        "column": 10,
        "age": 13,
        "pronuntiatio": "Luna decima tertia",
    }


def test_the_year_comes_from_the_path_else_the_query_else_today(lunar_client):
    b = lunar_client.get("/api/v1/elogia/nation/IT/1970/01/02").json()
    assert b["metadata"]["edition"] == EDITION
    assert b["luna"]["annuntiatio"]["year"] == 1970
    b = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01/02").json()
    assert b["luna"]["annuntiatio"]["year"] >= 2026
    r = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01/02?year=0")
    assert r.status_code == 400


def test_no_announcement_before_the_reform(lunar_client):
    b = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01/02?year=1500").json()
    assert b["luna"]["annuntiatio"] is None
    assert len(b["luna"]["tabula"]) == 31


def test_month_and_single_eulogy_responses_carry_it(lunar_client):
    b = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01?year=2026").json()
    assert all(d["luna"]["annuntiatio"]["year"] == 2026 for d in b["days"].values())
    day = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01/02").json()
    slug = day["elogia"][0]["id"].split("-", 1)[1]
    one = lunar_client.get(f"/api/v1/elogia/edition/{EDITION}/01/02/{slug}").json()
    assert one["luna"]["tabula"] == day["luna"]["tabula"]


def test_an_edition_without_the_table_has_no_luna(make_client):
    b = make_client().get(f"/api/v1/elogia/edition/{EDITION}/01/02").json()
    assert b["luna"] is None


def test_the_edition_source_declares_the_table(lunar_client):
    eds = lunar_client.get("/api/v1/editions").json()
    rows = eds["editions"] if isinstance(eds, dict) else eds
    src = next(e["source"] for e in rows if e["edition_id"] == EDITION)
    assert src["lunar_table"] == "gregorian"


@pytest.mark.parametrize(
    "bad",
    [{"1-2": {"xxv": 26}}, {"01-02": {"xxx": 26}}, {"01-02": {"xxv": 0}}, ["x"]],
)
def test_a_malformed_misprints_file_fails_at_startup(tmp_path, make_client, data_paths, bad):
    public = tmp_path / "public"
    shutil.copytree(data_paths[0], public)
    ed = public / EDITION
    src = json.loads((ed / "source.json").read_text(encoding="utf-8"))
    (ed / "source.json").write_text(json.dumps(src | {"lunar_table": "gregorian"}), "utf-8")
    (ed / "lunar_misprints.json").write_text(json.dumps(bad), "utf-8")
    with pytest.raises(ValueError, match="lunar_misprints.json"):
        make_client(data_path=str(public))


@pytest.fixture
def client_2004(tmp_path, make_client, data_paths):
    """The fixture 2004 editions, declared to print the 2004 lunar tables."""
    private = tmp_path / "private"
    shutil.copytree(data_paths[1], private)
    for ed in ("martyrologium_romanum_2004", "martyrologium_romanum_2004_it_IT"):
        (private / ed / "source.json").write_text(
            json.dumps({"title": ed, "lunar_table": "gregorian-2004"}), "utf-8"
        )
    return make_client(data_path=f"{data_paths[0]}:{private}")


def test_a_2004_day_has_its_layout_and_no_margin(client_2004):
    b = client_2004.get("/api/v1/elogia/edition/martyrologium_romanum_2004/01/02?year=2026").json()
    luna = b["luna"]
    assert luna["rows"] == [19, 12]
    assert luna["dominical_letter"] is None and luna["epactae"] is None
    assert [c["letter"] for c in luna["tabula"][24:26]] == ["F", "F"]
    assert [c["red"] for c in luna["tabula"][24:26]] == [True, False]
    assert luna["annuntiatio"]["pronuntiatio"].startswith("Luna ")


def test_a_2004_edition_reads_29_february(client_2004):
    leap = client_2004.get("/api/v1/elogia/edition/martyrologium_romanum_2004/02/29?year=2028")
    assert leap.status_code == 200
    eve = client_2004.get("/api/v1/elogia/edition/martyrologium_romanum_2004/02/28?year=2028")
    assert leap.json()["luna"]["tabula"] == eve.json()["luna"]["tabula"]


def test_the_italian_edition_announces_in_italian(client_2004):
    b = client_2004.get(
        "/api/v1/elogia/edition/martyrologium_romanum_2004_it_IT/01/02?year=2026"
    ).json()
    age = b["luna"]["annuntiatio"]["age"]
    assert b["luna"]["annuntiatio"]["pronuntiatio"] == f"Luna: {age}"


def test_an_unknown_variant_fails_at_startup(tmp_path, make_client, data_paths):
    public = tmp_path / "public"
    shutil.copytree(data_paths[0], public)
    ed = public / EDITION
    src = json.loads((ed / "source.json").read_text(encoding="utf-8"))
    (ed / "source.json").write_text(json.dumps(src | {"lunar_table": "julian"}), "utf-8")
    with pytest.raises(ValueError, match="unknown lunar_table"):
        make_client(data_path=str(public))
