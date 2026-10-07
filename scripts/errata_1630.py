#!/usr/bin/env python3
"""The 1630 edition's own errata (ERRATA IN TEXTV MARTYROLOGII, scan p. 747), keyed by
canonical ID: data/editions/martyrologium_romanum_1630/printed_errata.json.

The errata list corrections to the Martyrology's text by printed page and line
("Primus numerus Paginam, secundus Lineam ipsius textus indicat"); errata in the
notes are left to the reader. Each entry becomes one or more items:
  replace   'Cononiæ, Bononiæ.'                printed → corrected
  add       'post Item, adde, Romæ.'           printed = what it follows
  delete    'dele Bergomi S. Domnonis martyris.'
  replace   'dele vndequadraginta, & pone triginta nouem.'
and 'vbique' entries apply to every eulogy with the word. The texts stay as
printed: the errata are shown beside them, not applied.

Each item's `printed` phrase occurs exactly once in its eulogy's text as whole
words (like a footnote's `after`); the eulogy is found among those printed on the
entry's page. Printed page p is scan page p + 38, or p + 52 after the scan's
duplicated sheet (printed pp. 430-443 are scanned twice). What couldn't be placed
is listed in scripts/data/errata_1630_review.md; reviewed fixes go in
scripts/data/errata_1630_overrides.json, keyed by the entry's ref ("81.20"):
{"id": "mr:…"} the eulogy; "printed": the phrase as our text prints it;
"corrected" (and "kind") when the erratum misquotes the print; "position": "before"
for an addition that opens the next eulogy ('post sunt, adde, Item': Item begins the
eulogy after); {"drop": "<why>"} when it doesn't apply to this copy.

Usage:
  python3 errata_1630.py data/sources/martyrologium_romanum_1630.tei.xml [repo_root]
(run after digitize_1630.py and align_1630_ids.py; needs lxml)
"""

import collections
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import lxml.etree as etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import digitize_1630 as D  # noqa: E402

HERE = Path(__file__).resolve().parent
OVERRIDES = HERE / "data" / "errata_1630_overrides.json"
REVIEW = HERE / "data" / "errata_1630_review.md"
EDITION = "martyrologium_romanum_1630"
ERRATA_PAGE = "747"
REF = re.compile(r"(?:(?<=\s)|^)(?:(\d{1,3})\.(\d{1,2})[.,]|ibid\.\s*(\d{1,2})\.?)(?=\s)")


def scan_page(printed):
    return printed + (38 if printed <= 443 else 52)


def errata_text(tei):
    """The errata paragraphs, the instruction words (set in italics) as ⟨post⟩ etc."""
    body = etree.parse(str(tei)).getroot().find(f".//{D.NS}body")
    page, out = None, []
    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.replace(D.NS, "")
        if tag == "pb":
            page = el.get("n")
        elif page == ERRATA_PAGE and tag == "p":
            parts = [el.text or ""]
            for c in el:
                inner = (c.text or "").strip()
                parts.append(f"⟨{inner}⟩" if c.tag == f"{D.NS}hi" and inner else (c.text or ""))
                parts.append(c.tail or "")
            out.append(D.norm("".join(parts)))
    return " ".join(out)


def entries(text):
    """(ref, page, body, as printed) per entry; 'ibid.' keeps the page before it."""
    marks = list(REF.finditer(text))
    out, page = [], None
    for m, nxt in zip(marks, marks[1:] + [None], strict=False):
        page = int(m.group(1)) if m.group(1) else page
        line = int(m.group(2) or m.group(3))
        body = text[m.end() : nxt.start() if nxt else len(text)].strip()
        printed = re.sub(r"⟨([^⟩]*)⟩", r"\1", text[m.start() : nxt.start() if nxt else len(text)])
        out.append((f"{page}.{line}", page, body, D.norm(printed)))
    return out


def clean(t):
    return t.strip(" ,.;")


def halves(t):
    """'Cartheiæ, & Buphasius, Cartheiæ, & Euphrasius' → the two readings."""
    t = t.strip(" ,")
    pieces = [p.strip() for p in t.split(",")]
    if len(pieces) % 2 == 0 and len(pieces) > 1:
        k = len(pieces) // 2
        return clean(", ".join(pieces[:k])), clean(", ".join(pieces[k:]))
    m = re.fullmatch(r"(\S+)\.\s+(\S+)\.?", t)  # 'Vrsiceni. Vrciceni.'
    return (clean(m.group(1)), clean(m.group(2))) if m else None


def ops(body):
    """The items an entry's body asks for, as (kind, printed, corrected, context)."""
    b = body.replace("⟨&⟩", "&")
    m = re.fullmatch(r"⟨post⟩\s*,?\s*(.+?),?\s*⟨adde⟩\s*,?\s*(.+?)(?:,\s*&\s*⟨dele⟩\s*(.+))?", b)
    if m:
        out = [("add", clean(m.group(1)), clean(m.group(2)), None)]
        if m.group(3):
            out.append(("delete", clean(m.group(3)), "", None))
        return out
    m = re.fullmatch(r"⟨post⟩\s*,?\s*(.+?)\s*⟨dele⟩\s*,?\s*(.+)", b)
    if m:  # 'post, generis. dele, vitæ.': delete what follows
        return [("delete", clean(m.group(2)), "", clean(m.group(1)))]
    m = re.fullmatch(r"⟨dele⟩\s*,?\s*(.+?)[,.]?\s*&\s*⟨pone⟩\s*,?\s*(.+)", b)
    if m:
        return [("replace", clean(m.group(1)), clean(m.group(2)), None)]
    m = re.fullmatch(r"(.+?),\s*⟨dele⟩\s*,\s*(.+)", b)
    if m:  # '& alij ignibus, dele, &.': delete a word within the phrase
        return [("delete", clean(m.group(2)) or "&", "", clean(m.group(1)))]
    m = re.fullmatch(r"⟨dele⟩\s*,?\s*(.+)", b)
    if m:
        return [("delete", clean(m.group(1)), "", None)]
    if "⟨" not in b:
        h = halves(b)
        if h:
            return [("replace", h[0], h[1], None)]
    return []


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


def _fold(text):
    """text without accents, ę as æ (the print uses both), with each folded
    character's offset in text."""
    out, at = [], []
    for i, c in enumerate(text.replace("ę", "æ")):
        for d in unicodedata.normalize("NFD", c):
            if not unicodedata.combining(d):
                out.append(d)
                at.append(i)
    return "".join(out), at


def as_printed(text, phrase):
    """The run of text that reads `phrase` once accents are set aside (the errata
    print "Agathopodis" for the text's "Agathopŏdis"), or None."""
    ft, at = _fold(text)
    fp, _ = _fold(phrase)
    hits = occurrences(ft, fp)
    if not hits:
        return None
    i, j = hits[0], hits[0] + len(fp)
    return text[at[i] : at[j] if j < len(at) else len(text)]


def anchored(text, phrase, context):
    """The phrase as it must be given to occur once in text: itself, or (for a
    word deleted within a phrase, or one repeated) widened by its context; each as
    the text prints it, accents included."""
    phrase = as_printed(text, phrase) or phrase
    context = context and (as_printed(text, context) or context)
    if len(occurrences(text, phrase)) == 1:
        return phrase
    if context:
        for wide in (f"{context} {phrase}", f"{context}, {phrase}"):
            if len(occurrences(text, wide)) == 1:
                return wide
        if phrase in context and len(occurrences(text, context)) == 1:
            return context
    return None


def main():
    tei = Path(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else D.ROOT
    ed_dir = root / "data" / "editions" / EDITION
    overrides = json.load(open(OVERRIDES, encoding="utf-8")) if OVERRIDES.exists() else {}
    days = D.extract_days(tei)
    aligned = {
        mo: json.load(open(ed_dir / f"{mo:02d}.json", encoding="utf-8")) for mo in range(1, 13)
    }
    on_page = collections.defaultdict(list)  # scan page → eulogies (id, text) of the days on it
    for x in days:
        pages = sorted({p for k, _t, p in x["blocks"] if k == "text"})
        for p in pages:
            on_page[p] += list(aligned[x["month"]][str(x["day"])]["elogia"].items())
    every = [i for m in aligned.values() for d in m.values() for i in d["elogia"].items()]

    errata, review = collections.defaultdict(list), collections.defaultdict(list)
    text = errata_text(tei)
    vbique = re.search(r"([^.]*?)\s*,\s*⟨vbique⟩\s*,\s*(.+?)\.?$", text)
    for ref, page, body, printed_entry in entries(text[: vbique.start()] if vbique else text):
        o = overrides.get(ref) or {}
        if "drop" in o:
            continue
        items = ops(body)
        if "corrected" in o:  # the reviewed reading replaces the parsed one
            items = [(o.get("kind", "replace"), o["printed"], o["corrected"], None)]
        elif "printed" in o and items:
            items = [(items[0][0], o["printed"], items[0][2], None)] + items[1:]
        if not items:
            review["not parsed"].append(f"{ref}: {printed_entry}")
            continue
        for kind, printed, corrected, context in items:
            if printed == corrected:
                review["printed and corrected read the same"].append(f"{ref}: {printed_entry}")
                continue
            cands = on_page.get(scan_page(page), [])
            hits = [(i, t) for i, t in cands if anchored(t, printed, context)]
            if "id" in o:
                hits = [(o["id"], aligned_text(aligned, o["id"]))]
            if len(hits) != 1:
                review["no single eulogy" if hits else "not found on its page"].append(
                    f"{ref} ({kind} “{printed}”): {[i for i, _t in hits] or printed_entry}"
                )
                continue
            eid, etext = hits[0]
            phrase = anchored(etext, printed, context)
            if phrase is None:
                review["not found in its eulogy"].append(f"{ref} ({kind} “{printed}”) in {eid}")
                continue
            printed = as_printed(phrase, printed) or printed
            if kind == "delete" and phrase != printed:
                # a word deleted within a wider phrase: the phrase, and what is left of it
                kind, corrected = "replace", D.norm(phrase.replace(printed, "", 1)).strip(" ,")
            elif kind != "delete" and phrase != printed:
                corrected = (
                    phrase.replace(printed, corrected, 1) if kind == "replace" else corrected
                )
            errata[eid].append(
                {
                    "kind": kind,
                    "printed": phrase,
                    "corrected": corrected,
                    "ref": ref,
                    "entry": printed_entry,
                    **({"position": "before"} if o.get("position") == "before" else {}),
                    "_at": etext.find(phrase),
                }
            )
    if vbique:  # 'Anachoretæ, & Lerinensis, vbique, Anachoritæ, & Lirinensis.'
        olds = [w.strip(" &") for w in vbique.group(1).split(",") if w.strip(" &")]
        news = [w.strip(" &.") for w in vbique.group(2).split(",") if w.strip(" &.")]
        entry = re.sub(r"⟨([^⟩]*)⟩", r"\1", vbique.group(0)).strip()
        for old, new in zip(olds, news, strict=True):
            k = len(os.path.commonprefix([old, new]))  # the letter to change
            stem = _fold(old[: k + 2].lower())[0]
            for eid, etext in every:
                for w in sorted(set(re.findall(r"\w+", etext))):
                    if not _fold(w.lower())[0].startswith(stem) or len(w) <= k:
                        continue
                    # every form of the word, in any case: only that letter changes
                    fixed = w[:k] + (new[k].upper() if w[k].isupper() else new[k]) + w[k + 1 :]
                    for at in occurrences(etext, w):
                        errata[eid].append(
                            {
                                "kind": "replace",
                                "printed": w,
                                "corrected": fixed,
                                "ref": "vbique",
                                "entry": entry,
                                "_at": at,
                            }
                        )
    out = {
        i: [
            {k: v for k, v in e.items() if not k.startswith("_")}
            for e in sorted(errata[i], key=lambda e: e["_at"])
        ]
        for i in sorted(errata)
    }
    with open(ed_dir / "printed_errata.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    lines = [
        "# 1630 printed Errata: to review",
        "",
        "Written by `scripts/errata_1630.py`. Settle an item with an entry in",
        "`errata_1630_overrides.json` (see the script's docstring), then rerun it.",
        "",
        f"{sum(map(len, out.values()))} errata on {len(out)} eulogies.",
    ]
    for k, v in review.items():
        lines += ["", f"## {k} ({len(v)})", ""] + [f"- {x}" for x in v]
    REVIEW.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{sum(map(len, out.values()))} errata on {len(out)} eulogies; "
        + ", ".join(f"{len(v)} {k}" for k, v in review.items())
    )


def aligned_text(aligned, eid):
    mo = int(eid[3:5])
    for d in aligned[mo].values():
        if eid in d["elogia"]:
            return d["elogia"][eid]
    return ""


if __name__ == "__main__":
    main()
