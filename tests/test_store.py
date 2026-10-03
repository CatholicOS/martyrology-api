from martyrology_api.registry import Registry
from martyrology_api.store import Store, detect_shape, parse_month_file


def make_store(crmedr_path, clbdr_path, data_paths) -> Store:
    return Store(data_paths, Registry.load(crmedr_path, clbdr_path))


def test_available_and_shape(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.available() == {
        "martyrologium_romanum_1749",
        "martyrologium_romanum_1914_en_unofficial",
        "martyrologium_romanum_2004",
        "martyrologium_romanum_2004_it_IT",
    }
    assert s.shape("martyrologium_romanum_1749") == "day-structured"
    assert s.shape("martyrologium_romanum_2004") == "flat"


def test_day_structured_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_1749", 1, 1)
    assert d is not None
    assert d.titulus is not None and d.titulus.startswith("1 Januarii")
    ids = [e.id for e in d.elogia]
    assert ids == ["mr:0101-circumcisio-domini", "mr:0102-concordius"]
    conc = d.elogia[1]
    # The 1749 print numbers no eulogies: no entry, whatever the position.
    assert (conc.entry, conc.anchor_month, conc.anchor_day) == (None, 1, 2)  # anchored 01-02
    assert d.conclusio is not None and d.conclusio.startswith("Et alibi")


def test_flat_day_uses_registry_placement(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_2004", 1, 1)
    assert d is not None
    assert d.titulus is None and d.conclusio is None
    ids = [e.id for e in d.elogia]
    assert ids == ["mr:0101-maria-dei-genetrix", "mr:0101-basilius"]
    assert d.elogia[0].unnumbered is True
    assert d.elogia[1].asterisk is True


def test_flat_day_omits_ids_without_text(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_2004_it_IT", 1, 1)
    assert d is not None
    assert [e.id for e in d.elogia] == ["mr:0101-maria-dei-genetrix"]  # basilius has no it text


def test_leap_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d_2004 = s.day("martyrologium_romanum_2004", 2, 29)
    d_1749 = s.day("martyrologium_romanum_1749", 2, 29)
    assert d_2004 is not None and d_1749 is not None
    assert [e.id for e in d_2004.elogia] == ["mr:0229-oswaldus"]
    assert [e.id for e in d_1749.elogia] == ["mr:0229-oswaldus"]


def test_missing_day_and_edition(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.day("martyrologium_romanum_1749", 3, 1) is None
    assert s.day("martyrologium_romanum_1584", 1, 1) is None
    assert s.month("martyrologium_romanum_1584", 1) == {}


def test_find_by_slug_on_printed_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    hit = s.find_by_slug("martyrologium_romanum_1749", 1, 1, "concordius")
    assert hit is not None and hit.id == "mr:0102-concordius"
    assert s.find_by_slug("martyrologium_romanum_1749", 1, 2, "concordius") is None
    hit_2004 = s.find_by_slug("martyrologium_romanum_2004", 1, 2, "concordius")
    assert hit_2004 is not None and hit_2004.id == "mr:0102-concordius"


def test_placements_cross_edition(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    p = {pl.edition_id: pl for pl in s.placements("mr:0102-concordius")}
    assert p["martyrologium_romanum_1749"].day_printed == "01-01"
    assert p["martyrologium_romanum_2004"].day_printed == "01-02"
    assert p["martyrologium_romanum_2004_it_IT"].day_printed == "01-02"
    text_2004 = p["martyrologium_romanum_2004"].text
    assert text_2004 is not None and text_2004.startswith("Spoleti")


def test_unaligned_day_returns_null_ids(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_1914_en_unofficial", 1, 2)
    assert d is not None
    assert len(d.elogia) == 2
    assert [e.id for e in d.elogia] == [None, None]
    assert [e.entry for e in d.elogia] == [None, None]  # the 1914 print numbers nothing
    assert d.elogia[0].text == "At Spoleto, St. Concordius, priest and martyr."
    assert d.elogia[1].text == "At Rome, many holy martyrs."
    assert (d.elogia[0].anchor_month, d.elogia[0].anchor_day) == (1, 2)


def test_unaligned_edition_find_by_slug_returns_none(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.find_by_slug("martyrologium_romanum_1914_en_unofficial", 1, 2, "concordius") is None


def test_placements_unaffected_by_unaligned_edition(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    p = {pl.edition_id: pl for pl in s.placements("mr:0102-concordius")}
    assert "martyrologium_romanum_1914_en_unofficial" not in p
    assert p["martyrologium_romanum_1749"].day_printed == "01-01"


def test_aligned(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.aligned("martyrologium_romanum_1749") is True
    assert s.aligned("martyrologium_romanum_1914_en_unofficial") is False
    assert s.aligned("martyrologium_romanum_1584") is None


def test_flat_parse_with_null_entry(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    raw = {"mr:0304-nullus-entry": "Textus sine numero.", "mr:0304-primus": "Textus primus."}
    days = parse_month_file(raw, 3, "flat", reg)
    assert [e.id for e in days[4].elogia] == ["mr:0304-primus", "mr:0304-nullus-entry"]
    assert days[4].elogia[1].entry is None


def test_detect_shape():
    assert detect_shape({"mr:0101-basilius": "t"}) == "flat"
    assert detect_shape({"1": {"elogia": {}}}) == "day-structured"
    assert detect_shape({}) == "day-structured"


def test_source_reads_source_json(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    src = s.source("martyrologium_romanum_1749")
    assert src is not None and src["year"] == 2013
    assert s.source("martyrologium_romanum_1914_en_unofficial") is None
    assert s.source("martyrologium_romanum_1584") is None


def test_each_2004_edition_prints_its_own_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    la = s.day("martyrologium_romanum_2004", 1, 3)
    it = s.day("martyrologium_romanum_2004_it_IT", 1, 3)
    assert la is not None and it is not None
    assert [(e.id, e.entry, e.asterisk) for e in la.elogia] == [
        ("mr:0103-hermes", 1, False),
        ("mr:0103-abrunculus", 2, True),
        ("mr:0103-gregorius", 3, False),
        ("mr:0103-sine-numero", 4, False),
    ]
    # Abrunculus is absent from the CEI even though a stray text exists.
    assert [(e.id, e.entry, e.asterisk) for e in it.elogia] == [
        ("mr:0103-hermes", 1, False),
        ("mr:0103-gregorius", 2, True),
        ("mr:0103-sine-numero", None, False),
    ]


def test_one_eulogy_on_different_days(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    la5 = s.day("martyrologium_romanum_2004", 1, 5)
    it6 = s.day("martyrologium_romanum_2004_it_IT", 1, 6)
    assert la5 is not None and it6 is not None
    assert [e.id for e in la5.elogia] == ["mr:0105-durando"]
    assert s.day("martyrologium_romanum_2004", 1, 6) is None  # the stray text is not printed
    assert s.day("martyrologium_romanum_2004_it_IT", 1, 5) is None
    assert [e.id for e in it6.elogia] == ["mr:0106-durando"]


def test_parse_month_file_without_edition_uses_main_placement(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    days = parse_month_file({"mr:0103-gregorius": "x"}, 1, "flat", reg)
    assert [(e.id, e.entry) for e in days[3].elogia] == [("mr:0103-gregorius", 3)]


def test_footnotes_loaded_by_id(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.footnotes("martyrologium_romanum_2004") == {
        "mr:0102-argeus-et-socii": [
            {
                "mark": "1",
                "after": "sociorum",
                "text": "Quorum nomina: sancti Narcissus et Marcellinus.",
            }
        ]
    }


def test_footnotes_empty_without_a_file_or_texts(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.footnotes("martyrologium_romanum_1749") == {}
    assert s.footnotes("no_such_edition") == {}
