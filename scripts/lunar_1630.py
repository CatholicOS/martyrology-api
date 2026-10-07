#!/usr/bin/env python3
"""Check the 1630 edition's lunar tables against the Gregorian computus.

The tables under each day's heading are not stored: the API computes them
(src/martyrology_api/lunar.py) for every edition that declares `"lunar_table":
"gregorian"` in its source.json. This script reads the tables as transcribed in the
TEI and compares every cell with the computus. A cell the page images confirm as the
printer's error goes in data/editions/martyrologium_romanum_1630/lunar_misprints.json
("MM-DD" → epact → the age printed); every other difference is the transcription's
(misread digits, a row left out, a day number of the margin read into the row) and is
listed for the record in scripts/data/lunar_1630_check.md.

Usage:
  python3 lunar_1630.py data/sources/martyrologium_romanum_1630.tei.xml [repo_root]
(needs lxml)
"""

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import digitize_1630 as D  # noqa: E402
from lxml import etree  # noqa: E402

from martyrology_api import lunar as L  # noqa: E402

HERE = Path(__file__).resolve().parent
REPORT = HERE / "data" / "lunar_1630_check.md"
EDITION = "martyrologium_romanum_1630"
# Differences checked against the page images that lunar_misprints.json can't hold (the
# print's own slips that leave every cell's value right, the transcription's slips).
CHECKED = {
    "03-09": "print: '17 18' set twice in the first row (19 numbers under 17 letters)",
    "09-05": "transcription: 13 read as '1 3'",
    "10-29": "transcription: one of the two 2s (f, F) left out",
    "11-03": "transcription: the day of the month (margin) read into the row",
}
NS = "{http://www.tei-c.org/ns/1.0}"
LETTERS = set(L.LETTERS)


def text(el):
    return " ".join("".join(el.itertext()).split())


def blocks(tei):
    """The Martyrology's headings, paragraphs, notes and table rows, in reading order."""
    out, page = [], None
    for el in etree.parse(str(tei)).getroot().find(f".//{NS}body").iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.replace(NS, "")
        if tag == "pb":
            page = int(el.get("n"))
            continue
        if page is None or not D.FIRST_PAGE <= page <= D.LAST_PAGE or page in D.DUPLICATE_PAGES:
            continue
        if tag == "row":
            out.append((page, "row", [text(c) for c in el.findall(f"{NS}cell")]))
        elif tag in ("head", "p", "note") and not any(
            a.tag == f"{NS}table" for a in el.iterancestors()
        ):
            out.append((page, tag, text(el).split()))
    return out


def tokens(words):
    """A row's letters and numbers: dots dropped, '3-4' split."""
    return [t for w in words for t in re.split(r"[.\-]", w) if t]


def transcribed(tei):
    """{(month, day): (scan page, the ages as transcribed, in printed order)}."""
    seq, days, i = blocks(tei), {}, 0
    while i < len(seq):
        page, tag, words = seq[i]
        i += 1
        if tag != "head" or "Luna" not in " ".join(words):
            continue
        md = D.roman_date(D.strip_acc(" ".join(words)))
        ages = []
        while i < len(seq):
            _page, tag2, w2 = seq[i]
            toks = tokens(w2)
            if len(toks) == 1 and md and toks[0] == str(md[1]):  # the day of the month
                i += 1
                continue
            if tag2 == "row" or (len(toks) > 4 and all(t in LETTERS or t.isdigit() for t in toks)):
                ages += [int(t) for t in toks if t.isdigit()]
                i += 1
                continue
            if tag2 in ("head", "note") or len(toks) <= 4:  # the margin apparatus
                i += 1
                continue
            break
        if md and md not in days:
            days[md] = (page, ages)
    return days


def main():
    tei = Path(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else D.ROOT
    misprints = json.loads(
        (root / "data" / "editions" / EDITION / "lunar_misprints.json").read_text("utf-8")
    )
    days = transcribed(tei)
    lines, same, printed = [], 0, set()
    for (m, d), (page, ages) in sorted(days.items()):
        want = L.table()[L.day_of_year(m, d) - 1]
        known = misprints.get(f"{m:02d}-{d:02d}", {})
        if not ages:
            lines.append(f"- {m}-{d} (p. {page}): no table transcribed")
            continue
        if ages == want:
            same += 1
            continue
        diffs = []
        if len(ages) == len(want):  # a whole row: cell by cell
            ops = [
                ("replace", k, k + 1, k, k + 1) if a != b else ("equal", k, k + 1, k, k + 1)
                for k, (a, b) in enumerate(zip(want, ages, strict=True))
            ]
        else:  # a cell left out or read twice: align the rows
            ops = difflib.SequenceMatcher(a=want, b=ages, autojunk=False).get_opcodes()
        for op, a0, a1, b0, b1 in ops:
            if op == "equal":
                continue
            cols = [f"{L.LETTERS[k]} ({L.epact_label(L.COLUMNS[k][0])})" for k in range(a0, a1)]
            got = ages[b0:b1]
            mis = (
                op == "replace"
                and a1 - a0 == b1 - b0
                and all(
                    known.get(L.epact_label(L.COLUMNS[k][0])) == g
                    for k, g in zip(range(a0, a1), got, strict=True)
                )
            )
            if mis:
                printed.update(
                    f"{m:02d}-{d:02d}|{L.epact_label(L.COLUMNS[k][0])}" for k in range(a0, a1)
                )
            what = "misprint, recorded" if mis else "see the page"
            diffs.append(f"{', '.join(cols) or '—'}: {want[a0:a1]} computed, {got} read ({what})")
        note = CHECKED.get(f"{m:02d}-{d:02d}")
        lines.append(f"- {m}-{d} (p. {page}): " + "; ".join(diffs) + (f" ({note})" if note else ""))
    recorded = {f"{day}|{e}" for day, cells in misprints.items() for e in cells}
    if recorded - printed:
        sys.exit(f"recorded misprints not found in the transcription: {sorted(recorded - printed)}")
    REPORT.write_text(
        "\n".join(
            [
                "# 1630 lunar tables: the transcription against the computus",
                "",
                "Written by `scripts/lunar_1630.py`. The API computes the tables "
                "(`src/martyrology_api/lunar.py`); the cells the page images show "
                "misprinted are in "
                f"`data/editions/{EDITION}/lunar_misprints.json`.",
                "",
                f"{len(days)} days read; {same} transcribed exactly as computed; "
                f"{len(misprints)} days with misprints in the print.",
                "",
                *lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(days)} days, {same} as computed, {len(lines)} listed in {REPORT.name}")


if __name__ == "__main__":
    main()
