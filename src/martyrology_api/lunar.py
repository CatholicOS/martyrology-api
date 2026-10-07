"""The moon's age as the Roman Martyrology announces it ("Luna vigesima prima"), by the
Gregorian computus that the printed lunar tables follow.

Editions from the Gregorian reform on print, under each day's heading, a table of the
30 epacts' letters (the *litteræ Martyrologii*, 31 columns: the epact xxv has two, `F`
and `f` for the Arabic 25) with the moon's age under each. The year's letter comes from
its golden number and epact; the age under it is announced. The rules are those of the
*Explicatio eorum quæ … ad pronunciationem Lunæ pertinent* in the 1630 edition (pp.
30–36), which every later Gregorian edition repeats:

- the year's epact by the Gregorian computus (Lilius, Clavius), whose solar and lunar
  equations are the book's tables of golden numbers, epacts and letters by century;
  epact xxv with a golden number above 11 is the Arabic 25 (letter `f`);
- the new moons (*Luna prima*) fall on the days of the calendar of epacts: `*` on
  1 January, then one epact a day backwards, in alternate months of 30 and 29 days;
  in the hollow months xxv shares its day with xxiv and the Arabic 25 with xxvj;
- a day's age under a letter counts from that epact's last new moon (the table's first
  days count back from January's new moon with a 30-day lunation);
- in a year with golden number 1, the moon is announced one day less than printed until
  January's new moon, except under `P` (*);
- the bissextile day takes the age of the day it doubles (24 February).

Editions vary in how they print and read the tables (`Variant`): the 2004 edition (De
pronuntiatione lunæ ad libitum peragenda, pp. 23-27; the Italian, pp. 33-37) counts
February's lunation as 30 days, the bissextile day standing before 24 February (a common
year skips it: the ages jump by two that day), prints a 29 February table repeating 28
February's, calls the Arabic 25 a black F and xxv a red F, sets the table in rows of 19 and
12, and prints no margin apparatus.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from functools import cache

# The 31 columns of the printed table, in printed order: the epact each stands for
# (0 is *, 25 is xxv, "25" the Arabic 25) and its letter.
COLUMNS: list[tuple[int | str, str]] = [
    *zip(range(1, 20), "abcdefghiklmnpqrstu", strict=True),
    (20, "A"),
    (21, "B"),
    (22, "C"),
    (23, "D"),
    (24, "E"),
    ("25", "f"),
    (25, "F"),
    (26, "G"),
    (27, "H"),
    (28, "M"),
    (29, "N"),
    (0, "P"),
]
LETTERS = [letter for _, letter in COLUMNS]

ROMAN = [
    "*",
    "j",
    "ij",
    "iij",
    "iiij",
    "v",
    "vj",
    "vij",
    "viij",
    "ix",
    "x",
    "xj",
    "xij",
    "xiij",
    "xiiij",
    "xv",
    "xvj",
    "xvij",
    "xviij",
    "xix",
    "xx",
    "xxj",
    "xxij",
    "xxiij",
    "xxiiij",
    "xxv",
    "xxvj",
    "xxvij",
    "xxviij",
    "xxix",
]

ORDINALS = [
    "prima",
    "secunda",
    "tertia",
    "quarta",
    "quinta",
    "sexta",
    "septima",
    "octava",
    "nona",
    "decima",
    "undecima",
    "duodecima",
    "decima tertia",
    "decima quarta",
    "decima quinta",
    "decima sexta",
    "decima septima",
    "decima octava",
    "decima nona",
    "vigesima",
    "vigesima prima",
    "vigesima secunda",
    "vigesima tertia",
    "vigesima quarta",
    "vigesima quinta",
    "vigesima sexta",
    "vigesima septima",
    "vigesima octava",
    "vigesima nona",
    "trigesima",
]

DOMINICAL = "ABCDEFG"
FIRST_GREGORIAN_YEAR = 1583  # the first whole Gregorian year (the reform: 15 Oct 1582)
_CUMULATIVE = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def golden_number(year: int) -> int:
    return year % 19 + 1


def epact(year: int) -> int | str:
    """The year's Gregorian epact: 0 for *, 1–29, or "25" for the Arabic 25."""
    g = golden_number(year)
    c = year // 100 + 1
    solar = 3 * c // 4 - 12
    lunar = (8 * c + 5) // 25 - 5
    e = (11 * g + 20 + lunar - solar) % 30
    return "25" if e == 25 and g > 11 else e


def epact_label(e: int | str) -> str:
    return e if isinstance(e, str) else ROMAN[e]


def column(e: int | str) -> int:
    """The printed column of an epact."""
    return next(i for i, (x, _) in enumerate(COLUMNS) if x == e)


def day_of_year(month: int, day: int) -> int:
    """1–365 in a common year (29 February is not a day of the table)."""
    return _CUMULATIVE[month - 1] + day


@dataclass(frozen=True)
class Variant:
    """How an edition prints and reads the tables. The computus is the same; editions differ
    in the calendar of February, the bissextile day, the letters, and the layout."""

    name: str
    # the 31 letters in printed order, and the columns printed in red
    letters: tuple[str, ...]
    red: frozenset[int]
    # the two printed rows' lengths
    rows: tuple[int, int]
    # February's new moons of vj-xxiiij fall a day later (5-23 February instead of 4-22),
    # their lunations rejoining the calendar on 24 February (the 2004 edition)
    february_late: bool
    # "24": the bissextile day is read under 24 February; "29": a 29 February table that
    # repeats 28 February's (the 2004 edition, which reads civil dates)
    leap: str
    # the dominical letter and the new-moon epacts are printed in the margin
    margin: bool


GREGORIAN = Variant(
    name="gregorian",
    letters=tuple(LETTERS),
    red=frozenset(),
    rows=(17, 14),
    february_late=False,
    leap="24",
    margin=True,
)
# The editio typica of 2001/2004 (De pronuntiatione lunæ, pp. 23-27): the Arabic 25 and xxv are both
# an F, told apart by colour; no margin apparatus; one table per civil date, 29 February included.
# The tables print the Arabic 25 (the first F) in red; the explanation's example calls the F read
# with a golden number from 12 to 19 (the Arabic 25) the black one, the other way round.
GREGORIAN_2004 = Variant(
    name="gregorian-2004",
    letters=tuple("F" if letter == "f" and k == 24 else letter for k, letter in enumerate(LETTERS)),
    red=frozenset({24}),
    rows=(19, 12),
    february_late=True,
    leap="29",
    margin=False,
)
VARIANTS = {v.name: v for v in (GREGORIAN, GREGORIAN_2004)}

_FEB_4, _FEB_24 = 35, 55  # days of the year


@cache
def new_moons() -> dict[int | str, list[int]]:
    """For each epact, the days of the (common) year of its new moons: the calendar of epacts."""
    out: dict[int | str, list[int]] = {e: [] for e, _ in COLUMNS}
    start, full = 1, True
    while start <= 365:
        for e, _ in COLUMNS:
            if isinstance(e, str):  # the Arabic 25
                offset = 4 if not full else 5  # with xxvj in hollow months, else with xxv
            elif e == 0:
                offset = 0
            elif full or e > 24:
                offset = 30 - e
            else:
                offset = 29 - e  # xxv and xxiv share a day in a hollow month
            if start + offset <= 365:
                out[e].append(start + offset)
        start += 30 if full else 29
        full = not full
    return out


@cache
def table(variant: Variant = GREGORIAN) -> list[list[int]]:
    """The printed table: for each day of the common year (index 0 = 1 January), the
    moon's age under each of the 31 columns."""
    moons = new_moons()
    rows: list[list[int]] = []
    for d in range(1, 366):
        row = []
        for e, _ in COLUMNS:
            past = [m for m in moons[e] if m <= d]
            if past:
                age = d - past[-1] + 1
                if (
                    variant.february_late
                    and isinstance(e, int)
                    and 6 <= e <= 24
                    and _FEB_4 < past[-1] <= d < _FEB_24
                ):
                    age = age - 1 or 30  # the new moon a day later
            else:  # before January's new moon: back from it with a 30-day lunation
                age = 30 - (moons[e][0] - d) + 1
            row.append(age)
        rows.append(row)
    return rows


def dominical_letter(month: int, day: int) -> str:
    """The letter printed in the margin for the day (A on 1 January)."""
    return DOMINICAL[(day_of_year(month, day) - 1) % 7]


def epacts_of_day(month: int, day: int) -> list[str]:
    """The epacts whose new moon falls on the day, as printed beside the dominical letter."""
    d = day_of_year(month, day)
    return [epact_label(e) for e, days in new_moons().items() if d in days]


@dataclass(frozen=True)
class Announcement:
    year: int
    golden_number: int
    epact: str
    letter: str
    column: int
    age: int
    pronuntiatio: str


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def table_day(year: int, month: int, day: int, variant: Variant = GREGORIAN) -> tuple[int, int]:
    """The printed day whose table a civil date reads: in a leap year, with the bissextile day
    doubling 24 February, 25-29 February read the day before; with a 29 February table (2004),
    each date reads its own, 29 February 28 February's."""
    if month == 2 and day >= 25 and _leap(year) and variant.leap == "24":
        return 2, day - 1
    return printed_day(month, day, variant)


def printed_day(month: int, day: int, variant: Variant = GREGORIAN) -> tuple[int, int]:
    """The day of the table a printed day shows: 29 February (2004) repeats 28 February."""
    if (month, day) == (2, 29) and variant.leap == "29":
        return 2, 28
    return month, day


def pronounce(age: int, language: str = "la") -> str:
    """How the age is announced after the day: in Latin by its ordinal (*quota luna sit
    pronuntianda*: "Luna vigesima"); in the Italian edition by its number after the printed
    "Luna:" (Rito per la lettura, n. 10, and Il giorno lunare, pp. 33-37: «il numero 20, che
    è la luna da enunciare»: "Luna: 20")."""
    if language[:2] == "it":
        return f"Luna: {age}"
    return f"Luna {ORDINALS[age - 1]}"


def announce(
    year: int,
    month: int,
    day: int,
    printed: bool = False,
    variant: Variant = GREGORIAN,
    language: str = "la",
) -> Announcement | None:
    """The moon announced in `year` on a civil date, or (`printed`) under a printed day's
    table; None before the Gregorian reform, or for a date the edition has no table for."""
    if year < FIRST_GREGORIAN_YEAR:
        return None
    try:
        datetime.date(year if not printed else 2000, month, day)
    except ValueError:
        return None
    if printed and (month, day) == (2, 29) and variant.leap != "29":
        return None  # no 29 February table
    m, d = printed_day(month, day, variant) if printed else table_day(year, month, day, variant)
    e = epact(year)
    col = column(e)
    age = table(variant)[day_of_year(m, d) - 1][col]
    if golden_number(year) == 1 and e != 0:
        if day_of_year(m, d) < new_moons()[e][0]:  # until January's new moon: one less
            age = age - 1 or 30
    return Announcement(
        year=year,
        golden_number=golden_number(year),
        epact=epact_label(e),
        letter=variant.letters[col],
        column=col,
        age=age,
        pronuntiatio=pronounce(age, language),
    )
