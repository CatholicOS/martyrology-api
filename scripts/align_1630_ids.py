#!/usr/bin/env python3
"""Align the digitized 1630 (Urban VIII) edition to CRMEDR canonical IDs.

Latin-to-Latin: each 1630 eulogy is compared with the IDs of the same day in the
CRMEDR registry (current and deprecated), through their texts in the 1749 edition
(data/editions/martyrologium_romanum_1749) and the 2004 Latin (martyrology-texts),
plus a name-stem check against the ID's slug. IDs attested only in the 1914
English have no Latin text and match by name alone. Gates: vigils and octaves only
match vigil/octave IDs, and a pope's printed ordinal (Papæ Secundi) must agree with
the ID's (-ii). Unmatched fragments without a saint marker are merged into the
eulogy before them. What is left is searched across the year (cross-day: a new ID
for this day, with the twin's slug, linked by same_eulogy) or settled by the
reviewed overrides in data/align_1630_overrides.json, keyed "M-D|<opening words>":
  "MERGE_PREV" | "DROP" | "RUBRIC" | "mr:<id>" | {"split_at": "<text>"}
  | {"coin": "<slug>", "subject_la": "...", "same_eulogy": [...], "note": "..."}

Input:  data/editions/martyrologium_romanum_1630/MM.json (from digitize_1630.py)
Output: the same files keyed by ID, alignment.json, and deprecated_ids_1630.json
        (the new deprecated IDs, to add to crmedr/data/deprecated_ids.json).

Usage:
  python3 align_1630_ids.py /path/to/crmedr /path/to/martyrology-texts [repo_root] [--write]
"""

import collections
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
WRITE = "--write" in sys.argv
CRMEDR = Path(args[0]) if args else sys.exit(__doc__)
TEXTS = Path(args[1]) if len(args) > 1 else sys.exit(__doc__)
ROOT = Path(args[2]) if len(args) > 2 else Path(__file__).resolve().parent.parent
EDITION = "martyrologium_romanum_1630"
ED_DIR = ROOT / "data" / "editions" / EDITION
OVERRIDES = json.load(
    open(Path(__file__).resolve().parent / "data" / "align_1630_overrides.json", encoding="utf-8")
)


def fold(s):
    s = (
        s.replace("æ", "ae")
        .replace("Æ", "Ae")
        .replace("œ", "oe")
        .replace("Œ", "Oe")
        .replace("ę", "e")
    )
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()
    s = s.replace("j", "i").replace("v", "u").replace("y", "i")
    s = s.replace("ph", "f").replace("th", "t").replace("ch", "c").replace("w", "uu")
    s = re.sub(r"([a-z])\1", r"\1", s)  # double letters
    return re.sub(r"[^a-z ]+", " ", s)


# ---- registry and comparison texts
REG = json.load(open(CRMEDR / "data" / "martyrology_ids.json", encoding="utf-8"))["entries"]
ENT = {e["id"]: e for e in REG}
LA = json.load(open(CRMEDR / "i18n" / "la.json", encoding="utf-8"))
BYDAY = collections.defaultdict(list)
for e in REG:
    BYDAY[(e["month"], e["day"])].append(e["id"])
TEXTS_BY_ID = collections.defaultdict(list)
for mo in range(1, 13):
    j = json.load(open(ROOT / "data/editions/martyrologium_romanum_1749" / f"{mo:02d}.json"))
    for v in j.values():
        for i, t in v["elogia"].items():
            TEXTS_BY_ID[i].append(fold(t))
    j = json.load(open(TEXTS / "data/editions/martyrologium_romanum_2004" / f"{mo:02d}.json"))
    for i, t in j.items():
        TEXTS_BY_ID[i].append(fold(t))

SLUGSTOP = {
    "et",
    "de",
    "a",
    "ab",
    "in",
    "cum",
    "socii",
    "sociae",
    "martyres",
    "milites",
    "uirgines",
    "episcopi",
}
ROMAN = re.compile(r"^(?:i|ii|iii|iu|u|ui|uii|uiii|ix|x|xi|xii|xiii)$")
ALIAS = {
    "xist": ["sixt"],
    "meginr": ["mein"],
    "ansgar": ["anschar", "anscar"],
    "brendan": ["brandan"],
    "ingenuin": ["genuin"],
    "alboin": ["albin"],
}


def stems(i):
    out = []
    for p in i.split("-", 1)[1].split("-"):
        f = fold(p).strip()
        if p in SLUGSTOP or ROMAN.match(f) or len(f) < 3:
            continue
        out.append(f[:4] if len(f) >= 5 else f)
    return out


def stem_alts(st):
    for k, v in ALIAS.items():
        if st and k.startswith(st[:4]):
            return [st] + [a[:4] for a in v]
    return [st]


def name_hit(i, f):
    toks, st = f.split(), stems(i)
    if not st:
        return 0

    def hit(s):
        for a in stem_alts(s):
            for t in toks:
                if (
                    t.startswith(a)
                    if len(a) >= 4
                    else t == a or (t.startswith(a) and len(t) <= len(a) + 3)
                ):
                    return True
        return False

    return sum(1 for s in st if hit(s)) / len(st)


def sim(a, b):
    return difflib.SequenceMatcher(None, a[:260], b[:260], autojunk=False).ratio()


def score(f, i):
    if not TEXTS_BY_ID.get(i):
        return 0.2, name_hit(i, f)  # attested only in a vernacular edition: match by name
    return max(sim(f, t) for t in TEXTS_BY_ID[i]), name_hit(i, f)


def accept_same(s, n):
    return s >= 0.55 or (n >= 0.99 and s >= 0.15) or (n >= 0.5 and s >= 0.42)


# ---- gates
PAPAL_ORD = {
    "primi": "i",
    "secundi": "ii",
    "tertii": "iii",
    "quarti": "iv",
    "quinti": "v",
    "sexti": "vi",
    "septimi": "vii",
    "octaui": "viii",
    "noni": "ix",
    "decimi": "x",
    "undecimi": "xi",
    "uudecimi": "xi",
    "duodecimi": "xii",
}


def kind(f):
    w = f.split()[:1]
    return (
        "vigilia" if w == ["uigilia"] else "octaua" if w and w[0] in ("octaua", "octaue") else None
    )


def id_kind(i):
    s = i.split("-", 1)[1]
    return "vigilia" if s.startswith("vigilia") else "octaua" if s.startswith("octava") else None


def gated(f, i):
    if kind(f) != id_kind(i):
        return False
    printed = None
    for pat in (r"\bpap(?:ae|e)\s+(\w+)", r"\b(\w+)\s+pap(?:ae|e)\b"):
        m = re.search(pat, f)
        printed = printed or (PAPAL_ORD.get(m.group(1)) if m else None)
    im = re.search(r"-(i{1,3}|iv|v|vi{1,3}|ix|x|xi{1,2})(?:-|$)", i)
    return not (printed and im and printed != im.group(1))


SAINT_MARK = re.compile(
    r"(?<![\w])(?:s[aã]n?ct(?:i|æ|ę|e|orum|orũ|arum|arũ)|b[eę]at(?:i|æ|ę|orum|orũ|arum|arũ)"
    r"|(?-i:SS?\.|BB?\.|S(?=\s?[A-Z]))|natal(?:is|e)|passio|depositio|translatio|trãslatio"
    r"|c[oõ]m+emoratio|cõmemoratio|dedicatio|inuentio|ordinatio)(?=[\s,;:.A-Z]|$)",
    re.I,
)


def override_for(key, e):
    for k, v in OVERRIDES.items():
        if k.startswith("$"):
            continue
        day, opening = k.split("|", 1)
        if day == key and fold(e).strip().startswith(fold(opening).strip()):
            return v
    return None


def main():
    prov, coined, stats = {}, {}, collections.Counter()
    unresolved = []
    for mo in range(1, 13):
        path = ED_DIR / f"{mo:02d}.json"
        month = json.load(open(path, encoding="utf-8"))
        for d in sorted(month, key=int):
            day = month[d]
            key, mmdd = f"{mo}-{d}", f"{mo:02d}{int(d):02d}"
            el = list(day["elogia"].values() if isinstance(day["elogia"], dict) else day["elogia"])
            split = []
            for e in el:
                o = override_for(key, e)
                if isinstance(o, dict) and o.get("split_at", "") in e and "split_at" in o:
                    i = e.index(o["split_at"])
                    split += [e[:i].strip(), e[i:].strip()]
                else:
                    split.append(e)
            el, rubrics = [], []
            for e in split:
                o = override_for(key, e)
                if o == "MERGE_PREV" and el:
                    el[-1] += " " + e
                elif o == "RUBRIC":
                    rubrics.append((len(el) - 1, e))
                elif o != "DROP":
                    el.append(e)
            F = [fold(e) for e in el]
            pairs = sorted(
                (
                    (score(F[x], i), x, i)
                    for x in range(len(el))
                    for i in BYDAY[(mo, int(d))]
                    if gated(F[x], i)
                ),
                key=lambda p: -(p[0][0] + 0.3 * p[0][1]),
            )
            assign, used = {}, set()
            for (s, n), x, i in pairs:
                if x not in assign and i not in used and accept_same(s, n):
                    assign[x] = (i, "same-day", round(s, 3))
                    used.add(i)
            # unmatched fragments without a saint marker continue the eulogy before them
            merged, massign = [], {}
            for x, e in enumerate(el):
                if (
                    x not in assign
                    and merged
                    and override_for(key, e) is None
                    and not SAINT_MARK.search(" ".join(e.split()[:10]))
                ):
                    merged[-1] += " " + e
                    stats["merged"] += 1
                    continue
                if x in assign:
                    massign[len(merged)] = assign[x]
                merged.append(e)
            obj = {}
            for x, e in enumerate(merged):
                o = override_for(key, e)
                if isinstance(o, str) and o.startswith("mr:"):
                    cid, method, sc = o, "override", None
                elif isinstance(o, dict) and "coin" in o:
                    cid, method, sc = f"mr:{mmdd}-{o['coin']}", "coined-deprecated", None
                    coined[cid] = {
                        "subject_la": o.get("subject_la"),
                        "same_eulogy": o.get("same_eulogy"),
                        "note": o.get("note"),
                        "country": o.get("country"),
                    }
                elif x in massign:
                    cid, method, sc = massign[x]
                else:
                    f = fold(e)
                    cands = sorted(
                        (
                            (score(f, i)[0], i)
                            for i in ENT
                            if gated(f, i) and stems(i) and name_hit(i, f) >= 0.99
                        ),
                        reverse=True,
                    )
                    if (
                        cands
                        and cands[0][0] >= 0.70
                        and (len(cands) == 1 or cands[0][0] - cands[1][0] >= 0.08)
                    ):
                        twin = cands[0][1]
                        cid = f"mr:{mmdd}-{twin.split('-', 1)[1]}"
                        method, sc = "cross-day", round(cands[0][0], 3)
                        if cid in ENT and not ENT[cid].get("deprecated") and cid != twin:
                            unresolved.append(
                                (key, x, e[:100], [(round(s, 2), i) for s, i in cands[:3]])
                            )
                            cid, method, sc = f"mr:{mmdd}-unresolved-{x}", "unresolved", None
                        elif cid not in ENT:
                            group = [twin] + [
                                g for g in ENT[twin].get("same_eulogy") or [] if g != cid
                            ]
                            coined[cid] = {
                                "subject_la": LA.get(twin),
                                "same_eulogy": group,
                                "twin": twin,
                            }
                    else:
                        unresolved.append(
                            (key, x, e[:100], [(round(s, 2), i) for s, i in cands[:3]])
                        )
                        cid, method, sc = f"mr:{mmdd}-unresolved-{x}", "unresolved", None
                obj[cid] = e
                prov[cid] = {"method": method, "score": sc}
                stats[method] += 1
            out = {"titulus": day["titulus"], "elogia": obj}
            if day.get("conclusio"):
                out["conclusio"] = day["conclusio"]
            if rubrics:
                keys = list(obj)
                out["rubricae"] = [
                    {"after": keys[a] if a >= 0 else None, "text": t} for a, t in rubrics
                ]
            month[d] = out
        if WRITE:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(month, f, ensure_ascii=False, indent=1)
                f.write("\n")
    print(dict(stats))
    for u in unresolved:
        print("UNRESOLVED", u)
    new = []
    for cid, c in sorted(coined.items()):
        if cid in ENT:
            continue
        mm, dd = int(cid[3:5]), int(cid[5:7])
        keys = (
            list(json.load(open(ED_DIR / f"{mm:02d}.json", encoding="utf-8"))[str(dd)]["elogia"])
            if WRITE
            else []
        )
        twin = ENT.get(c.get("twin") or (c.get("same_eulogy") or [None])[0] or "", {})
        row = {
            "id": cid,
            "month": mm,
            "day": dd,
            "entry": keys.index(cid) + 1 if cid in keys else None,
            "deprecated": True,
            "attested_in": EDITION,
            "country": c.get("country") or twin.get("country"),
            "subject_la": c.get("subject_la"),
        }
        if c.get("same_eulogy"):
            row["same_eulogy"] = c["same_eulogy"]
        if c.get("note"):
            row["note"] = c["note"]
        new.append(row)
    print("new deprecated IDs:", len(new))
    if WRITE:
        with open(ED_DIR / "alignment.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "$comment": "Per-ID alignment provenance for the 1630 edition: same-day "
                    "(an ID of the same day, matched on its 1749/2004 Latin text or, for IDs "
                    "attested only in the 1914 English, on the name), cross-day (a new ID for "
                    "this day with its twin's slug, linked by same_eulogy), override (reviewed, "
                    "scripts/data/align_1630_overrides.json) or coined-deprecated. Scores are "
                    "text similarities; all draft.",
                    "ids": prov,
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
            f.write("\n")
        with open(ROOT / "deprecated_ids_1630.json", "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("deprecated_ids_1630.json written to the repo root; merge into crmedr/data/")


if __name__ == "__main__":
    main()
