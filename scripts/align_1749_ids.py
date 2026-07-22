#!/usr/bin/env python3
"""Align the digitized 1749 elogia to CRMEDR canonical IDs and coin deprecated
IDs (in nominative lemma form) for elogia with no counterpart in the editio
altera 2004.

Pipeline (v2 — see docs and the CRMEDR canonicalization report):
  0. merge OCR continuation fragments (a paragraph after one without final
     punctuation continues the same elogium);
  1. same-day matching: slug-stem hits on capitalized tokens + text similarity
     + token-Jaccard against the 2004 Latin text; accepted on strong name
     evidence OR (text similarity AND Jaccard); greedy one-to-one per day;
  2. cross-day matching for the reform's repositioned saints: name-zone stems
     (capitalized tokens after a sanctity marker), Jaccard >= 0.15, text
     similarity >= 0.40, uniqueness margin;
  3. coining: subject extraction (persons -> nominative lemma via an empirical
     genitive->nominative dictionary mined from the current registry slugs
     paired with their 2004 texts, plus declension rules; feast leads take the
     feast phrase; anonymous groups take number+class+place per registry rule
     8); collision-deduplicated; each coined entry carries subject_la.

Outputs: rewritten MM.json (elogia as ID-keyed objects), alignment.json, and
deprecated_ids.json (commit into crmedr/data/).

NOTE: step 3's coining under-extracts named martyrs when a genitive class-word
(Martyrum, fratrum) sits between the sanctity marker and the names, mis-slugging
multi-martyr eulogies with the place name. `remediate_group_slugs.py` corrects that
in a post pass (2026-07-20); its improved extraction should be folded back here.

Requires the PRIVATE CatholicOS/martyrology-texts repository; without it the
matcher degrades to stem evidence and cross-day matching is disabled.

Usage:
  python3 align_1749_ids.py /path/to/crmedr /path/to/martyrology-texts [repo_root]
"""

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

CRMEDR = Path(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)
TEXTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None
ROOT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(__file__).resolve().parent.parent


def fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower().replace("æ", "e").replace("œ", "e").replace("j", "i"))


def foldslug(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", "-", s.lower().replace("æ", "e").replace("œ", "e")).strip("-")


lemma = {}  # mined below from the registry + 2004 texts


def _mine_lemma_map(current, texts):
    SLUGSTOP = {"et", "de", "a", "ab", "in", "cum", "socii"}
    pairs = defaultdict(Counter)
    for e in current:
        t = texts.get(e["id"])
        if not t:
            continue
        slugtoks = [
            p
            for p in e["id"].split("-", 1)[1].split("-")
            if len(p) >= 4 and p not in SLUGSTOP and not p.isdigit()
        ]
        ttoks = fold(t).split()
        for st in slugtoks:
            best_tt, best_cp = None, 0
            for tt in ttoks:
                cp = 0
                for x, y in zip(st, tt, strict=False):
                    if x == y:
                        cp += 1
                    else:
                        break
                if cp > best_cp:
                    best_cp, best_tt = cp, tt
            if best_tt and best_cp >= min(5, len(st) - 1) and best_tt != st:
                pairs[best_tt][st] += 1
    out = {}
    for form, cnt in pairs.items():
        best, n = cnt.most_common(1)[0]
        if len(cnt) == 1 or n >= 2 * cnt.most_common(2)[1][1]:
            out[form] = best
    return out


def lemmatize(tok):
    if tok in lemma:
        return lemma[tok]
    if tok.endswith("ae"):
        return tok[:-2] + "a"
    if tok.endswith("ii"):
        return tok[:-2] + "ius"
    if tok.endswith("onis"):
        return tok[:-4] + "o"
    if tok.endswith("icis"):
        return tok[:-4] + "ix"
    if tok.endswith("ntis"):
        return tok[:-4] + "ns"
    if tok.endswith("i") and len(tok) > 4:
        return tok[:-1] + "us"
    return tok


ED_DIR = ROOT / "data" / "editions" / "martyrologium_romanum_1749"
ed = {}
for _mon in range(1, 13):
    _month = json.load(open(ED_DIR / f"{_mon:02d}.json", encoding="utf-8"))
    for _d, _v in _month.items():
        if not isinstance(_v["elogia"], list):
            sys.exit("elogia already aligned; re-run digitize_1749.py first")
        ed[f"{_mon}-{_d}"] = _v

# ---- STEP 1: merge continuation fragments (previous elogium lacks final '.')
merged_count = 0
for day in ed.values():
    els = day["elogia"]
    out = []
    for el in els:
        if out and not out[-1].rstrip().endswith((".", "!", "?")):
            out[-1] = out[-1].rstrip() + " " + el
            merged_count += 1
        else:
            out.append(el)
    day["elogia"] = out
print(
    "fragments merged:", merged_count, "| elogia now:", sum(len(v["elogia"]) for v in ed.values())
)

# ---- shared text utilities
LATSTOP = {
    "sancti",
    "sanctae",
    "sancta",
    "sanctus",
    "sanctorum",
    "sanctarum",
    "beati",
    "beatae",
    "beatorum",
    "martyris",
    "martyrum",
    "episcopi",
    "confessoris",
    "confessorum",
    "virginis",
    "virginum",
    "presbyteri",
    "abbatis",
    "diaconi",
    "papae",
    "regis",
    "monachi",
    "item",
    "eodem",
    "ipso",
    "apud",
    "natalis",
    "passio",
    "depositio",
    "commemoratio",
    "die",
    "qui",
    "quae",
    "cuius",
    "eius",
    "cum",
    "sub",
    "post",
    "anno",
    "tempore",
    "temporibus",
    "imperatore",
    "imperatoris",
    "ecclesiae",
    "domini",
    "nostri",
    "christi",
    "deo",
    "deum",
}


def sig_tokens(folded_text):
    return {t[:6] for t in folded_text.split() if len(t) >= 5 and t not in LATSTOP}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


reg = json.load(open(CRMEDR / "data" / "martyrology_ids.json", encoding="utf-8"))
current = [e for e in reg["entries"] if not e.get("deprecated")]
texts = {}
for m in range(1, 13):
    if TEXTS:
        texts.update(
            json.load(
                open(
                    TEXTS / "data" / "editions" / "martyrologium_romanum_2004" / f"{m:02d}.json",
                    encoding="utf-8",
                )
            )
        )
lemma.update(_mine_lemma_map(current, texts))
la2004f = {k: fold(v)[:300] for k, v in texts.items()}
la2004sig = {k: sig_tokens(fold(v)) for k, v in texts.items()}

SLUGSTOP = {"et", "de", "a", "ab", "in", "cum", "socii"}


def slug_stems(mrid):
    parts = mrid.split("-", 1)[1].split("-") if "-" in mrid else []
    return [p[:6] for p in parts if p not in SLUGSTOP and not p.isdigit() and len(p) >= 4]


def stem_hit(stem, toks, exact=False):
    for t in toks:
        if t.startswith(stem):
            return 1.0
        if not exact and len(t) >= 4 and SequenceMatcher(None, stem, t[: len(stem)]).ratio() >= 0.8:
            return 0.8
    return 0.0


def cap_tokens(raw):
    raw2 = re.sub(r"\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b", r"\1\2", raw)
    return [fold(t).strip() for t in re.findall(r"[A-Z][a-zA-Z]{2,}", raw2)]


MARKER = re.compile(r"(sanct[iaeous]+r?u?m?|beat[iaeous]+r?u?m?)\s+", re.I)
RANK6 = {
    "martyr",
    "episco",
    "confes",
    "virgin",
    "presby",
    "abbati",
    "diacon",
    "papae",
    "regis",
    "ducis",
    "monach",
    "militu",
    "matron",
    "vidua",
    "sacerd",
    "item",
    "eodem",
    "levita",
    "pontif",
    "anacho",
    "eremit",
}


def name_zone(raw):
    raw2 = re.sub(r"\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b", r"\1\2", raw)
    out = []
    for m in MARKER.finditer(raw2):
        for t in re.findall(r"[A-Z][a-zA-Z]+", raw2[m.end() : m.end() + 80]):
            f = fold(t).strip()
            if f and f[:6] not in RANK6:
                out.append(f)
    return out


by_day = {}
for e in current:
    by_day.setdefault((e["month"], e["day"]), []).append(e["id"])

folded, ctoks, nzone, sigs = {}, {}, {}, {}
for mm_dd, day in ed.items():
    for idx, el in enumerate(day["elogia"]):
        k = (mm_dd, idx)
        folded[k] = fold(el)
        ctoks[k] = cap_tokens(el)
        nzone[k] = name_zone(el)
        sigs[k] = sig_tokens(folded[k])


# ---- STEP 2: matching v2
def accept_sameday(ef, s_stem, s_text, jac):
    if ef.lstrip().startswith(("octava", "vigilia")):
        return s_stem >= 0.5 and (jac >= 0.15 or s_text >= 0.5)
    return s_stem >= 0.65 or (s_text >= 0.42 and jac >= 0.15)


assign, claimed = {}, set()
for mm_dd, day in ed.items():
    m, d = map(int, mm_dd.split("-"))
    cands = by_day.get((m, d), [])
    pairs = []
    for idx in range(len(day["elogia"])):
        k = (mm_dd, idx)
        for c in cands:
            stems = slug_stems(c)
            s_stem = sum(stem_hit(st, ctoks[k]) for st in stems) / len(stems) if stems else 0
            t = la2004f.get(c, "")
            s_text = SequenceMatcher(None, folded[k][:300], t).ratio()
            jac = jaccard(sigs[k], la2004sig.get(c, set()))
            if accept_sameday(folded[k], s_stem, s_text, jac):
                pairs.append((1.5 * s_stem + 1.3 * s_text + 0.8 * jac, idx, c))
    pairs.sort(reverse=True)
    ui, uc = set(), set()
    for tot, idx, c in pairs:
        if idx in ui or c in uc:
            continue
        assign[(mm_dd, idx)] = (c, round(tot, 2), "same-day")
        ui.add(idx)
        uc.add(c)
        claimed.add(c)
p1 = len(assign)

unclaimed = [e["id"] for e in current if e["id"] not in claimed]
uc_stems = {c: slug_stems(c) for c in unclaimed}
for mm_dd, day in ed.items():
    for idx in range(len(day["elogia"])):
        k = (mm_dd, idx)
        if k in assign or not nzone[k] or folded[k].lstrip().startswith(("octava", "vigilia")):
            continue
        best = []
        for c in unclaimed:
            if c in claimed:
                continue
            stems = uc_stems[c]
            if not stems:
                continue
            s_stem = sum(stem_hit(st, nzone[k]) for st in stems) / len(stems)
            if s_stem < 0.75:
                continue
            jac = jaccard(sigs[k], la2004sig.get(c, set()))
            if jac < 0.15:
                continue
            s_text = SequenceMatcher(None, folded[k][:300], la2004f.get(c, "")).ratio()
            if s_text < 0.40:
                continue
            best.append((1.5 * s_stem + 1.3 * s_text + 0.8 * jac, c))
        best.sort(reverse=True)
        if best and (len(best) == 1 or best[0][0] - best[1][0] >= 0.3):
            assign[k] = (best[0][1], round(best[0][0], 2), "cross-day")
            claimed.add(best[0][1])
print("pass1:", p1, "| pass2:", len(assign) - p1, "| total matched:", len(assign))

# ---- STEP 3: subject extraction + coining v2
HONOR = {
    "sancti": ("Sanctus", False),
    "sancta": ("Sancta", False),
    "sanctae": ("Sancta", False),
    "sanctus": ("Sanctus", False),
    "sanctorum": ("Sancti", True),
    "sanctarum": ("Sanctae", True),
    "beati": ("Beatus", False),
    "beatae": ("Beata", False),
    "beatorum": ("Beati", True),
}
RANKWORDS = {
    "Martyris",
    "Martyrum",
    "Episcopi",
    "Confessoris",
    "Confessorum",
    "Virginis",
    "Virginum",
    "Presbyteri",
    "Abbatis",
    "Abbatum",
    "Diaconi",
    "Papae",
    "Regis",
    "Reginae",
    "Ducis",
    "Monachi",
    "Monachorum",
    "Militum",
    "Viduae",
    "Sacerdotis",
    "Levitae",
    "Pontificis",
    "Anachoretae",
    "Eremitae",
    "Imperatoris",
    "Comitis",
    "Matris",
    "Item",
    "Eodem",
    "Ipso",
    "Apud",
    "Sancti",
    "Sanctae",
    "Sanctorum",
    "Beati",
    "Beatae",
    "Ordinis",
}
CONNECT = {"et", "de", "a"}
NUMWORDS = {
    "duorum": "duo",
    "trium": "tres",
    "quatuor": "quattuor",
    "quattuor": "quattuor",
    "quinque": "quinque",
    "sex": "sex",
    "septem": "septem",
    "octo": "octo",
    "novem": "novem",
    "decem": "decem",
    "viginti": "viginti",
    "triginta": "triginta",
    "quadraginta": "quadraginta",
    "quinquaginta": "quinquaginta",
    "sexaginta": "sexaginta",
    "centum": "centum",
    "plurimorum": "plurimi",
    "multorum": "multi",
    "omnium": "omnes",
}
GROUP = {
    "martyrum": "martyres",
    "militum": "milites",
    "monachorum": "monachi",
    "virginum": "virgines",
    "fratrum": "fratres",
    "confessorum": "confessores",
    "presbyterorum": "presbyteri",
    "sanctorum": "sancti",
}


def extract_subject(raw):
    """Returns (slug, subject_la) drafts for an unmatched 1749 elogium."""
    raw2 = re.sub(r"\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b", r"\1\2", raw)
    lead = raw2.lstrip()
    # feast leads
    fm = re.match(
        r"(Circumcisio|Octava|Vigilia|Purificatio|Annuntiatio|Assumptio|Nativitas|Conceptio"
        r"|Exaltatio|Inventio|Dedicatio|Translatio|Apparitio|Conversio|Commemoratio omnium"
        r"|Festum|Festivitas)\b",
        lead,
    )
    if fm:
        words = re.findall(r"[A-Za-z]+", lead)
        keep = []
        for w in words:
            lw = w.lower()
            if lw in ("nostri", "eiusdem", "ejusdem", "quae", "qui", "cuius"):
                break
            keep.append(w)
            if len(keep) >= 4:
                break
        # trim trailing connectors
        while keep and keep[-1].lower() in CONNECT:
            keep.pop()
        slug = "-".join(foldslug(w) for w in keep[:4])
        subject = " ".join(keep[:4])
        return slug, subject
    m = MARKER.search(raw2)
    if m:
        marker = m.group(1).lower()
        hon, plural = HONOR.get(marker, ("Sanctus", False))
        tail = raw2[m.end() : m.end() + 100]
        toks = re.findall(r"[A-Za-z]+", tail)
        names = []
        for t in toks:
            if t in RANKWORDS:
                break
            if t[0].isupper():
                names.append(t)
            elif t.lower() in CONNECT and names:
                names.append(t.lower())
            else:
                break
            if len([n for n in names if n[0].isupper()]) >= 3:
                break
        while names and names[-1] in CONNECT:
            names.pop()
        if names and any(n[0].isupper() for n in names):
            lems = []
            for n in names:
                if n in CONNECT:
                    lems.append(n)
                else:
                    lems.append(lemmatize(foldslug(n)))
            slug = "-".join(foldslug(x) for x in lems)
            subject = hon + " " + " ".join(x if x in CONNECT else x.capitalize() for x in lems)
            return slug, subject
        # anonymous group: number + class + place
        gtoks = fold(tail).split()
        num = next((NUMWORDS[t] for t in gtoks[:6] if t in NUMWORDS), None)
        grp = next((GROUP[t] for t in gtoks[:6] if t in GROUP), "martyres")
        pm = re.match(r"\s*(?:Item\s+)?(?:Apud|In|Ad)?\s*([A-Z][a-zA-Z]+)", raw2)
        place = foldslug(pm.group(1)) if pm else None
        parts = [p for p in (num, grp, place) if p]
        slug = "-".join(parts)
        subject = ("Sancti " if not marker.startswith("beat") else "Beati ") + " ".join(
            p.capitalize() for p in parts[:2]
        )
        return slug, subject
    words = re.findall(r"[A-Za-z]+", lead)[:3]
    return "-".join(foldslug(w) for w in words), " ".join(words)


existing_ids = {e["id"] for e in reg["entries"] if not e.get("deprecated")}
coined, dep_entries, sidecar = set(), [], {}
aligned = {}
for mm_dd in sorted(ed, key=lambda k: tuple(map(int, k.split("-")))):
    day = ed[mm_dd]
    m, d = map(int, mm_dd.split("-"))
    obj = {}
    for idx, el in enumerate(day["elogia"]):
        k = (mm_dd, idx)
        if k in assign:
            cid, sc, method = assign[k]
            sidecar[cid] = {"method": method, "score": sc}
        else:
            slug, subject = extract_subject(el)
            slug = slug or "sine-nomine"
            cid = f"mr:{m:02d}{d:02d}-{slug}"
            if cid in existing_ids or cid in coined:
                pm = re.match(r"\s*(?:Apud|In|Ad)?\s*([A-Z][a-zA-Z]+)", el)
                cid2 = f"{cid}-{foldslug(pm.group(1)) if pm else 'loco'}"
                n = 2
                while cid2 in existing_ids or cid2 in coined:
                    cid2 = f"{cid}-{n}"
                    n += 1
                cid = cid2
            coined.add(cid)
            dep_entries.append(
                {
                    "id": cid,
                    "month": m,
                    "day": d,
                    "entry": idx + 1,
                    "deprecated": True,
                    "attested_in": "martyrologium_romanum_1749",
                    "subject_la": subject,
                }
            )
            sidecar[cid] = {"method": "coined-deprecated", "score": None}
        obj[cid] = el
    aligned[mm_dd] = {
        "titulus": day["titulus"],
        "elogia": obj,
        **({"conclusio": day["conclusio"]} if "conclusio" in day else {}),
    }
print("coined deprecated:", len(dep_entries))
for _mon in range(1, 13):
    _month = {k.split("-")[1]: v for k, v in aligned.items() if int(k.split("-")[0]) == _mon}
    with open(ED_DIR / f"{_mon:02d}.json", "w", encoding="utf-8") as f:
        json.dump(_month, f, ensure_ascii=False, indent=1)
        f.write("\n")
with open(ED_DIR / "alignment.json", "w", encoding="utf-8") as f:
    json.dump(
        {
            "$comment": "Per-ID alignment provenance: same-day / cross-day (matched to a "
            "current CRMEDR ID) or coined-deprecated (coined with deprecated:true in the "
            "CRMEDR). Scores are matcher totals; all draft.",
            "ids": sidecar,
        },
        f,
        ensure_ascii=False,
        indent=1,
    )
    f.write("\n")
with open(ROOT / "deprecated_ids.json", "w", encoding="utf-8") as f:
    json.dump(dep_entries, f, ensure_ascii=False, indent=1)
    f.write("\n")
print("deprecated_ids.json written to repo root; commit into crmedr/data/")
