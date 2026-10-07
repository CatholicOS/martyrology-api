import datetime

import pytest

from martyrology_api import lunar as L


def _letters(row: str) -> list[str]:
    return row.split()


# The book's tables of the letters by golden number 1-19 (1630, pp. 30-31), for the centuries
# they serve. In the 1900-2199 table golden number 16 is printed "P" for p, and 17 "F" for f
# (the Arabic 25 with a golden number above 11, by the book's own rule, p. 33).
CENTURY_TABLES = {
    (1583, 1699): "a m D d q G g t N k B b n E e r H h u",
    (1700, 1899): "P l C c p F f s M i A a m D d q G g t",
    (1900, 2199): "N k B b n E e r H h u P l C c p f f s",
}


@pytest.mark.parametrize("years", list(CENTURY_TABLES))
def test_the_year_letter_follows_the_book_tables(years):
    want = _letters(CENTURY_TABLES[years])
    for y in range(years[0], years[1] + 1):
        assert L.COLUMNS[L.column(L.epact(y))][1] == want[L.golden_number(y) - 1], y


@pytest.mark.parametrize(
    ("year", "golden", "epact", "letter"),
    [
        (1630, 16, "xvj", "r"),
        (1631, 17, "xxvij", "H"),
        (1634, 1, "j", "a"),
        (1639, 6, "xxvj", "G"),
        (1680, 9, "xxix", "N"),
        (1700, 10, "ix", "i"),
        (1710, 1, "*", "P"),
        (1715, 6, "xxv", "F"),
        (1716, 7, "vj", "f"),
        (1911, 12, "*", "P"),
        (1916, 17, "25", "f"),
        (2200, 16, "xiij", "n"),
        (2026, 13, "xj", "l"),
    ],
)
def test_golden_number_epact_and_letter(year, golden, epact, letter):
    a = L.announce(year, 1, 1)
    assert a is not None
    assert (a.golden_number, a.epact, a.letter) == (golden, epact, letter)


@pytest.mark.parametrize(
    ("date", "age"),
    [
        # 1583, letter g: Luna octava on 1 January, prima on 24 January
        ((1583, 1, 1), 8),
        ((1583, 1, 24), 1),
        # 1587/1588: Luna secunda on 31 December under B, tertia on 1 January under b
        ((1587, 12, 31), 2),
        ((1588, 1, 1), 3),
        # 1595/1596: trigesima under u; in 1596 (golden number 1) prima on 1 January although
        # a prints secunda, one day less up to 29 January, then as printed
        ((1595, 12, 31), 30),
        ((1596, 1, 1), 1),
        ((1596, 1, 29), 29),
        ((1596, 1, 30), 1),
        # 1699/1700 (the lunar equation): decima on both days
        ((1699, 12, 31), 10),
        ((1700, 1, 1), 10),
        # 1899/1900: vigesima nona on both days (1900 has golden number 1)
        ((1899, 12, 31), 29),
        ((1900, 1, 1), 29),
        # 2399/2400 (saltus lunae back): tertia, then quinta
        ((2399, 12, 31), 3),
        ((2400, 1, 1), 5),
    ],
)
def test_the_books_worked_examples(date, age):
    a = L.announce(*date)
    assert a is not None and a.age == age


def test_printed_rows():
    # 1630, 2 and 3 January (p. 46, 49), and 29 July as the computus gives it (p. 405 misprints
    # F G H as 27 28 29)
    assert L.table()[1] == [*range(3, 21), 21, 22, 23, 24, 25, 26, 27, 27, 28, 29, 30, 1, 2]
    assert L.table()[2] == [*range(4, 22), 22, 23, 24, 25, 26, 27, 28, 28, 29, 30, 1, 2, 3]
    jul29 = L.table()[L.day_of_year(7, 29) - 1]
    assert jul29[L.column(25)] == 28 and jul29[L.column(26)] == 29 and jul29[L.column(27)] == 30
    assert L.LETTERS == _letters("a b c d e f g h i k l m n p q r s t u A B C D E f F G H M N P")


def test_margin_apparatus():
    assert (L.dominical_letter(1, 1), L.epacts_of_day(1, 1)) == ("A", ["*"])
    assert (L.dominical_letter(1, 2), L.epacts_of_day(1, 2)) == ("B", ["xxix"])
    assert (L.dominical_letter(7, 29), L.epacts_of_day(7, 29)) == ("G", ["xxviij"])
    # a hollow month: xxv with xxiiij, the Arabic 25 with xxvj (31 July, p. 409: "B xxv. 25")
    assert L.dominical_letter(7, 31) == "B"
    assert sorted(L.epacts_of_day(7, 31)) == ["25", "xxvj"]
    assert sorted(L.epacts_of_day(8, 1)) == ["xxiiij", "xxv"]
    # a full month: the Arabic 25 with xxv
    assert sorted(L.epacts_of_day(1, 6)) == ["25", "xxv"]


def test_every_lunation_is_29_or_30_days():
    for e, days in L.new_moons().items():
        gaps = {b - a for a, b in zip(days, days[1:], strict=False)}
        assert gaps <= {29, 30}, e


def _easter(y: int) -> datetime.date:
    """The Gregorian Easter (Meeus/Jones/Butcher), independent of the module."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return datetime.date(y, month, day + 1)


def test_easter_from_the_table():
    """The paschal full moon is the first Luna xiiij on or after 21 March, and Easter the
    Sunday after it: the table must give the Gregorian Easter in every year."""
    for y in range(1583, 4100):
        col = L.column(L.epact(y))
        start = L.day_of_year(3, 21)
        full = next(d for d in range(start, 366) if L.table()[d - 1][col] == 14)
        date = datetime.date(y, 1, 1) + datetime.timedelta(days=full - 1)
        if y % 4 == 0 and (y % 100 or y % 400 == 0):  # the table's days are a common year's
            date += datetime.timedelta(days=1)
        easter = date + datetime.timedelta(days=7 - (date.isoweekday() % 7) or 7)
        assert easter == _easter(y), y


def test_announcement_edges():
    assert L.announce(1582, 12, 1) is None  # before the first whole Gregorian year
    assert L.announce(2025, 2, 29) is None
    a = L.announce(2026, 8, 4)
    assert a is not None and a.pronuntiatio == f"Luna {L.ORDINALS[a.age - 1]}"
    # a leap year: 25 February is the bissextile day, read under 24 February
    leap24, leap25 = L.announce(2028, 2, 24), L.announce(2028, 2, 25)
    assert leap24 is not None and leap25 is not None and leap24.age == leap25.age
    printed25 = L.announce(2028, 2, 25, printed=True)
    assert (
        printed25 is not None
        and printed25.age == L.table()[L.day_of_year(2, 25) - 1][printed25.column]
    )
    assert L.ORDINALS[0] == "prima" and L.ORDINALS[20] == "vigesima prima"
    assert len(L.ORDINALS) == 30 and L.ORDINALS[29] == "trigesima"


# ---- the 2004 edition (De pronuntiatione lunæ, pp. 23-27; the Italian, pp. 33-37)
V2004 = L.GREGORIAN_2004


def _row(m, d, v=V2004):
    pm, pd = L.printed_day(m, d, v)
    return L.table(v)[L.day_of_year(pm, pd) - 1]


def test_2004_rows_as_printed():
    # 25 August (Latin and Italian alike): the two F read 26 and 25
    assert _row(8, 25) == [*range(2, 21), 21, 22, 23, 24, 25, 26, 25, 26, 27, 28, 29, 1]
    # 13 April: the red F (the Arabic 25) 10, the black F (xxv) 9
    assert _row(4, 13)[24:26] == [10, 9]
    # February: the new moons of vj-xxiiij a day later, rejoining the calendar on 24 February
    assert _row(2, 5)[L.column(24)] == 30 and L.table()[L.day_of_year(2, 5) - 1][L.column(24)] == 1
    assert [_row(2, d)[L.column(6)] for d in (22, 23, 24, 25)] == [29, 30, 2, 3]
    assert _row(2, 24) == L.table()[L.day_of_year(2, 24) - 1]
    # 29 February repeats 28 February's table
    assert _row(2, 29) == _row(2, 28)
    # every other month as the editions before it
    rest = [d for d in range(1, 366) if not L.day_of_year(2, 5) <= d <= L.day_of_year(2, 23)]
    assert all(L.table(V2004)[d - 1] == L.table()[d - 1] for d in rest)


def test_2004_letters_and_layout():
    assert V2004.letters[24:26] == ("F", "F") and V2004.red == frozenset({24})
    assert sum(V2004.rows) == 31 == sum(L.GREGORIAN.rows) and not V2004.margin


@pytest.mark.parametrize(
    ("date", "age", "letter"),
    [
        ((2005, 1, 1), 20, "u"),  # the book's example: golden number 11, epact xix
        ((2005, 8, 2), 26, "u"),
        ((2204, 1, 1), 28, "M"),  # golden number 1: one less until January's new moon
        ((2204, 1, 2), 29, "M"),
        ((2204, 1, 3), 1, "M"),
        ((2011, 4, 13), 10, "F"),  # epact 25 with golden number 17
    ],
)
def test_2004_worked_examples(date, age, letter):
    a = L.announce(*date, variant=V2004)
    assert a is not None and (a.age, a.letter) == (age, letter)


def test_2004_leap_day_and_language():
    # a 2004 edition has a 29 February table, read on that day
    a = L.announce(2024, 2, 29, printed=True, variant=V2004)
    b = L.announce(2024, 2, 28, printed=True, variant=V2004)
    assert a is not None and b is not None and a.age == b.age
    assert L.announce(2024, 2, 29, printed=True) is None  # the editions before it have none
    it = L.announce(2026, 8, 4, variant=V2004, language="it-IT")
    assert it is not None and it.pronuntiatio == f"Luna: {it.age}"
    assert L.pronounce(21) == "Luna vigesima prima" and L.pronounce(21, "it") == "Luna: 21"
