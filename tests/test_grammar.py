import pytest

from martyrology_api.grammar import ElogiaRequest, parse_elogia_path
from martyrology_api.problems import ApiProblem


def test_month_only():
    assert parse_elogia_path("01") == ElogiaRequest(month=1)


def test_month_day_slug():
    r = parse_elogia_path("01/02/argeus-et-socii")
    assert (r.month, r.day, r.slug) == (1, 2, "argeus-et-socii")


def test_universal_year():
    r = parse_elogia_path("1970/01/02")
    assert (r.year, r.month, r.day) == (1970, 1, 2)


def test_nation_forms():
    assert parse_elogia_path("nation/IT/01") == ElogiaRequest(nation="IT", month=1)
    r = parse_elogia_path("nation/IT/1970/01/02")
    assert (r.nation, r.year, r.month, r.day) == ("IT", 1970, 1, 2)


def test_edition_form():
    r = parse_elogia_path("edition/martyrologium_romanum_1749/01/01/concordius")
    assert (r.edition, r.month, r.day, r.slug) == ("martyrologium_romanum_1749", 1, 1, "concordius")


def test_leap_day_ok():
    assert parse_elogia_path("02/29").day == 29


@pytest.mark.parametrize("bad", [
    "", "13", "1", "001", "01/32", "02/30", "04/31", "01/02/UPPER",
    "nation/it/01", "nation/ITA/01", "edition/x/1970/01",  # year after edition forbidden
    "1970", "nation/IT", "edition/martyrologium_romanum_1749",  # month required
    "01/02/argeus-et-socii/extra", "0170/01",
])
def test_bad_paths(bad):
    with pytest.raises(ApiProblem) as ei:
        parse_elogia_path(bad)
    assert ei.value.status == 400
