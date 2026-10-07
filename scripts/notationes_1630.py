#!/usr/bin/env python3
"""Baronius's Notationes and the margin notes of the 1630 edition, keyed by
canonical ID: footnotes.json and marginalia.json in
data/editions/martyrologium_romanum_1630/.

Each day's notes follow its eulogies; a note opens with its lemma, the eulogy's
words it comments on, and the reference letter printed in the eulogy
(`b Aristarchi.] De quo Act. Apost. …`; the day's first note, set with a drop
cap, often has the letter after the lemma or none: `DOMINICI a.]`, `NEMESII.]`).
digitize_1630.py walks the TEI and, run with mark=True, leaves each removed letter
in the eulogies as MARK + letter: that is the note's anchor. The note text runs
from its lemma to the next note, joining the paragraphs it runs on into, across
columns and pages (words hyphenated at a break are joined); verse it quotes is
set inline, lines divided by ' / '.

The notes are set in two columns, and the TEI does not always read them in
printed order. layout_1630.py measured where each note paragraph stands on the
page (scripts/data/layout_1630.json); here each day's note paragraphs are put in
printed order (page, column, height) before they are joined. Each margin note is
given to the note, or the eulogy, it stands beside on the page image
(scripts/data/marginalia_1630_placement.json, by page and block key as in
layout_1630.json: {"note": "M-D|letter"}, {"id": "mr:…"} or "SKIP", with "doubt"
where the placement was uncertain); one missing there goes by its measured place,
else by TEI order, and is listed for review.
Margin notes of the calendar apparatus (the day of the month, dominical letters,
epacts) are left to the lunar table (#96).

Notes on the day heading (`a Kalendis Ianuarij.]`, `b Luna.]`) go with the day's
first eulogy, unanchored. Orthography is as printed (long s as s), like the
eulogies. Reviewed corrections are in scripts/data/notationes_1630_overrides.json,
keyed "M-D|<letter>":
  {"id": "mr:…"}            the note belongs to this eulogy
  {"after": "<phrase>"|null} its anchor phrase
  {"text": "<…>"}           its text (a transcription fix)
  "DROP"                    not a note of this day
What couldn't be settled is listed in scripts/data/notationes_1630_review.md.

Usage:
  python3 notationes_1630.py data/sources/martyrologium_romanum_1630.tei.xml [repo_root]
(run after digitize_1630.py and align_1630_ids.py; needs lxml)
"""

import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import digitize_1630 as D  # noqa: E402
from layout_1630 import page_blocks  # noqa: E402

HERE = Path(__file__).resolve().parent
LAYOUT = HERE / "data" / "layout_1630.json"
OVERRIDES = HERE / "data" / "notationes_1630_overrides.json"
REVIEW = HERE / "data" / "notationes_1630_review.md"
PLACEMENT = HERE / "data" / "marginalia_1630_placement.json"
EDITION = "martyrologium_romanum_1630"
MIN_SCORE = 0.6  # a paragraph placed on the page with a lower match keeps its TEI order
NOTE_KINDS = ("note", "foot", "verse")

# the calendar apparatus in the margin: day numbers, dominical letters, epacts
CALENDAR = re.compile(r"^(?:(?:\d{1,2}|[A-HIMNP]|[xvijl]+|\*)\.?\s*)+$")

# ---- note starts
LETTER_START = re.compile(r"(?:^|(?<=[.;:)!?,])\s+)([a-z])\s+(?=[A-ZÆŒ*(])")


def lemma_letter(lemma):
    """(letter, lemma words) of a note's lemma: 'b Aristarchi.', 'DOMINICI a.',
    'KALENDIS a Ianuarij.', 'S. Maria ad Martyresa.'; letter None if not printed."""
    for pat, li, wi in (
        (r"^([a-z])\s+(.*)$", 1, 2),
        (r"^(.*?\S)\s+([a-z])\s*[.,]?\s*$", 2, 1),
        (r"^(\S+\s+)([a-z])\s+(.*)$", 2, None),
    ):
        m = re.match(pat, lemma, re.S)
        if m:
            words = m.group(wi) if wi else m.group(1) + m.group(3)
            return m.group(li), words.strip(" .,")
    m = re.match(r"^(.*?(?:\.|soc|Martyres))\s*([a-z])\s*[.,]?\s*$", lemma)  # glued
    if m:
        return m.group(2), m.group(1).strip(" .,")
    return None, lemma.strip(" .,")


def note_starts(t, daytext):
    """Offsets in paragraph t where a note begins: a lettered lemma after a sentence
    end, or (at the paragraph's start) a lemma that is lettered, in capitals or made
    of the day's words."""
    out = set()
    for m in re.finditer(r"\]", t):
        before = t[: m.start()]
        lo = max(0, len(before) - 100)
        if "]" in before[lo:]:
            lo = before.rindex("]", lo) + 1
        lettered = [x.start(1) for x in LETTER_START.finditer(before) if x.start(1) >= lo]
        if lettered:
            out.add(lettered[-1])
        elif m.start() <= 100 and "]" not in before:
            if (
                lemma_letter(before)[0]
                or re.match(r"[A-ZÆŒ]{2,}|[A-Z] [A-Z]{2,}", before)
                or D.lemma_in(before + "]", daytext)
            ):
                out.add(0)
    return sorted(out)


def join(a, b, sep=" "):
    """Join two pieces of a note, rejoining a word hyphenated at the break."""
    if not a:
        return b
    if re.search(r"\w-$", a) and re.match(r"[a-zæœ]", b):
        return a[:-1] + b
    return a + sep + b


# ---- whole-word phrases, as the frontend finds them
def _isw(c):
    return unicodedata.category(c)[0] in "LMN"


def occurrences(text, phrase):
    out, i = [], text.find(phrase)
    while i >= 0:
        j = i + len(phrase)
        if (i == 0 or not _isw(text[i - 1])) and (j == len(text) or not _isw(text[j])):
            out.append(i)
        i = text.find(phrase, i + 1)
    return out


def anchor_phrase(text, end):
    """The shortest run of whole words ending at `end` that occurs once in text."""
    for k in reversed([m.start() for m in re.finditer(r"\S+", text[:end])]):
        phrase = text[k:end].strip()
        if phrase and len(occurrences(text, phrase)) == 1:
            return phrase
    return None


def unmark(t):
    return re.sub(D.MARK + "[a-z]", "", t)


def fold(t):
    return re.sub(r"\s+", " ", D.fold(t)).strip()


# ---- per day
def build_notes(days, layout):
    """Each day's notes in printed order: {letter, lemma, text, segs:[(page, col, y)]}."""
    pages = page_blocks(days)
    pos = {}
    for page, blocks in pages.items():
        for i, kind, text, key in blocks:
            p = layout.get(str(page), {}).get(key)
            pos[(i, page, kind, text)] = p
    out, carry = [], None
    for i, day in enumerate(days):
        daytext = " ".join(day["paras"])
        items, last = [], (0, 0, -1.0)
        for kind, text, page in day["blocks"]:
            if kind not in NOTE_KINDS:
                continue
            p = pos.get((i, page, kind, text))
            if p and p[2] >= MIN_SCORE:
                last = (page, p[0], float(p[1]))
            elif last[0] != page:
                last = (page, 0, -1.0)
            else:
                last = (page, last[1], last[2] + 0.01)
            starts = [] if kind == "verse" else note_starts(text, daytext)
            cuts = ([0] if not starts or starts[0] else []) + starts + [len(text)]
            for k, (a, b) in enumerate(zip(cuts, cuts[1:], strict=False)):
                seg = text[a:b].strip()
                if seg:
                    where = (last[0], last[1], last[2] + 0.001 * k)
                    items.append((where, a in starts, kind, seg, (page, text)))
        items.sort(key=lambda x: x[0])
        notes = []
        for where, start, kind, seg, src in items:
            if start:
                lem = seg[: seg.index("]")]
                letter, words = lemma_letter(lem)
                notes.append(
                    {"letter": letter, "lemma": words, "text": seg, "segs": [where], "src": src}
                )
                continue
            target = notes[-1] if notes else carry
            if target is None:
                continue
            sep = " / " if kind == "verse" and target.get("verse") else " "
            target["text"] = join(target["text"], seg, sep)
            target["verse"] = kind == "verse"
            target["segs"].append(where)
        if notes:
            carry = notes[-1]
        out.append(notes)
    return out


def day_marks(day, elogia):
    """The reference letters of a day in printed order: (letter, eulogy index or
    None for the heading, offset in the unmarked eulogy)."""
    marks = []
    for k, e in enumerate(elogia):
        shift = 0
        for m in re.finditer(D.MARK + "([a-z])", e):
            marks.append((m.group(1), k, m.start() - shift))
            shift += 2
    # the heading's letters come before the eulogies' (others are the calendar's: 'x. F')
    first = D.ALPHA.find(marks[0][0]) if marks else len(D.ALPHA)
    head = [c for c in re.findall(r"\b([a-z])\b", day["titulus"]) if 0 <= D.ALPHA.find(c) < first]
    return [(c, None, 0) for c in head] + marks


def settle_letters(notes, marks):
    """Fill in letters that weren't printed: the day's first expected letter, else the
    letter after the previous note's."""
    first = marks[0][0] if marks else "a"
    for k, n in enumerate(notes):
        if n["letter"] is None:
            prev = notes[k - 1]["letter"] if k else None
            n["letter"] = D.ALPHA[D.ALPHA.find(prev) + 1] if prev else first
            n["guessed"] = True


def move_strays(all_notes, all_marks):
    """Notes read with the next (or previous) day's: a leading note whose letter this
    day has one too many of and the previous day lacks goes back to it; a trailing
    one the next day lacks goes forward."""

    def surplus(i, letter):
        have = sum(n["letter"] == letter for n in all_notes[i])
        return have - sum(m[0] == letter for m in all_marks[i])

    for i in range(1, len(all_notes)):
        notes = all_notes[i]
        while (
            notes and surplus(i, notes[0]["letter"]) > 0 and surplus(i - 1, notes[0]["letter"]) < 0
        ):
            all_notes[i - 1].append(notes.pop(0))
    for i in range(len(all_notes) - 1):
        notes = all_notes[i]
        while (
            notes
            and surplus(i, notes[-1]["letter"]) > 0
            and surplus(i + 1, notes[-1]["letter"]) < 0
        ):
            all_notes[i + 1].insert(0, notes.pop())


def lemma_keys(lemma):
    ws = [D._w(w) for w in re.findall(r"[^\s,.&]+", lemma)]
    keys = [w for w in ws if len(w) >= 3 and not D.LEMMA_GENERIC.match(w)]
    return keys or [w for w in ws if len(w) >= 3]


def by_lemma(note, mark, plain):
    """Does the mark stand by a word of the note's lemma?"""
    keys = lemma_keys(note["lemma"])
    if not keys or mark[1] is None:
        return False
    e, at = plain[mark[1]], mark[2]
    near = e[:at].split()[-4:] + e[at:].split()[:2]
    return any(D._w(w)[:5] == keys[0][:5] for w in near)


def pair_marks(notes, marks, plain):
    """{note index: mark}: by letter (by the lemma among repeats), then a note whose
    letter has no mark takes a free mark by its lemma (a misprinted letter)."""
    pairs, used, relettered = {}, set(), []
    for k, n in enumerate(notes):
        if n.get("guessed"):  # its letter wasn't printed: by the lemma first
            continue
        cands = [m for m in marks if m[0] == n["letter"] and m not in used]
        if len(cands) > 1:
            cands = [m for m in cands if by_lemma(n, m, plain)] or cands
        if cands:
            pairs[k] = cands[0]
            used.add(cands[0])
    for k, n in enumerate(notes):
        if k in pairs:
            continue
        cands = [m for m in marks if m not in used and by_lemma(n, m, plain)]
        cands.sort(key=lambda m: m[0] != n["letter"])
        if cands:
            pairs[k] = cands[0]
            used.add(cands[0])
            if n.get("guessed"):
                n["letter"] = cands[0][0]
            elif cands[0][0] != n["letter"]:
                relettered.append(k)
    for k, n in enumerate(notes):
        cands = [m for m in marks if m[0] == n["letter"] and m not in used]
        if k not in pairs and n.get("guessed") and cands:
            pairs[k] = cands[0]
            used.add(cands[0])
    return pairs, relettered


def aligned_id(plain_e, at, aligned):
    """The canonical ID whose 1630 text holds the digitized eulogy's text at `at`."""
    for left, right in ((60, 30), (30, 15), (15, 0)):
        ctx = plain_e[max(0, at - left) : at + right].strip()
        hits = [i for i, t in aligned.items() if ctx and ctx in t]
        if len(hits) == 1:
            return hits[0]
    best = max(aligned, key=lambda i: D._ratio(plain_e, aligned[i], 300), default=None)
    return best


def lemma_home(note, plain, aligned):
    """For a note without a mark: (ID, anchor) of the eulogy with its lemma's first key
    word, the anchor ending after the run of the eulogy's words matching the lemma."""
    keys = lemma_keys(note["lemma"])
    if not keys:
        return None, None
    lem = [D._w(w)[:5] for w in note["lemma"].split()]
    for e in plain:
        words = list(re.finditer(r"\S+", e))
        for i, w in enumerate(words):
            if D._w(w.group())[:5] != keys[0][:5]:
                continue
            j = i  # extend over the following lemma words ('Leucij Episc.' -> 'Leucij Episcopi')
            while j + 1 < len(words) and D._w(words[j + 1].group())[:5] in lem:
                j += 1
            end = words[j].end()
            while end > words[j].start() and not _isw(e[end - 1]):
                end -= 1
            eid = aligned_id(e, end, aligned)
            ctx = e[max(0, end - 40) : end].lstrip()
            pos = aligned[eid].find(ctx) if ctx else -1
            return eid, anchor_phrase(aligned[eid], pos + len(ctx)) if pos >= 0 else None
    return None, None


def build(tei, root=D.ROOT):
    """Everything the outputs are made from: the days, their notes (each with its
    `day` 'M-D' and `home` (ID, letter)), the footnotes and the review lists."""
    ed_dir = root / "data" / "editions" / EDITION
    layout = json.load(open(LAYOUT, encoding="utf-8"))
    overrides = json.load(open(OVERRIDES, encoding="utf-8")) if OVERRIDES.exists() else {}
    days, months = D.digitize(tei, root, mark=True)
    aligned_months = {
        mo: json.load(open(ed_dir / f"{mo:02d}.json", encoding="utf-8")) for mo in range(1, 13)
    }
    elogia = [months[x["month"]][str(x["day"])]["elogia"] for x in days]
    all_marks = [day_marks(x, e) for x, e in zip(days, elogia, strict=True)]
    all_notes = build_notes(days, layout)
    for notes, marks in zip(all_notes, all_marks, strict=True):
        settle_letters(notes, marks)
    move_strays(all_notes, all_marks)

    plains = [[unmark(e) for e in els] for els in elogia]
    # each day's notes with the mark each points at: [note, mark or None, relettered]
    entries = []
    for notes, marks, plain in zip(all_notes, all_marks, plains, strict=True):
        pairs, relettered = pair_marks(notes, marks, plain)
        entries.append([[n, pairs.get(k), k in relettered] for k, n in enumerate(notes)])
    # a note without a mark whose lemma stands by a free mark of the day before or after
    for i, day in enumerate(entries):
        for entry in [e for e in day if e[1] is None]:
            for j in (i - 1, i + 1):
                if not 0 <= j < len(entries):
                    continue
                taken = {e[1] for e in entries[j] if e[1]}
                free = [
                    m for m in all_marks[j] if m not in taken and by_lemma(entry[0], m, plains[j])
                ]
                free.sort(key=lambda m: m[0] != entry[0]["letter"])
                if free:
                    day.remove(entry)
                    entries[j].append([entry[0], free[0], free[0][0] != entry[0]["letter"]])
                    break

    footnotes, review = collections.defaultdict(list), collections.defaultdict(list)
    for x, day, marks, plain in zip(days, entries, all_marks, plains, strict=True):
        md = f"{x['month']}-{x['day']}"
        aligned = aligned_months[x["month"]][str(x["day"])]["elogia"]
        first_id = next(iter(aligned))
        for n, m, relettered in day:
            o = overrides.get(f"{md}|{n['letter']}", {})
            if o == "DROP":
                continue
            if relettered:
                review["letter differs"].append(
                    f"{md} note {n['letter']} ({n['lemma']}) ↔ mark {m[0]} in the eulogy: "
                    "the eulogy's letter kept"
                )
                n["letter"] = m[0]
            if m and m[1] is not None:
                e, at = plain[m[1]], m[2]
                eid = aligned_id(e, at, aligned)
                ctx = e[max(0, at - 40) : at].lstrip()
                pos = aligned[eid].find(ctx) if ctx else -1
                after = anchor_phrase(aligned[eid], pos + len(ctx)) if pos >= 0 else None
                if after is None:
                    review["unanchored"].append(f"{md} {n['letter']} ({n['lemma']}) → {eid}")
            elif m:  # the day heading
                eid, after = first_id, None
            else:
                eid, after = lemma_home(n, plain, aligned)
                eid = eid or first_id
                review["no mark"].append(
                    f"{md} {n['letter']} ({n['lemma']}) → {eid} after “{after}”"
                )
            eid = o.get("id", eid)
            after = o.get("after", after)
            n["home"] = (eid, n["letter"])
            n["day"] = md
            footnotes[eid].append(
                {
                    "mark": n["letter"],
                    "after": after,
                    "text": o.get("text", n["text"]),
                    "_at": (m[1] if m and m[1] is not None else -1, m[2] if m else 0),
                }
            )
        used = {m for _n, m, _r in day if m}
        for m in marks:
            if m not in used:
                where = "heading" if m[1] is None else plain[m[1]][max(0, m[2] - 30) : m[2]]
                review["mark without note"].append(f"{md} {m[0]} after “…{where}”")

    return {
        "days": days,
        "notes": all_notes,
        "elogia": elogia,
        "layout": layout,
        "aligned": aligned_months,
        "footnotes": footnotes,
        "review": review,
    }


def main():
    tei = Path(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else D.ROOT
    ed_dir = root / "data" / "editions" / EDITION
    b = build(tei, root)
    footnotes, review = b["footnotes"], b["review"]
    marginalia = build_marginalia(b["days"], b["notes"], b["layout"], review)

    def ordered(d, key):
        return {
            i: [
                {k: v for k, v in n.items() if not k.startswith("_")} for n in sorted(d[i], key=key)
            ]
            for i in sorted(d)
        }

    fn = ordered(footnotes, lambda n: n["_at"])
    mg = ordered(marginalia, lambda n: n["_at"])
    for name, data in (("footnotes.json", fn), ("marginalia.json", mg)):
        with open(ed_dir / name, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
    write_review(review, fn, mg)
    print(
        f"{sum(map(len, fn.values()))} notes on {len(fn)} eulogies, "
        f"{sum(map(len, mg.values()))} margin notes on {len(mg)} eulogies; "
        + ", ".join(f"{len(v)} {k}" for k, v in review.items())
    )


def build_marginalia(days, all_notes, layout, review):
    """Each margin note beside a note (the note's eulogy, note: its letter) or beside a
    eulogy (note: null): as placed from the page images (PLACEMENT); a margin note not
    placed there goes by where layout_1630.json found it on the page, else by TEI order
    (the note read after it on its page), and is listed for review."""
    placement = json.load(open(PLACEMENT, encoding="utf-8")) if PLACEMENT.exists() else {}
    pages = page_blocks(days)
    by_ref = {f"{n['day']}|{n['letter']}": n for notes in all_notes for n in notes if "day" in n}
    started = collections.defaultdict(list)  # (page, paragraph) -> the notes it opens
    stands = collections.defaultdict(list)  # page -> (column, y, note) of each note's pieces
    for notes in all_notes:
        for n in notes:
            started[n["src"]].append(n)
            for page, col, y in n["segs"]:
                if y >= 0:
                    stands[page].append((col, y, n))
    out = collections.defaultdict(list)
    for page, blocks in sorted(pages.items()):
        for j, (i, kind, text, key) in enumerate(blocks):
            if not kind.startswith("margin"):
                continue
            text = re.sub(r"(\w)- (\w)", r"\1\2", D.norm(text))
            if CALENDAR.match(text):
                continue
            x = days[i]
            where = f"{x['month']}-{x['day']} p{page} “{text}”"
            placed = placement.get(str(page), {}).get(key)
            p = layout.get(str(page), {}).get(key)
            y = p[1] if p else 0
            if placed == "SKIP":
                continue
            if placed and placed.get("doubt"):
                review["margin placement in doubt"].append(f"{where}: {placed['doubt']}")
            if placed and "id" in placed:
                out[placed["id"]].append({"text": text, "note": None, "_at": (-1, y)})
                continue
            note = by_ref.get(placed["note"]) if placed and "note" in placed else None
            if note is None:
                if p:  # the nearest note above it in the column beside it
                    here = [s for s in stands[page] if s[0] == p[0] and s[1] <= p[1] + 8]
                    note = max(here, key=lambda s: s[1])[2] if here else None
                if note is None:
                    nxt = [n for b in blocks[j + 1 :] for n in started[(page, b[2])]]
                    prv = [n for b in blocks[:j] for n in started[(page, b[2])]]
                    note = (nxt or prv[-1:] or [None])[0]
                review["margin placed without the page image"].append(where)
            if note is None or "home" not in note:
                review["margin not placed"].append(where)
                continue
            eid, letter = note["home"]
            out[eid].append({"text": text, "note": letter, "_at": (D.ALPHA.find(letter), y)})
    return out


def write_review(review, fn, mg):
    lines = [
        "# 1630 Notationes and marginalia: to review",
        "",
        "Written by `scripts/notationes_1630.py`. Settle an item with an entry in",
        "`notationes_1630_overrides.json` (see the script's docstring), then rerun it.",
        "",
        f"{sum(map(len, fn.values()))} notes on {len(fn)} eulogies; "
        f"{sum(map(len, mg.values()))} margin notes on {len(mg)} eulogies.",
    ]
    for k, v in review.items():
        lines += ["", f"## {k} ({len(v)})", ""] + [f"- {x}" for x in v]
    REVIEW.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
