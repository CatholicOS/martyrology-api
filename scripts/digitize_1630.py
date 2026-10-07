#!/usr/bin/env python3
"""Digitize the 1630 (Urban VIII) edition of the Martyrologium Romanum, printed in
Rome by the Vatican press with Baronius's Notationes, from a proofread TEI
transcription into day-keyed monthly JSON (unaligned: `elogia` are arrays).

Output: data/editions/martyrologium_romanum_1630/MM.json
Then run align_1630_ids.py to key the eulogies by canonical ID.

The TEI (one <pb/> per page of the scan, Martyrology on pp. 39-688) was
transcribed from the public-domain scan and proofread against the page images
twice. Its orthography is normalized only for the long s; u/v, i/j, æ/œ, accents
and abbreviation tildes are as printed.

Each day is cut from its heading (`Pridie Nonas Augusti. Luna.`, dated by
`roman_date`) to the start of Baronius's notes; the day's continuous text is
split into eulogies at sentence boundaries where a new eulogy begins (a place
or opener followed by a saint marker), checked against the 1749 edition's
eulogies of the same day. Baronius's reference letters (a, b, c ... pointing to
his notes) are removed, guided by the lemma of each note.

Usage:
  pip install lxml
  python3 digitize_1630.py data/sources/martyrologium_romanum_1630.tei.xml [repo_root]

Baronius's notes and the margin notes are extracted from the same walk by
notationes_1630.py (footnotes.json, marginalia.json).
"""

import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

import lxml.etree as etree

ROOT = Path(__file__).resolve().parent.parent
NS = "{http://www.tei-c.org/ns/1.0}"
FIRST_PAGE, LAST_PAGE = 39, 688  # the Martyrology (with notes) in the scan
# The scan carries printed pp. 430-443 twice (PDF pp. 468-481 and 482-495).
DUPLICATE_PAGES = range(482, 496)

# ---- day headings: Roman dates
MONTHS = [
    "Ianuarij",
    "Februarij",
    "Martij",
    "Aprilis",
    "Maij",
    "Iunij",
    "Iulij",
    "Augusti",
    "Septembris",
    "Octobris",
    "Nouembris",
    "Decembris",
]
MLEN = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
NONES = {3: 7, 5: 7, 7: 7, 10: 7}  # Nones fall on the 7th in March, May, July, October
ORD = {
    "pridie": 2,
    "tertio": 3,
    "quarto": 4,
    "quinto": 5,
    "sexto": 6,
    "septimo": 7,
    "octauo": 8,
    "nono": 9,
    "decimo": 10,
    "vndecimo": 11,
    "undecimo": 11,
    "duodecimo": 12,
    "decimotertio": 13,
    "decimoquarto": 14,
    "decimoquinto": 15,
    "decimosexto": 16,
    "decimoseptimo": 17,
    "decimooctauo": 18,
    "decimonono": 19,
    "tertiodecimo": 13,
    "quartodecimo": 14,
    "quintodecimo": 15,
    "sextodecimo": 16,
    "septimodecimo": 17,
    "octauodecimo": 18,
    "nonodecimo": 19,
}


def norm(t):
    return re.sub(r"\s+", " ", t).strip()


def strip_acc(t):
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def roman_date(t):
    """'Pridie Nonas Augusti. Luna.' -> (8, 4); None if not a day heading."""
    s = re.sub(r"[^a-z ]", " ", t.lower().replace("j", "i").replace("v", "u"))
    # 'septimo'/'octauo' must not be read as September/October
    s_m = re.sub(r"\b\w*(?:septimo|octauo)\w*\b", " ", s)
    mi = None
    for i, m in enumerate(MONTHS):
        if re.search(r"\b" + m.lower().replace("j", "i").replace("v", "u")[:4], s_m):
            mi = i + 1
    if mi is None:
        return None
    if re.search(r"\bkalendis\b", s) and not re.search(r"\bkal", s.replace("kalendis", "")):
        return (mi, 1)
    if re.search(r"\bnonis\b", s):
        return (mi, NONES.get(mi, 5))
    if re.search(r"\bidibus\b", s):
        return (mi, NONES.get(mi, 5) + 8)
    best = None  # longest ordinal wins (decimoquarto over quarto)
    for k, v in ORD.items():
        if re.search(r"\b" + k, s) and (best is None or len(k) > len(best[0])):
            best = (k, v)
    if not best:
        return None
    n = best[1]
    if "kal" in s:  # n days before the Kalends: the previous month
        pm = mi - 1 or 12
        return (2, 30 - n) if pm == 2 else (pm, MLEN[pm - 1] + 2 - n)
    if re.search(r"\bnon", s):
        return (mi, NONES.get(mi, 5) + 1 - n)
    if re.search(r"\bid", s):
        return (mi, NONES.get(mi, 5) + 9 - n)
    return None


# ---- walking the TEI
BLOCK = ("p", "head", "note", "fw", "table", "list", "item", "row", "cell", "l", "lg")
ROW_TOKEN = r"(?:[A-Za-z*]{1,2}|\d{1,2}(?:-\d{1,2})?|[xvij]+|\.)\.?"  # lunar-table cells
LEMMA = re.compile(r"^\s*(?:([a-z])\s+(.{1,90}?)|(.{1,90}?)\s+([a-z])\s*[.,]?\s*)\]")
GENERIC = re.compile(
    r"^(?:sanct|beat|episc|epis|mart|confess|uirg|abb|presb|diac|plurim|item|natal|passio"
    r"|depos|transl|comm|socio|socia|alias|papa|imp|regis|mon$)"
)


def own_text(el):
    """Text of an element and its inline children, without nested blocks."""
    parts = [el.text or ""]
    for c in el:
        tag = c.tag.replace(NS, "") if isinstance(c.tag, str) else ""
        parts.append(" " if tag in BLOCK else own_text(c))
        parts.append(c.tail or "")
    return norm("".join(parts))


def strip_rows(t):
    """Drop a leading run of lunar-table tokens glued to a paragraph."""
    w, i = t.split(), 0
    while (
        i < len(w)
        and re.fullmatch(ROW_TOKEN, w[i])
        and not (
            i + 1 < len(w)
            and re.fullmatch(r"[a-zæœ]{3,}.*", w[i + 1])
            and w[i][0].isupper()
            and len(w[i]) > 1
        )
    ):
        i += 1
    return " ".join(w[i:]) if i >= 8 else t


def _f(s):
    s = s.replace("æ", "ae").replace("Æ", "AE").replace("œ", "oe").replace("Œ", "OE")
    s = strip_acc(s.replace("ę", "e"))
    return re.sub(r"[^a-z ]", " ", s.lower().replace("j", "i").replace("v", "u"))


def add_note(day, t):
    """Record a note's reference letter and lemma ('d Almachij mart.]')."""
    m = LEMMA.match(t)
    if m:
        day["notes"].append(
            {"letter": m.group(1) or m.group(4), "lemma": (m.group(2) or m.group(3)).strip(" .,")}
        )


def lemma_in(note, daytext):
    """Does a note's distinctive lemma word occur in this day's text?"""
    m = LEMMA.match(note)
    if not m:
        return False
    words = [
        w for w in _f(m.group(2) or m.group(3)).split() if len(w) >= 3 and not GENERIC.match(w)
    ]
    return bool(words) and re.search(r"\b" + re.escape(words[0][:5]), _f(daytext)) is not None


def extract_days(tei):
    """Each day's heading, text paragraphs and note letters+lemmas. `blocks` keeps,
    in reading order, the day's text paragraphs ("text"), note paragraphs ("note",
    "foot" for the foot of the page, "verse") and margin notes ("margin" beside the
    notes, "margin-text" beside the eulogies) as (kind, text, page), for
    notationes_1630.py."""
    body = etree.parse(str(tei)).getroot().find(f".//{NS}body")
    days, cur, state, page = [], None, None, None

    def block(day, kind, t):
        if t:
            day["blocks"].append((kind, t, page))

    for el in body.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.replace(NS, "")
        if tag == "pb":
            page = int(el.get("n"))
            continue
        if page is None or not FIRST_PAGE <= page <= LAST_PAGE or page in DUPLICATE_PAGES:
            continue
        if tag == "head":
            t = own_text(el)
            rd = roman_date(strip_acc(t)) if re.search(r"Kal|kal|Non|Idus|Idib|Pridie", t) else None
            if rd:
                cur = {"month": rd[0], "day": rd[1], "titulus": t, "paras": [], "notes": []}
                cur["blocks"] = []
                days.append(cur)
                state = "day"
            elif state == "day" and cur["paras"]:
                state = "notes"  # e.g. "AVGVSTI 4." opens the day's notes
            elif state == "notes" and LEMMA.match(t):  # a lemma set as a heading
                add_note(cur, t)
                block(cur, "note", t)
            continue
        if tag == "note" and el.get("type") == "footnote" and cur is not None:
            # notes printed at the foot of the page: this day's, or the previous day's tail
            t = own_text(el)
            if state == "notes":
                add_note(cur, t)
                block(cur, "foot", t)
            elif state == "day" and cur["paras"]:
                if lemma_in(t, " ".join(cur["paras"])):
                    state = "notes"
                    add_note(cur, t)
                    block(cur, "foot", t)
                elif len(days) > 1:
                    add_note(days[-2], t)
                    block(days[-2], "foot", t)
            elif len(days) > 1:
                block(days[-2], "foot", t)
            continue
        if tag == "note" and el.get("place") == "margin" and cur is not None:
            t = own_text(el)
            if state == "notes" and LEMMA.match(t):  # a note the transcription set in the margin
                add_note(cur, t)
                block(cur, "note", t)
            else:
                block(cur, "margin" if state == "notes" else "margin-text", t)
            continue
        if tag == "l" and state == "notes":
            block(cur, "verse", own_text(el))
            continue
        if tag == "item" and state == "notes":  # notes the transcription set as a list
            add_note(cur, own_text(el))
            block(cur, "note", own_text(el))
            continue
        if tag != "p":
            continue
        if state == "notes":
            add_note(cur, own_text(el))
            block(cur, "note", own_text(el))
            continue
        if state != "day":
            continue
        t = strip_rows(own_text(el))
        if not t:
            continue
        if (
            re.fullmatch(r"(?:[A-Z]\s){3,}[A-Z]?\s*\d+\.?", t)
            or re.fullmatch(r"[A-Z ]{6,}\s+\d+\.?", t)
            or re.fullmatch(r"[A-Z]{4,}-", t)
        ):
            state = "notes"  # a month heading ("I V L I I 10.", or broken: "DECEM-")
            continue
        if all(re.fullmatch(ROW_TOKEN, w) for w in t.split()):
            continue  # a lunar-table row
        if re.search(r"\]", t[:160]):  # a note lemma: 'Circumcisio.]'
            state = "notes"
            add_note(cur, t)
            block(cur, "note", t)
            continue
        cur["paras"].append(t)
        block(cur, "text", t)
    return days


# ---- eulogy segmentation
ABBR = re.compile(
    r"(?:\b(?:S|SS|B|BB|D|N|Ep|Episc|Ap|Imp|Pp|Sanct|Mart|Conf|Pont|Max|Rom|Ss|M|cap|c|lib"
    r"|n|tom|vid|ibid|al|alias|Kal|Kalend|Kalen|kal|kalend|kalen|Non|Id|Ian|Febr|Apr|April"
    r"|Iun|Iul|Aug|Sept|Septemb|Octob|Nouemb|Decemb|Ianuar|Febru)|\b[A-Z]|\b[IVXL]{2,})\.$"
)
CONT = re.compile(
    r"^(?:Qui|Quæ|Quae|Quod|Quorum|Quarum|Cuius|Cujus|Huius|Hujus|Eius|Ejus|Hic|Hæc|Hi|Hoc"
    r"|Vbi|Ubi|Inde|Postea|Tandem|Deinde|Denique|Cumque|Et|Sed|Ad|Horum|Harum|Eorum|Earum"
    r"|Ipse|Ipsa|Is|Ea|Hunc|Hanc|Quem|Quam|Quos|Cui|Quibus|Cum|Tum|Ibique|Vnde|Unde|Nam"
    r"|Atque|Porro|Interim|Mox|Iterum|Demum|Postremo|Ac|Igitur|Sicque|Itaque|Qua|Quo)\b"
)
OPENER = re.compile(
    r"^(?:Litaniæ|Litaniae|Item|Eodem|Eôdem|Ibidem|Ibîdem|Ipso|Ipsa|Apud|In|Ad|Iuxta|Prope"
    r"|Inter|Sub|Natalis|Natale|Passio|Depositio|Translatio|Commemoratio|Inuentio|Inventio"
    r"|Dedicatio|Ordinatio|Vigilia|Octaua|Octava|Elevatio|Eleuatio|Sanctorum|Sanctarum"
    r"|Sancti|Sanctæ|Beati|Beatæ|S\.|SS\.|B\.)\b",
    re.I,
)
# a saint marker in the genitive (or a feast word) near the start of a sentence
GENMARK = re.compile(
    r"(?<![\w])(?:s[aã]n?ct(?:i|æ|ę|e|orum|orũ|arum|arũ)|b[eę]at(?:i|æ|ę|orum|orũ|arum|arũ)"
    r"|(?-i:SS?\.|BB?\.|S(?=\s?[A-Z]))|natal(?:is|e)|passio|depositio|translatio|trãslatio"
    r"|c[oõ]m+emoratio|cõmemoratio|dedicatio|inuentio|ordinatio)(?=[\s,;:.A-Z]|$)",
    re.I,
)
RANK = r"(?:Papæ|Papę|Episcopi|Episc\.|martyris|confessoris|virginis|abbatis|presbyteri)"


def fold(s):
    s = unicodedata.normalize(
        "NFKD", s.replace("æ", "ae").replace("Æ", "Ae").replace("œ", "oe").replace("Œ", "Oe")
    )
    s = "".join(c for c in s.replace("ę", "e") if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z ]+", " ", s.replace("j", "i").replace("v", "u"))


def sentences(t):
    out, cur = [], ""
    for piece in re.split(r"(?<=[.!?])\s+", t):
        cur = (cur + " " + piece).strip() if cur else piece
        if ABBR.search(cur):  # ends with an abbreviation: the sentence goes on
            continue
        out.append(cur)
        cur = ""
    if cur:
        out.append(cur)
    return out


def starts_eulogy(s):
    if not re.match(r"[A-ZÆŒ¶]", s) or CONT.match(s):
        return False
    if OPENER.match(s) or GENMARK.search(" ".join(s.split()[:10])):
        return True
    # place + name + rank without a saint marker: "Romæ Vitaliani Papæ."
    return bool(re.match(r"^[A-ZÆ][^\s,]+\s+(?:in\s+\S+\s+)?[A-Z][a-zæœ]+\s+" + RANK, s))


def rough_segments(t):
    segs = []
    for s in sentences(t):
        if segs and not starts_eulogy(s):
            segs[-1] += " " + s
        else:
            segs.append(s)
    return segs


def _ratio(a, b, n=None):
    fa, fb = (fold(a)[:n], fold(b)[:n]) if n else (fold(a), fold(b))
    return difflib.SequenceMatcher(None, fa, fb, autojunk=False).ratio()


def refine(segs, ref):
    """Merge a segment into the previous one when both best-match the same 1749
    eulogy and the merged text matches it at least as well."""
    if not ref:
        return segs
    out = []
    for s in segs:
        if out:
            bp, ip = max(((_ratio(out[-1], r, 220), i) for i, r in enumerate(ref)), default=(0, -1))
            _bs, is_ = max(((_ratio(s, r, 220), i) for i, r in enumerate(ref)), default=(0, -1))
            if ip == is_ and ip >= 0 and bp > 0.45:
                merged = out[-1] + " " + s
                if _ratio(merged, ref[ip]) >= _ratio(out[-1], ref[ip]):
                    out[-1] = merged
                    continue
        out.append(s)
    return out


# ---- Baronius's reference letters
ALPHA = "abcdefghiklmnopqrstuxyz"
LEMMA_GENERIC = re.compile(
    r"^(?:sanct|beat|episc|epis|mart|confess|uirg|abb|presb|diac|plurim|item|natal|passio|depos"
    r"|transl|comm|socio|socia|alias|papa|imp|regis|et|de|in|sub|cum|ac)"
)
SEP = "⁣"  # invisible separator: keeps eulogy boundaries through marker stripping
MARK = "\ue000"  # private use: where a removed letter stood (MARK + letter), for the notes


def _w(s):
    return re.sub(r"[^a-z ]", "", _f(s)).replace(" ", "")


def strip_markers(text, notes, mark=False):
    """Remove the reference letters that the day's notes point at: a letter directly
    after (or just before) a word of its note's lemma; then letters of the sequence
    lying between their anchored neighbours. With `mark`, MARK + letter is left in
    the letter's place, glued to the word before it."""
    toks = re.findall(r"\S+|\s+", text)
    words = [(i, t) for i, t in enumerate(toks) if t.strip()]
    drop = set()
    for n in notes:
        lem = [_w(w) for w in re.findall(r"[^\s,.&]+", n["lemma"])]
        keys = [w for w in lem if len(w) >= 3 and not LEMMA_GENERIC.match(w)]
        keys = keys or [w for w in lem if len(w) >= 3]
        if not keys:
            continue
        found = False
        for wi, (_i, t) in enumerate(words):
            if _w(t)[:5] != keys[0][:5]:
                continue
            for j, u in words[wi + 1 : wi + 5] + words[max(0, wi - 2) : wi][::-1]:
                m = re.fullmatch(r"([a-z])([,.;:]?)", u)
                if m and m.group(1) == n["letter"] and j not in drop:
                    drop.add(j)
                    found = True
                    break
            if found:
                break
        if not found:
            # a letter the transcription glued to its word ('Ioannisb'): removed only
            # where the rest of the word is exactly a word of the note's lemma
            for i, t in enumerate(toks):
                m = re.fullmatch(r"(.*\w)([a-z])([,.;:]?)", t)
                stem = _w(m.group(1)).replace("ae", "e") if m else ""
                if (
                    m
                    and m.group(2) == n["letter"]
                    and stem in {w.replace("ae", "e") for w in lem if w}
                ):
                    toks[i] = m.group(1) + (MARK + m.group(2) if mark else "") + m.group(3)
                    break
    pos = {re.fullmatch(r"([a-z])[,.;:]?", toks[j]).group(1): j for j in drop}
    if pos:
        top = max(ALPHA.find(c) for c in pos)
        for letter in [c for c in ALPHA[: top + 1] if c not in pos]:
            k = ALPHA.find(letter)
            lo = max([pos[c] for c in ALPHA[:k] if c in pos] or [-1])
            hi = min([pos[c] for c in ALPHA[k + 1 :] if c in pos] or [len(toks)])
            if lo < 0 and hi == len(toks):
                continue
            cand = [
                i
                for i, t in enumerate(toks)
                if lo < i < hi and re.fullmatch(letter + r"[,.;:]?", t)
            ]
            if len(cand) == 1:
                drop.add(cand[0])
                pos[letter] = cand[0]
    out = []
    for i, t in enumerate(toks):
        if i in drop:
            while out and not out[-1].strip():
                out.pop()
            m = re.fullmatch(r"([a-z])([,.;:]?)", t)
            out.append((MARK + m.group(1) if mark else "") + m.group(2))
            continue
        out.append(t)
    s = re.sub(r"\s+([,.;:])", r"\1", "".join(out))
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def clean_day(elogia, notes, mark=False):
    t = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", f" {SEP} ".join(elogia))  # "Prisci c,Crescentis"
    t = strip_markers(t, notes, mark=True)
    taken = set(re.findall(MARK + "([a-z])", t))  # letters already removed
    toks = re.findall(r"\S+|\s+", t)
    # leftover lone consonants are markers whose note was not captured (no Latin
    # word is a lone consonant); a/e only where bracketed by such neighbours, and
    # not when that letter was already found (then a lone a/e is the preposition)
    pos = {}
    for i, tok in enumerate(toks):
        m = re.fullmatch(r"([b-df-np-z])([,.;:]?)", tok)
        if m:
            pos.setdefault(m.group(1), i)
    for letter in "ae":
        if letter in taken:
            continue
        k = ALPHA.find(letter)
        lo = max([pos[c] for c in ALPHA[:k] if c in pos] or [-1])
        hi = min([pos[c] for c in ALPHA[k + 1 :] if c in pos] or [-1])
        if hi < 0:
            continue
        cand = [
            i
            for i, tok in enumerate(toks)
            if lo < i < hi and re.fullmatch(letter + r"[,.;:]?", tok)
        ]
        if len(cand) == 1:
            pos[letter] = cand[0]
    drop, out = set(pos.values()), []
    drop |= {i for i, tok in enumerate(toks) if re.fullmatch(r"[b-df-np-z][,.;:]?", tok)}
    for i, tok in enumerate(toks):
        if i in drop:
            while out and not out[-1].strip() and out[-1] != SEP:
                out.pop()
            out.append((MARK + tok[0] if mark else "") + tok[1:])
            continue
        out.append(tok)
    t = "".join(out)
    t = re.sub(r"\s*\[\*\]\s*", " ", t)
    t = re.sub(r"(?<=\s)\*(?=\s)", "", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"([,;:])(?=[^\s\d" + MARK + "])", r"\1 ", t)
    t = re.sub(r"&(?=\S)", "& ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    if not mark:
        t = re.sub(MARK + "[a-z]", "", t)
    return [e.strip() for e in t.split(SEP) if e.strip()]


TITULUS = re.compile(
    r"((?:[A-Z][a-zâêîôûæ]+\s+)?(?:[Kk]al\w*ñ?|Non\w*|Id\w*)\.?\s+[A-Za-z]+\.?)(?:\s+[a-z])?\s*(Luna)?"
)
ASTERISK_BREAK = re.compile(
    r"\s\*\s(?=(?:Romæ|Romę|Item|Eodem|Ibidem|Apud|In |[A-Z][a-zæœ]+ (?:sanct|S\.|SS\.|beat|B\.)))"
)


def load_reference(root=ROOT):
    ref = {}
    ref_dir = root / "data" / "editions" / "martyrologium_romanum_1749"
    for mo in range(1, 13):
        for d, v in json.load(open(ref_dir / f"{mo:02d}.json", encoding="utf-8")).items():
            el = v.get("elogia", {})
            ref[(mo, int(d))] = list(el.values() if isinstance(el, dict) else el)
    return ref


def digitize(tei, root=ROOT, mark=False):
    """The days walked from the TEI and the monthly data. With `mark`, each removed
    reference letter is left in the eulogies as MARK + letter (see strip_markers)."""
    ref = load_reference(root)
    days = extract_days(tei)
    assert len(days) == 365 and len({(d["month"], d["day"]) for d in days}) == 365, len(days)
    conclusio = None
    months = {}
    for x in days:
        t = re.sub(r"(\w)- (\w)", r"\1\2", " ".join(x["paras"]))  # words split across pages
        # a sentence end the transcription glued to the next ("damnauit.Bononiæ")
        t = re.sub(r"(?<=[a-zæœęũõ])\.(?=[A-ZÆ][a-zæœ])", ". ", t)
        m = re.search(r"¶?\s*Et alibi aliorum.*$", t)
        if m:
            conclusio = m.group(0).lstrip("¶ ").strip()
            t = t[: m.start()].strip()
        t = re.sub(r"\s*\[\*\]\s*(?=[A-Z])", " ¶¶ ", t)  # an asterisked eulogy begins
        t = ASTERISK_BREAK.sub(" ¶¶ ", t)
        segs = [s for chunk in t.split(" ¶¶ ") for s in rough_segments(chunk.strip())]
        segs = refine(segs, ref.get((x["month"], x["day"]), []))
        tm = TITULUS.search(re.sub(r"\b[a-z]\b", " ", x["titulus"]).replace("  ", " "))
        titulus = norm(tm.group(1) + " Luna." if tm else x["titulus"]).replace("..", ".")
        months.setdefault(x["month"], {})[str(x["day"])] = {
            "titulus": titulus,
            "elogia": clean_day(segs, x["notes"], mark),
        }
    # Printed once, on 1 January: "Sic semper terminatur lectio Martyrologij."
    for month in months.values():
        for day in month.values():
            day["conclusio"] = conclusio
    return days, months


def main():
    tei = Path(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT
    ed_dir = root / "data" / "editions" / "martyrologium_romanum_1630"
    days, months = digitize(tei, root)
    ed_dir.mkdir(parents=True, exist_ok=True)
    for mo, month in months.items():
        out = {d: month[d] for d in sorted(month, key=int)}
        with open(ed_dir / f"{mo:02d}.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
    n = sum(len(d["elogia"]) for m in months.values() for d in m.values())
    print(f"{len(days)} days, {n} elogia -> {ed_dir}")


if __name__ == "__main__":
    main()
