#!/usr/bin/env python3
"""Check the 2004 edition's lunar tables (Latin and Italian) against the computus.

The 2004 Latin (editio typica altera) and its Italian translation print, under each day's
heading, the lunar table of the 2004 rules (De pronuntiatione lunæ ad libitum peragenda,
pp. 23-27; Il giorno lunare, pp. 33-37): src/martyrology_api/lunar.py's GREGORIAN_2004. The
API computes the tables; this script reads them from the PDFs by word positions (a row of
letters, the row of numbers under it, twice: 19 and 12 columns) and lists every cell that
differs from the computus, for checking against the page. The cells the page shows misprinted
go in each edition's lunar_misprints.json (the texts repository: these editions are under
copyright, the PDFs and the texts are not public). Only numbers are written here.

Usage:
  python3 lunar_2004.py <Latin 2004 PDF> <Italian 2004 PDF> [report.md]
(needs PyMuPDF)
"""

import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from martyrology_api import lunar as L  # noqa: E402

REPORT = Path(__file__).resolve().parent / "data" / "lunar_2004_check.md"
MONTHS = {
    m: i + 1
    for names in (
        "ianuarii februarii martii aprilis maii iunii iulii augusti septembris octobris "
        "novembris decembris",
        "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre "
        "novembre dicembre",
    )
    for i, m in enumerate(names.split())
}
V = L.GREGORIAN_2004
DAYS = [
    f"{m:02d}-{d:02d}"
    for m, n in enumerate((31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31), 1)
    for d in range(1, n + 1)
]
# Latin days the OCR doesn't read in order, checked on the page images: as computed.
CHECKED_BY_EYE = set(
    "01-20 02-26 02-27 02-28 03-23 04-07 04-11 05-02 05-06 05-12 05-16 06-27 07-12 09-26 "
    "10-11 10-30 12-11".split()
)


def lines_of(words):
    """The page's lines, top to bottom: (top, bottom, text)."""
    lines = {}
    for x0, y0, _x1, y1, w, b, ln, _n in words:
        lines.setdefault((b, ln), []).append((x0, y0, y1, w))
    return sorted(
        (min(v[1] for v in ws), max(v[2] for v in ws), " ".join(v[3] for v in sorted(ws)))
        for ws in lines.values()
    )


def in_order(text, after):
    """The numbers after a day's "Luna:" in the text layer's order (the OCR of a scan keeps
    a row's numbers in order more often than in place)."""
    nums = []
    for tok in text[after:].split():
        if re.fullmatch(r"\d{1,2}", tok):
            nums.append(int(tok))
        elif not re.fullmatch(r"[A-Za-z]\.?|[~I|]", tok):
            break
    return nums


def tables(pdf):
    """{"MM-DD": (page, [31 ages] or None, how)}: each day's table, its numbers matched to the
    letters above them by position, else (when it is the computed row) in the text's order."""
    out = {}
    doc = fitz.open(pdf)
    for i in range(len(doc)):
        page = doc[i]
        words = page.get_text("words")
        lines = lines_of(words)
        for k, (_top, _bot, text) in enumerate(lines):
            m = re.fullmatch(r"(?:Die\s+)?(\d{1,2})\s+([A-Za-z][A-Za-z ]*)", text.strip())
            month = m and m[2].replace(" ", "").lower()
            if not m or month not in MONTHS:
                continue
            luna = next((b for _t, b, t in lines[k + 1 : k + 4] if "Luna" in t), None)
            if luna is None:
                continue
            below = sorted((w for w in words if w[1] > luna - 1), key=lambda w: (w[1], w[0]))
            rows, cur, last = [], [], None
            for w in below:
                if last is not None and abs(w[1] - last) > 4:
                    rows.append(cur)
                    cur = []
                cur.append(w)
                last = w[1]
            rows.append(cur)
            kinds = []
            for r in rows[:4]:
                r = sorted(r, key=lambda w: w[0])
                if all(re.fullmatch(r"[A-Za-z]", w[4]) for w in r):
                    kinds.append(("L", r))
                elif all(re.fullmatch(r"\d{1,2}", w[4]) for w in r):
                    kinds.append(("N", r))
            ages = []
            for (k1, letters), (k2, nums) in zip(kinds[::2], kinds[1::2], strict=False):
                if (k1, k2) != ("L", "N"):
                    ages = []
                    break
                for lt in letters:
                    cx = (lt[0] + lt[2]) / 2
                    ages.append(int(min(nums, key=lambda n: abs((n[0] + n[2]) / 2 - cx))[4]))
            key = f"{MONTHS[month]:02d}-{int(m[1]):02d}"
            how = "position"
            if len(ages) != 31:
                text = page.get_text()
                at = re.search(rf"\b{int(m[1])}\s+{re.escape(m[2])}\b.*?Luna\s*:", text, re.S)
                mo, dd = MONTHS[month], int(m[1])
                pm, pd = L.printed_day(mo, dd, V)
                want = L.table(V)[L.day_of_year(pm, pd) - 1]
                ages = in_order(text, at.end()) if at else []
                how = "order"
                if ages != want:
                    ages = []
            out.setdefault(key, (i + 1, ages if len(ages) == 31 else None, how))
    return out


def check(name, pdf):
    lines, same, unread, by_eye = [], 0, [], 0
    found = tables(pdf)
    for key in DAYS:
        if key not in found:
            if key in CHECKED_BY_EYE:
                by_eye += 1
            else:
                unread.append(f"{key} (heading not found)")
    for key, (page, ages, _how) in sorted(found.items()):
        if ages is None:
            if key in CHECKED_BY_EYE:
                by_eye += 1
            else:
                unread.append(f"{key} (p. {page})")
            continue
        m, d = map(int, key.split("-"))
        pm, pd = L.printed_day(m, d, V)
        want = L.table(V)[L.day_of_year(pm, pd) - 1]
        cells = [
            f"{V.letters[k]} ({L.epact_label(L.COLUMNS[k][0])}): {w} computed, {g} read"
            for k, (w, g) in enumerate(zip(want, ages, strict=True))
            if w != g
        ]
        if cells:
            lines.append(f"- {key} (p. {page}): " + "; ".join(cells))
        else:
            same += 1
    return [
        f"## {name}",
        "",
        f"{same} days read as computed; {by_eye} checked on the page images, as computed; "
        f"{len(lines)} with differences; {len(unread)} not read (check by hand): "
        f"{', '.join(unread) or 'none'}.",
        "",
        *lines,
        "",
    ]


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    report = Path(sys.argv[3]) if len(sys.argv) > 3 else REPORT
    out = [
        "# 2004 lunar tables: the print against the computus",
        "",
        "Written by `scripts/lunar_2004.py` from the PDFs (not in the repository). Cells the "
        "page shows misprinted are in each edition's `lunar_misprints.json`.",
        "",
        *check("Latin (martyrologium_romanum_2004)", sys.argv[1]),
        *check("Italian (martyrologium_romanum_2004_it_IT)", sys.argv[2]),
    ]
    report.write_text("\n".join(out), encoding="utf-8")
    print(f"written {report}")


if __name__ == "__main__":
    main()
