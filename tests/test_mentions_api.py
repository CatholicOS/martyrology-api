import json
import logging
import shutil

import pytest

from martyrology_api.auth import Identity

ED = "martyrologium_romanum_2004"
SPOLETI = {
    "kind": "place",
    "where": "text",
    "start": 0,
    "end": 17,
    "qid": "Q13363",
    "name": None,
    "form": "Spoleti in Umbria",
}
CONCORDII = {
    "kind": "person",
    "where": "text",
    "start": 26,
    "end": 35,
    "qid": None,
    "name": "Concordius",
    "form": "Concordii",
}
NARCISSUS = {
    "kind": "person",
    "where": {"footnote": 1},
    "start": 22,
    "end": 31,
    "qid": "Q1",
    "name": "Narcissus",
    "form": "Narcissus",
}


def _by_id(elogia):
    return {e["id"]: e for e in elogia}


@pytest.fixture
def client(make_client):
    return make_client(restricted_texts_access="public")


def test_a_day_carries_each_eulogys_mentions_with_their_printed_words(client):
    b = client.get(f"/api/v1/elogia/edition/{ED}/01/02").json()
    e = _by_id(b["elogia"])
    assert e["mr:0102-concordius"]["mentions"] == [SPOLETI, CONCORDII]
    assert e["mr:0102-argeus-et-socii"]["mentions"] == [NARCISSUS]


def test_a_month_carries_them_and_a_eulogy_without_any_has_an_empty_list(client):
    b = client.get(f"/api/v1/elogia/edition/{ED}/01").json()
    assert _by_id(b["days"]["02"]["elogia"])["mr:0102-concordius"]["mentions"] == [
        SPOLETI,
        CONCORDII,
    ]
    assert all(e["mentions"] == [] for e in b["days"]["01"]["elogia"])


def test_a_single_eulogy_carries_them(client):
    b = client.get(f"/api/v1/elogia/edition/{ED}/01/02/concordius").json()
    assert b["elogia"][0]["mentions"] == [SPOLETI, CONCORDII]


def test_the_placements_carry_each_editions_own_mentions(client):
    b = client.get("/api/v1/elogium/mr:0102-concordius").json()["editions"]
    assert b[ED]["mentions"] == [SPOLETI, CONCORDII]
    assert [m["form"] for m in b["martyrologium_romanum_2004_it_IT"]["mentions"]] == ["A Spoleto"]
    assert [m["form"] for m in b["martyrologium_romanum_1749"]["mentions"]] == ["Concordii"]


class _SignedIn:
    async def identity(self, token):
        return Identity(subject="u1", username="j") if token == "good" else None


def test_withheld_texts_carry_no_mentions(make_client):
    # `form` quotes the copyrighted text: under a sign-in rule an anonymous caller gets none.
    c = make_client(restricted_texts_access="authenticated")
    day = c.get(f"/api/v1/elogia/edition/{ED}/01/02").json()
    assert day["metadata"]["access"] == "restricted-texts"
    assert all(e["mentions"] == [] for e in day["elogia"])
    month = c.get(f"/api/v1/elogia/edition/{ED}/01").json()
    assert all(e["mentions"] == [] for d in month["days"].values() for e in d["elogia"])
    one = c.get(f"/api/v1/elogia/edition/{ED}/01/02/concordius").json()
    assert one["elogia"][0]["mentions"] == []
    placements = c.get("/api/v1/elogium/mr:0102-concordius").json()["editions"]
    assert placements[ED]["mentions"] == []
    assert placements["martyrologium_romanum_2004_it_IT"]["mentions"] == []
    # A public-domain edition is not withheld.
    assert [m["form"] for m in placements["martyrologium_romanum_1749"]["mentions"]] == [
        "Concordii"
    ]
    # Signed in, they come back.
    c.app.state.authenticator = _SignedIn()
    authed = c.get(f"/api/v1/elogia/edition/{ED}/01/02", headers={"Authorization": "Bearer good"})
    assert _by_id(authed.json()["elogia"])["mr:0102-concordius"]["mentions"] == [SPOLETI, CONCORDII]


def test_a_crmedr_without_mentions_serves_none(make_client, crmedr_path, tmp_path, caplog):
    older = tmp_path / "crmedr"
    shutil.copytree(crmedr_path, older)
    (older / "data/mentions.json").unlink()
    caplog.set_level(logging.WARNING, logger="martyrology_api.mentions")
    c = make_client(crmedr_path=older, restricted_texts_access="public")
    b = c.get(f"/api/v1/elogia/edition/{ED}/01/02").json()
    assert all(e["mentions"] == [] for e in b["elogia"])
    assert "mentions.json is missing" in caplog.text


def test_start_up_warns_when_the_offsets_come_from_other_texts_than_those_served(
    make_client, crmedr_path, tmp_path, caplog
):
    pinned = tmp_path / "crmedr"
    shutil.copytree(crmedr_path, pinned)
    f = pinned / "data/mentions.json"
    f.write_text(
        json.dumps(json.loads(f.read_text(encoding="utf-8")) | {"texts": {"commit": "aaaaaaa"}}),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "bundle_format": 1,
                "api_version": "0.0.0",
                "api_commit": "c",
                "data": {"texts": "bbbbbbbbbbbb", "crmedr": "c", "clbdr": "c"},
                "python_requires": ">=3.12",
                "files": {},
            }
        ),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="martyrology_api.mentions")
    make_client(crmedr_path=pinned, manifest_path=str(manifest))
    assert "offsets from martyrology-texts aaaaaaa" in caplog.text
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("aaaaaaa" in w and "bbbbbbbbbbbb" in w for w in warnings)
