#!/usr/bin/env python3
"""Measure where Baronius's notes and the margin notes of the 1630 edition sit on
the page, for notationes_1630.py.

The TEI keeps the notes in the order the transcription read them, which is not
always the printed order: the notes are set in two columns, a note that runs on
into the next column or page can be read before or after the notes beside it,
and the margin notes (mostly references to Baronius's Annales) were read in
batches, away from the note they stand beside. This script finds each note
paragraph and margin note of pp. 39-688 on the page image, by matching its
opening words against the words tesseract reads there, and records its column
(0 left, 1 right) or margin side and its height on the page; the eulogy
paragraphs too, so a margin note beside the eulogies can be told from one beside
the notes.

Output: scripts/data/layout_1630.json, keyed by page then by the block's key
(see block_key): [column, y in thousandths of the page height, match score] for
a paragraph, [side, y, score, the words of the text line beside it] for a margin
note. Blocks it couldn't find are left out; notationes_1630.py then keeps their
TEI order.

Usage (needs the scan, PyMuPDF and tesseract with the Latin model):
  python3 layout_1630.py data/sources/martyrologium_romanum_1630.tei.xml scan.pdf [ocr_dir]
The scan: Internet Archive bub_gb_2pQUlbrbtAsC (the TEI's page n = PDF page n).
ocr_dir keeps tesseract's word boxes between runs (default: a temporary directory).
"""

import collections
import csv
import difflib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import digitize_1630 as D  # noqa: E402

OUT = Path(__file__).resolve().parent / "data" / "layout_1630.json"


def block_key(kind, text, seen):
    """A block's key on its page: 'm' (margin note), 't' (eulogy text) or 'n' (note)
    + '|' + its first 40 characters, '#2' etc. for a repeat."""
    k = ("m" if kind.startswith("margin") else "t" if kind == "text" else "n") + "|" + text[:40]
    seen[k] += 1
    return k if seen[k] == 1 else f"{k}#{seen[k]}"


def page_blocks(days):
    """{page: [(day index, kind, text, key)]} in the order of the walk."""
    pages, seen = collections.defaultdict(list), collections.defaultdict(collections.Counter)
    for i, day in enumerate(days):
        for kind, text, page in day["blocks"]:
            pages[page].append((i, kind, text, block_key(kind, text, seen[page])))
    return pages


def nw(w):
    """A word for matching against OCR: folded, f and long s merged (the OCR confuses them)."""
    w = w.replace("æ", "ae").replace("Æ", "ae").replace("œ", "oe").replace("Œ", "oe")
    w = w.replace("ę", "e").replace("ſ", "s")
    w = "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))
    w = w.lower().replace("j", "i").replace("v", "u").replace("f", "s").replace("y", "i")
    return re.sub(r"[^a-z0-9]", "", w)


def ocr_page(pdf, n, ocr_dir):
    """Tesseract's words on scan page n: (left, top, right, bottom, text), page height."""
    import fitz  # PyMuPDF

    tsv = ocr_dir / f"p{n:03d}.tsv"
    if not tsv.exists() or not tsv.stat().st_size:
        png = ocr_dir / f"p{n:03d}.png"
        # the scan's foreground layer is ~600 dpi; render it at full resolution
        fitz.open(pdf)[n - 1].get_pixmap(dpi=600).save(png)
        subprocess.run(
            ["tesseract", str(png), str(tsv.with_suffix("")), "-l", "lat", "tsv"],
            check=True,
            capture_output=True,
            env={"OMP_THREAD_LIMIT": "1", "PATH": "/usr/bin:/usr/local/bin"},
        )
        png.unlink()
    words, height = [], 1
    with open(tsv, encoding="utf-8") as f:
        rows = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        next(rows)
        for x in rows:
            if len(x) < 12:
                continue
            if x[0] == "1":
                height = int(x[9])
            if x[0] != "5" or not x[11].strip():
                continue
            left, top, w, h = map(int, x[6:10])
            if w > 600 or h > 150:  # ornaments and rules read as words
                continue
            words.append((left, top, left + w, top + h, x[11]))
    return words, height


class Page:
    """The text body's edges (where word density drops), its two note columns, and
    the lines of words in each margin."""

    def __init__(self, words, height):
        self.height = height
        width = max([w[2] for w in words] + [1]) + 1
        cov = [0] * width
        for w in words:
            for x in range(w[0], w[2]):
                cov[x] += 1
        smooth = [sum(cov[max(0, x - 5) : x + 5]) / 10 for x in range(width)]
        core = sorted(smooth[width // 4 : 3 * width // 4])
        thr = 0.4 * core[len(core) // 2] if core else 1
        dense = [x for x in range(width) if smooth[x] >= thr]
        self.left, self.right = (dense[0], dense[-1]) if dense else (0, width)
        # the gutter between the note columns: the least covered x near the middle
        lo = int(self.left + 0.35 * (self.right - self.left))
        hi = int(self.left + 0.65 * (self.right - self.left))
        self.mid = min(range(lo, hi), key=lambda x: sum(cov[x - 10 : x + 10])) if hi > lo else lo
        self.body = [
            (w, nw(w[4]))
            for w in words
            if nw(w[4]) and w[0] >= self.left - 5 and w[2] <= self.right + 5
        ]
        hs = sorted(w[3] - w[1] for w, _n in self.body) or [30]
        self.lh = hs[len(hs) // 2]
        # tesseract reads some lines straight across both columns: match the notes
        # against the words column by column too
        self.columns = sorted(
            self.body,
            key=lambda b: (
                (b[0][0] + b[0][2]) / 2 >= self.mid,
                round((b[0][1] + b[0][3]) / 2 / self.lh),
                b[0][0],
            ),
        )
        self.mlines = []
        for side in (0, 1):
            ws = sorted(
                (w for w in words if (w[2] < self.left if side == 0 else w[0] > self.right)),
                key=lambda w: w[1] + w[3],
            )
            lines = []
            for w in ws:
                c = (w[1] + w[3]) / 2
                if lines and abs(c - lines[-1]["c"]) < 0.6 * self.lh:
                    lines[-1]["ws"].append(w)
                else:
                    lines.append({"side": side, "c": c, "ws": [w]})
            for ln in lines:
                ln["top"] = min(w[1] for w in ln["ws"])
                ln["n"] = "".join(nw(w[4]) for w in sorted(ln["ws"]))
            self.mlines += lines

    def y(self, top):
        return round(1000 * top / self.height)

    def locate(self, text, n=8):
        """[column, y, score] of the body word sequence best matching text's first words."""
        q = [x for x in (nw(t) for t in text.split()) if x][:n]
        if not q or not self.body:
            return None
        qs, best = " ".join(q), (0.0, None)
        for seq in (self.body, self.columns):
            for k in range(len(seq)):
                s = " ".join(n for _w, n in seq[k : k + len(q)])
                r = difflib.SequenceMatcher(None, qs, s, autojunk=False).ratio()
                if r > best[0]:
                    best = (r, [w for w, _n in seq[k : k + len(q)]])
        # the column most of the window's words are in, and its first word there
        cols = [int((w[0] + w[2]) / 2 >= self.mid) for w in best[1]]
        col = int(sum(cols) * 2 > len(cols)) if sum(cols) * 2 != len(cols) else cols[0]
        w = next(w for w, c in zip(best[1], cols, strict=True) if c == col)
        return [col, self.y(w[1]), round(best[0], 2)]

    def beside(self, top):
        """The body words on the line at height top."""
        c = top + self.lh / 2
        ws = sorted(w for w, _n in self.body if abs((w[1] + w[3]) / 2 - c) < 0.6 * self.lh)
        return " ".join(w[4] for w in ws)

    def match_margins(self, texts, floor=0.45):
        """[side, y, score, beside] per margin note: the run of margin lines its text best
        matches, each line used once, best matches first."""
        cands = []
        for i, t in enumerate(texts):
            q = nw(t.replace(" ", ""))
            k = max(1, round(len(q) / 8))  # ~8 characters to a margin line
            for side in (0, 1):
                ls = [ln for ln in self.mlines if ln["side"] == side]
                for j in range(len(ls)):
                    for kk in sorted({max(1, k - 1), k, k + 1, k + 2}):
                        seg = ls[j : j + kk]
                        if len(seg) < kk or seg[-1]["c"] - seg[0]["c"] > (kk + 0.5) * 1.5 * self.lh:
                            continue
                        s = "".join(ln["n"] for ln in seg)
                        r = difflib.SequenceMatcher(None, q, s, autojunk=False).ratio()
                        cands.append((r, i, side, j, kk, seg[0]["top"]))
        cands.sort(reverse=True)
        used, out = set(), [None] * len(texts)
        for r, i, side, j, kk, top in cands:
            if r < floor or out[i] is not None:
                continue
            lines = {(side, x) for x in range(j, j + kk)}
            if lines & used:
                continue
            used |= lines
            out[i] = [side, self.y(top), round(r, 2), self.beside(top)]
        return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    tei, pdf = Path(sys.argv[1]), sys.argv[2]
    ocr_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp())
    ocr_dir.mkdir(parents=True, exist_ok=True)
    pages = page_blocks(D.extract_days(tei))
    layout, found, total = {}, 0, 0
    for n in sorted(pages):
        pg = Page(*ocr_page(pdf, n, ocr_dir))
        blocks = pages[n]
        margins = [i for i, b in enumerate(blocks) if b[1].startswith("margin")]
        pos = dict(zip(margins, pg.match_margins([blocks[i][2] for i in margins]), strict=True))
        for i, (_day, kind, text, _key) in enumerate(blocks):
            if not kind.startswith("margin"):
                pos[i] = pg.locate(text)
        entry = {blocks[i][3]: p for i, p in sorted(pos.items()) if p}
        total += len(blocks)
        found += len(entry)
        layout[str(n)] = entry
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=0, sort_keys=False)
        f.write("\n")
    print(f"{found}/{total} blocks placed on {len(layout)} pages -> {OUT}")


if __name__ == "__main__":
    main()
