from martyrology_api.registry import Registry, anchor_day, is_canonical_id, slug_of


def test_load_entries_and_deprecated(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    assert reg.entries["mr:0101-basilius"].asterisk is True
    dep = reg.entries["mr:0101-circumcisio-domini"]
    assert dep.deprecated is True
    assert dep.attested_in == "martyrologium_romanum_1749"


def test_editions_filtered_to_martyrology(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    assert "missale_romanum_1570" not in reg.editions
    e = reg.editions["martyrologium_romanum_2004_it_IT"]
    assert (e.nature, e.scope, e.language, e.promulgated_year) == ("editio_vernacula", "IT", "it-IT", 2004)
    assert reg.editions["martyrologium_romanum_1584"].promulgated_year == 1584


def test_subjects_locale_fallback(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    assert reg.subjects("la")["mr:0101-circumcisio-domini"] == "Circumcisio Domini"
    assert reg.subjects("en")["mr:0102-concordius"] == "Saint Concordius"
    assert reg.subjects("de") == {}


def test_ids_for_day_ordering(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    ids = [e.id for e in reg.ids_for_day(1, 1)]
    assert ids == ["mr:0101-maria-dei-genetrix", "mr:0101-basilius"]  # unnumbered first
    assert all(not e.deprecated for e in reg.ids_for_day(1, 1))


def test_id_helpers():
    assert anchor_day("mr:0102-concordius") == (1, 2)
    assert slug_of("mr:0102-argeus-et-socii") == "argeus-et-socii"
    assert is_canonical_id("mr:0102-argeus-et-socii")
    assert not is_canonical_id("mr:102-x")
    assert not is_canonical_id("foo:0102-x")
