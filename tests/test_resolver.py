import pytest

from martyrology_api.registry import Registry
from martyrology_api.resolver import (EditionUnavailableError,
                                      PreFirstEditionError, resolve)


@pytest.fixture
def reg(crmedr_path, clbdr_path):
    return Registry.load(crmedr_path, clbdr_path)


AVAILABLE = {"martyrologium_romanum_1749", "martyrologium_romanum_2004",
             "martyrologium_romanum_2004_it_IT"}


def test_universal_default_is_2004(reg):
    r = resolve(reg, AVAILABLE)
    assert r.edition_id == "martyrologium_romanum_2004"
    assert r.resolved_from == {}


def test_nation_resolves_vernacular(reg):
    r = resolve(reg, AVAILABLE, nation="IT")
    assert r.edition_id == "martyrologium_romanum_2004_it_IT"
    assert r.resolved_from == {"nation": "IT"}


def test_nation_with_latin_locale_overrides(reg):
    r = resolve(reg, AVAILABLE, nation="IT", locale="la")
    assert r.edition_id == "martyrologium_romanum_2004"


def test_year_resolver(reg):
    assert resolve(reg, AVAILABLE, year=1970).edition_id == "martyrologium_romanum_1749"
    assert resolve(reg, AVAILABLE, year=2004).edition_id == "martyrologium_romanum_2004"


def test_year_resolving_to_textless_edition_is_404(reg):
    with pytest.raises(EditionUnavailableError) as ei:
        resolve(reg, AVAILABLE, year=1600)  # -> 1584, registered but no texts
    assert ei.value.extensions["edition"] == "martyrologium_romanum_1584"


def test_pre_first_edition(reg):
    with pytest.raises(PreFirstEditionError):
        resolve(reg, AVAILABLE, year=1500)


def test_unknown_nation_falls_back_universal(reg):
    r = resolve(reg, AVAILABLE, nation="FR")
    assert r.edition_id == "martyrologium_romanum_2004"
    assert r.resolved_from == {"nation": "FR"}


def test_locale_en_prefers_translation_when_available(reg):
    avail = AVAILABLE | {"martyrologium_romanum_2004_en_unofficial"}
    r = resolve(reg, avail, locale="en")
    assert r.edition_id == "martyrologium_romanum_2004_en_unofficial"


def test_tie_break_prefers_non_translation(reg):
    # locale la, year 2004: 2004 (la) wins over en translation (filtered by locale anyway)
    r = resolve(reg, AVAILABLE, year=2004, locale="la")
    assert r.edition_id == "martyrologium_romanum_2004"


def test_nation_with_year_falls_back_to_universal(reg):
    r = resolve(reg, AVAILABLE, nation="IT", year=1970)
    assert r.edition_id == "martyrologium_romanum_1749"
    assert r.resolved_from == {"nation": "IT", "year": 1970}


def test_translations_never_win_without_locale(reg):
    avail = AVAILABLE | {"martyrologium_romanum_2004_en_unofficial"}
    r = resolve(reg, avail)
    assert r.edition_id == "martyrologium_romanum_2004"
    r2 = resolve(reg, avail, year=2010)
    assert r2.edition_id == "martyrologium_romanum_2004"


def test_locale_fallback_never_leaks_foreign_scope(reg):
    from martyrology_api.registry import EditionMeta
    reg.editions["martyrologium_romanum_2005_la_DE"] = EditionMeta(
        id="martyrologium_romanum_2005_la_DE", nature="editio_vernacula",
        language="la", scope="DE", promulgated="2005", promulgated_year=2005)
    r = resolve(reg, AVAILABLE, nation="IT", locale="la")
    assert r.edition_id == "martyrologium_romanum_2004"
