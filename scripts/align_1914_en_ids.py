#!/usr/bin/env python3
"""Align the digitized 1914 English elogia to CRMEDR canonical IDs.

The 1914 unofficial English translation renders the same pre-reform lineage as
the (already aligned) 1749 Latin edition, plus saints added 1750-1914. Each
day's candidate pool is therefore:
  - the IDs of the 1749 edition for that day (current or deprecated), matched
    through their 1749 Latin texts — proper names, places and emperor names
    survive translation as cognates;
  - current CRMEDR IDs for that day not present in 1749 (post-1749 additions),
    matched through the 2004 English + Latin texts (martyrology-texts repo).

Matching combines slug-stem hits on English capitalized tokens (normalized
through an English->Latin name dictionary; genitive slug parts get nominative
variants), cognate-stem Jaccard against the Latin text, and — for 2004-only
candidates — text similarity against the 2004 English translation. Greedy
one-to-one per day with global ID uniqueness, then a strict same-month
cross-day pass. Residuals are resolved via scripts/data/align_1914_overrides.json
(hand-curated), which may assign an existing ID, coin a new deprecated ID
(attested_in: martyrologium_romanum_1914_en_unofficial), merge an OCR fragment
into the previous elogium (MERGE_PREV), or move closing-formula text into the
day's conclusio (CONCLUSIO).

Outputs (with --write): rewritten MM.json (elogia as ID-keyed objects),
alignment.json, and deprecated_ids_1914.json at the repo root (merge into
crmedr/data/deprecated_ids.json). Without --write: report only.

Usage:
  python3 align_1914_en_ids.py /path/to/crmedr /path/to/martyrology-texts [repo_root] [--write]
"""

import json
import re
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

args = [a for a in sys.argv[1:] if a != "--write"]
WRITE = "--write" in sys.argv
CRMEDR = Path(args[0]) if args else sys.exit(__doc__)
TEXTS = Path(args[1]) if len(args) > 1 else sys.exit(__doc__)
ROOT = Path(args[2]) if len(args) > 2 else Path(__file__).resolve().parent.parent

ED_1914 = ROOT / "data" / "editions" / "martyrologium_romanum_1914_en_unofficial"
ED_1749 = ROOT / "data" / "editions" / "martyrologium_romanum_1749"
OVERRIDES_PATH = Path(__file__).resolve().parent / "data" / "align_1914_overrides.json"
EDITION_KEY = "martyrologium_romanum_1914_en_unofficial"


def fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("æ", "e").replace("œ", "e")
    s = re.sub(r"[^a-z ]", " ", s)
    s = s.replace("j", "i")
    return re.sub(r"ae|oe", "e", s)


# ---- English -> Latin lemma dictionary (folded forms: j->i, ae/oe->e applied)
EN2LA = {
    "iohn": "ioannes",
    "iames": "iacobus",
    "peter": "petrus",
    "paul": "paulus",
    "mary": "maria",
    "william": "gulielmus",
    "henry": "henricus",
    "hugh": "hugo",
    "lewis": "ludovicus",
    "louis": "ludovicus",
    "charles": "carolus",
    "francis": "franciscus",
    "frances": "francisca",
    "stephen": "stephanus",
    "laurence": "laurentius",
    "lawrence": "laurentius",
    "denis": "dionysius",
    "dennis": "dionysius",
    "denys": "dionysius",
    "anthony": "antonius",
    "antony": "antonius",
    "andrew": "andreas",
    "matthew": "mattheus",
    "mark": "marcus",
    "luke": "lucas",
    "george": "georgius",
    "gregory": "gregorius",
    "ierome": "hieronymus",
    "ambrose": "ambrosius",
    "augustine": "augustinus",
    "austin": "augustinus",
    "benedict": "benedictus",
    "bernard": "bernardus",
    "dominic": "dominicus",
    "vincent": "vincentius",
    "catherine": "catharina",
    "katherine": "catharina",
    "katharine": "catharina",
    "margaret": "margarita",
    "iane": "ioanna",
    "ioan": "ioanna",
    "lucy": "lucia",
    "cecily": "cecilia",
    "helen": "helena",
    "anne": "anna",
    "ann": "anna",
    "elizabeth": "elisabeth",
    "christopher": "christophorus",
    "nicholas": "nicolaus",
    "martin": "martinus",
    "philip": "philippus",
    "bartholomew": "bartholomeus",
    "iude": "iudas",
    "timothy": "timotheus",
    "clement": "clemens",
    "sylvester": "silvester",
    "urban": "urbanus",
    "maurice": "mauritius",
    "sebastian": "sebastianus",
    "fabian": "fabianus",
    "blase": "blasius",
    "blaise": "blasius",
    "valentine": "valentinus",
    "patrick": "patricius",
    "edward": "eduardus",
    "edmund": "edmundus",
    "alban": "albanus",
    "boniface": "bonifatius",
    "hilary": "hilarius",
    "basil": "basilius",
    "cyril": "cyrillus",
    "cyprian": "cyprianus",
    "polycarp": "polycarpus",
    "iustin": "iustinus",
    "dorothy": "dorothea",
    "gertrude": "gertrudis",
    "hedwig": "hedvigis",
    "adalbert": "adalbertus",
    "stanislas": "stanislaus",
    "wenceslas": "wenceslaus",
    "casimir": "casimirus",
    "rose": "rosa",
    "teresa": "teresia",
    "theresa": "teresia",
    "clare": "clara",
    "bridget": ["brigida", "birgitta"],
    "brigid": "brigida",
    "maur": "maurus",
    "verdiana": "viridiana",
    "giles": "egidius",
    "raymond": "raimundus",
    "walter": "gualterus",
    "robert": "robertus",
    "richard": "richardus",
    "gerard": "gerardus",
    "gerald": "geraldus",
    "roger": "rogerius",
    "arnold": "arnoldus",
    "conrad": "conradus",
    "leopold": "leopoldus",
    "albert": "albertus",
    "norbert": "norbertus",
    "romuald": "romualdus",
    "hubert": "hubertus",
    "lambert": "lambertus",
    "germain": "germanus",
    "quentin": "quintinus",
    "crispin": "crispinus",
    "innocent": "innocentius",
    "celestine": "celestinus",
    "isidore": "isidorus",
    "chrysostom": "chrysostomus",
    "constantine": "constantinus",
    "iulian": "iulianus",
    "eugene": "eugenius",
    "adrian": "hadrianus",
    "hadrian": "hadrianus",
    "theodore": "theodorus",
    "gall": "gallus",
    "colman": "colmanus",
    "godfrey": "godefridus",
    "geoffrey": "gaufridus",
    "ralph": "radulphus",
    "canute": "canutus",
    "olaf": "olavus",
    "eric": "ericus",
    "ladislas": "ladislaus",
    "sigismund": "sigismundus",
    "emeric": "emericus",
    "everard": "eberardus",
    "bede": "beda",
    "cuthbert": "cuthbertus",
    "dunstan": "dunstanus",
    "swithin": "swithunus",
    "oswald": "osvaldus",
    "wilfrid": "wilfridus",
    "willibrord": "willibrordus",
    "anselm": "anselmus",
    "moses": "moyses",
    "solomon": "salomon",
    "eve": "eva",
    # feast words and epithets (also matched lowercase mid-text)
    "octave": "octava",
    "vigil": "vigilia",
    "circumcision": "circumcisio",
    "conversion": "conversio",
    "chair": "cathedra",
    "finding": "inventio",
    "invention": "inventio",
    "purification": "purificatio",
    "annunciation": "annuntiatio",
    "assumption": "assumptio",
    "conception": "conceptio",
    "exaltation": "exaltatio",
    "dedication": "dedicatio",
    "translation": "translatio",
    "apparition": "apparitio",
    "commemoration": "commemoratio",
    "child": "puer",
    "almoner": "eleemosynarius",
    "genevieve": "genovefa",
    "hermit": "eremita",
    "anchoret": "anachoreta",
    "cross": "crucis",
    "baptist": "baptista",
    "evangelist": "evangelista",
    "apostle": "apostolus",
    "apostles": "apostoli",
    "abbess": "abbatissa",
    "angels": "angeli",
    "trinity": "trinitas",
    "rosary": "rosarium",
    "snow": "nives",
    "sorrows": "dolores",
    "visitation": "visitatio",
    "transfiguration": "transfiguratio",
    "ascension": "ascensio",
    "espousals": "desponsatio",
}

NUM_EN2LA = {
    "two": "duo",
    "three": "tres",
    "four": "quattuor",
    "five": "quinque",
    "six": "sex",
    "seven": "septem",
    "eight": "octo",
    "nine": "novem",
    "ten": "decem",
    "eleven": "undecim",
    "twelve": "duodecim",
    "thirteen": "tredecim",
    "twenty": "viginti",
    "thirty": "triginta",
    "forty": "quadraginta",
    "fifty": "quinquaginta",
    "sixty": "sexaginta",
    "hundred": "centum",
    "thousand": "mille",
    "many": "multi",
    "seventy": "septuaginta",
    "eighty": "octoginta",
    "ninety": "nonaginta",
}
GROUP_EN2LA = {
    "soldiers": "milites",
    "martyrs": "martyres",
    "virgins": "virgines",
    "monks": "monachi",
    "brothers": "fratres",
    "companions": "socii",
    "bishops": "episcopi",
    "priests": "presbyteri",
    "confessors": "confessores",
    "children": "pueri",
    "women": "mulieres",
    "innocents": "innocentes",
    "queen": "regina",
    "pope": "papa",
    "prophet": "propheta",
    "deacon": "diaconus",
    "monk": "monachus",
    "soldier": "miles",
    "widow": "vidua",
}
PLACE_EN2LA = {
    "rome": "roma",
    "milan": "mediolanum",
    "naples": "neapolis",
    "cologne": "colonia",
    "lyons": "lugdunum",
    "treves": "treviri",
    "mentz": "moguntia",
    "mayence": "moguntia",
    "rheims": "remi",
    "poitiers": "pictavium",
    "tours": "turones",
    "vienne": "vienna",
    "saragossa": "cesaraugusta",
    "seville": "hispalis",
    "toledo": "toletum",
    "florence": "florentia",
    "iapan": "iaponia",
    "england": "anglia",
    "scotland": "scotia",
    "ireland": "hibernia",
    "spain": "hispania",
    "france": "gallia",
    "egypt": "egyptus",
}
WORD_EN2LA = {**NUM_EN2LA, **GROUP_EN2LA, **PLACE_EN2LA}

EN_CAP_STOP = {
    "the",
    "at",
    "in",
    "on",
    "of",
    "st",
    "sts",
    "ss",
    "saint",
    "saints",
    "blessed",
    "holy",
    "also",
    "same",
    "day",
    "city",
    "likewise",
    "his",
    "her",
    "our",
    "lord",
    "god",
    "he",
    "she",
    "after",
    "when",
    "who",
    "this",
    "there",
    "item",
    "further",
    "moreover",
    "again",
    "besides",
    "province",
    "town",
    "near",
    "under",
    "during",
    "emperor",
    "king",
    "governor",
    "pope",
    "bishop",
    "abbot",
    "church",
    "order",
    "monastery",
    "mount",
    "martyr",
    "martyrs",
    "virgin",
    "confessor",
    "priest",
    "birthday",
    "feast",
    "however",
    "although",
    "with",
    "by",
    "from",
    "and",
}

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
ENSTOP = {
    "saint",
    "saints",
    "blessed",
    "martyr",
    "martyrs",
    "bishop",
    "confessor",
    "virgin",
    "priest",
    "abbot",
    "church",
    "emperor",
    "birthday",
    "there",
    "which",
    "whose",
    "after",
    "under",
    "their",
    "being",
    "having",
    "where",
    "during",
    "against",
    "because",
    "through",
    "before",
    "whom",
    "while",
    "other",
    "great",
    "first",
    "same",
    "also",
    "suffered",
    "death",
    "faith",
    "christ",
    "christian",
    "christians",
    "persecution",
    "glorious",
    "crowned",
    "crown",
    "received",
    "obtained",
    "merited",
    "command",
    "order",
    "torments",
    "tortures",
    "beheaded",
    "scourged",
    "burned",
    "killed",
    "slain",
    "thrown",
    "cast",
    "prison",
    "confession",
    "eminent",
    "renowned",
    "famous",
    "illustrious",
    "sanctity",
    "miracles",
    "virtues",
    "heaven",
    "heavenly",
    "kingdom",
    "eternal",
    "departed",
    "passed",
    "rested",
    "peace",
    "happy",
    "holily",
}


def sig_la(folded_text):
    return {t[:5] for t in folded_text.split() if len(t) >= 5 and t not in LATSTOP}


def sig_en(folded_text):
    return {t[:5] for t in folded_text.split() if len(t) >= 5 and t not in ENSTOP}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


SLUGSTOP = {
    "et",
    "de",
    "a",
    "ab",
    "in",
    "cum",
    "socii",
    "sociorum",
    "sancti",
    "sanctae",
    "sanctorum",
    "beati",
    "beatae",
}


def lemma_variants(p):
    """Nominative variants for a (possibly genitive) slug part."""
    out = {p}
    for suf, rep in (
        ("ae", "a"),
        ("ii", "ius"),
        ("onis", "o"),
        ("icis", "ix"),
        ("ntis", "ns"),
        ("is", "es"),
    ):
        if p.endswith(suf) and len(p) > len(suf) + 1:
            out.add(p[: -len(suf)] + rep)
    if p.endswith("i") and len(p) > 3:
        out.add(p[:-1] + "us")
    return out


GROUPNUM = {
    "martyres",
    "milites",
    "virgines",
    "monachi",
    "fratres",
    "socii",
    "presbyteri",
    "episcopi",
    "confessores",
    "pueri",
    "mulieres",
    "innocentes",
    "martyrum",
    "duo",
    "tres",
    "quattuor",
    "quinque",
    "sex",
    "septem",
    "octo",
    "novem",
    "decem",
    "undecim",
    "duodecim",
    "tredecim",
    "viginti",
    "triginta",
    "quadraginta",
    "quinquaginta",
    "sexaginta",
    "septuaginta",
    "octoginta",
    "nonaginta",
    "centum",
    "mille",
    "multi",
    "plurimi",
    "omnes",
    "alii",
    "aliae",
}


def slug_stems(mrid):
    parts = mrid.split("-", 1)[1].split("-") if "-" in mrid else []
    parts = [p.replace("j", "i") for p in parts]
    return [
        (p, {re.sub(r"ae|oe", "e", v)[:6] for v in lemma_variants(p)})
        for p in parts
        if p not in SLUGSTOP and not p.isdigit() and len(p) >= 4
    ]


def stem_hit(variants, toks):
    best = 0.0
    for stem in variants:
        for t in toks:
            if t.startswith(stem) or (len(t) >= 5 and stem.startswith(t[:5])):
                return 1.0
            if len(t) >= 4 and SequenceMatcher(None, stem, t[: len(stem)]).ratio() >= 0.8:
                best = max(best, 0.8)
    return best


def en_tokens(raw):
    """Normalized token pool for a 1914 English elogium: capitalized tokens
    (through the EN->LA name dictionary) plus mapped number/group/place and
    feast words scanned lowercase."""
    caps = [fold(t).strip() for t in re.findall(r"[A-Z][a-zA-Z]{2,}", raw)]
    pool = []

    def vals(v):
        return v if isinstance(v, list) else [v]

    for c in caps:
        c = c.strip()
        if not c or c in EN_CAP_STOP:
            continue
        pool.extend(vals(EN2LA.get(c, c)))
    for w in fold(raw).split():
        if w in WORD_EN2LA:
            pool.append(WORD_EN2LA[w])
        elif w in EN2LA:
            pool.extend(vals(EN2LA[w]))
    return pool


# ---- load 1914 edition (must be unaligned arrays)
ed = {}
for _mon in range(1, 13):
    _month = json.load(open(ED_1914 / f"{_mon:02d}.json", encoding="utf-8"))
    for _d, _v in _month.items():
        if not isinstance(_v["elogia"], list):
            sys.exit("elogia already aligned; re-run digitize_1914_en.py first")
        ed[f"{_mon}-{_d}"] = _v

# ---- load 1749 aligned edition: per-day id -> Latin text
la1749 = {}  # (m, d) -> list[(id, text)]
for _mon in range(1, 13):
    _month = json.load(open(ED_1749 / f"{_mon:02d}.json", encoding="utf-8"))
    for _d, _v in _month.items():
        if isinstance(_v["elogia"], list):
            sys.exit("1749 edition is not aligned; run align_1749_ids.py first")
        la1749[(_mon, int(_d))] = list(_v["elogia"].items())

ids1749 = {cid for pairs in la1749.values() for cid, _ in pairs}

# ---- load registry + 2004 texts
reg = json.load(open(CRMEDR / "data" / "martyrology_ids.json", encoding="utf-8"))
current = [e for e in reg["entries"] if not e.get("deprecated")]
current_by_day = defaultdict(list)
for e in current:
    current_by_day[(e["month"], e["day"])].append(e["id"])

texts_la, texts_en = {}, {}
for m in range(1, 13):
    texts_la.update(
        json.load(
            open(
                TEXTS / "data" / "editions" / "martyrologium_romanum_2004" / f"{m:02d}.json",
                encoding="utf-8",
            )
        )
    )
    texts_en.update(
        json.load(
            open(
                TEXTS
                / "data"
                / "editions"
                / "martyrologium_romanum_2004_en_unofficial"
                / f"{m:02d}.json",
                encoding="utf-8",
            )
        )
    )

# ---- candidate pools per day
cand_by_day = {}  # (m,d) -> {id: {"la": folded, "lasig": set, "en": folded, "ensig": set}}
for (m, d), pairs in la1749.items():
    pool = cand_by_day.setdefault((m, d), {})
    for cid, text in pairs:
        f = fold(text)
        pool[cid] = {"la": f[:300], "lasig": sig_la(f)}
for (m, d), ids in current_by_day.items():
    pool = cand_by_day.setdefault((m, d), {})
    for cid in ids:
        c = pool.setdefault(cid, {})
        lt = texts_la.get(cid)
        if lt and "lasig" not in c:
            f = fold(lt)
            c["la"], c["lasig"] = f[:300], sig_la(f)
        et = texts_en.get(cid)
        if et:
            f = fold(et)
            c["en"], c["ensig"] = f[:300], sig_en(f)
cand_stems = {}
for pool in cand_by_day.values():
    for cid in pool:
        if cid not in cand_stems:
            cand_stems[cid] = slug_stems(cid)

# ---- precompute per-elogium features
folded, toks, sigs = {}, {}, {}
for mm_dd, day in ed.items():
    for idx, el in enumerate(day["elogia"]):
        k = (mm_dd, idx)
        folded[k] = fold(el)
        toks[k] = en_tokens(el)
        sigs[k] = sig_en(folded[k])


def score(k, cid, c):
    stems = cand_stems[cid]
    hits = [stem_hit(vs, toks[k]) for _, vs in stems]
    s_stem = sum(hits) / len(hits) if hits else 0.0
    s_first = 0.0
    for (base, _), h in zip(stems, hits, strict=True):
        if base not in GROUPNUM:
            s_first = h
            break
    jac_la = jaccard({t[:5] for t in toks[k]}, c.get("lasig", set()))
    s_en = SequenceMatcher(None, folded[k][:300], c["en"]).ratio() if c.get("en") else 0.0
    jac_en = jaccard(sigs[k], c.get("ensig", set()))
    total = 1.5 * s_stem + 1.2 * jac_la + 1.0 * s_en + 0.8 * jac_en
    return s_stem, s_first, jac_la, s_en, jac_en, total


def accept(ef, nstems, s_stem, s_first, jac_la, s_en, jac_en, has1749):
    if ef.lstrip().startswith(("octava", "vigilia", "the octave", "the vigil")):
        return s_stem >= 0.5 and (jac_la >= 0.1 or s_en >= 0.4)
    if not has1749:
        # post-1749 candidate known only from the 2004 texts: demand full-name
        # coverage or strong English-text agreement (modern saints have
        # multi-part names; a first-name hit alone is noise)
        return (s_stem >= 0.75 and (jac_en >= 0.1 or jac_la >= 0.1)) or (
            s_en >= 0.55 and jac_en >= 0.2
        )
    return (
        s_stem >= 0.65
        or (s_stem >= 0.4 and (jac_la >= 0.15 or jac_en >= 0.15))
        or (s_en >= 0.45 and jac_en >= 0.15)
        or jac_la >= 0.3
        or (s_first >= 1.0 and (jac_la >= 0.05 or nstems == 1))
    )


# ---- pass 1: per-day greedy, then global one-ID-one-elogium resolution
day_pairs = []  # (total, mm_dd, idx, cid)
for mm_dd, day in ed.items():
    m, d = map(int, mm_dd.split("-"))
    pool = cand_by_day.get((m, d), {})
    for idx in range(len(day["elogia"])):
        k = (mm_dd, idx)
        for cid, c in pool.items():
            s_stem, s_first, jac_la, s_en, jac_en, total = score(k, cid, c)
            if accept(
                folded[k],
                len(cand_stems[cid]),
                s_stem,
                s_first,
                jac_la,
                s_en,
                jac_en,
                "lasig" in c and cid in ids1749,
            ):
                day_pairs.append((total, mm_dd, idx, cid))
day_pairs.sort(reverse=True)
assign, claimed, used_keys = {}, set(), set()
for total, mm_dd, idx, cid in day_pairs:
    k = (mm_dd, idx)
    if k in used_keys or cid in claimed:
        continue
    assign[k] = (cid, round(total, 2), "same-day")
    used_keys.add(k)
    claimed.add(cid)
p1 = len(assign)

# ---- pass 2: same-month cross-day for repositioned entries (strict)
for mm_dd, day in ed.items():
    m, d = map(int, mm_dd.split("-"))
    for idx in range(len(day["elogia"])):
        k = (mm_dd, idx)
        if k in assign:
            continue
        best = []
        for (cm, cd), pool in cand_by_day.items():
            if cm != m or cd == d:
                continue
            for cid, c in pool.items():
                if cid in claimed:
                    continue
                s_stem, s_first, jac_la, s_en, jac_en, total = score(k, cid, c)
                strong = s_stem >= 0.75 or (s_first >= 1.0 and cid in ids1749)
                if strong and (jac_la >= 0.08 or jac_en >= 0.08):
                    best.append((total, cid))
        best.sort(reverse=True)
        if best and (len(best) == 1 or best[0][0] - best[1][0] >= 0.3):
            assign[k] = (best[0][1], round(best[0][0], 2), "cross-day")
            claimed.add(best[0][1])
p2 = len(assign) - p1

# ---- pass 3: last-one-standing per day (single leftover EN elogium and single
# unclaimed 1749-lineage candidate with any shared evidence)
p3 = 0
for mm_dd, day in ed.items():
    m, d = map(int, mm_dd.split("-"))
    left_en = [idx for idx in range(len(day["elogia"])) if (mm_dd, idx) not in assign]
    left_cand = [cid for cid, _ in la1749.get((m, d), []) if cid not in claimed]
    if len(left_en) == 1 and len(left_cand) == 1:
        k = (mm_dd, left_en[0])
        cid = left_cand[0]
        c = cand_by_day[(m, d)][cid]
        s_stem, s_first, jac_la, s_en, jac_en, total = score(k, cid, c)
        if total > 0.25:
            assign[k] = (cid, round(total, 2), "elimination")
            claimed.add(cid)
            p3 += 1

# ---- overrides
overrides = {}
if OVERRIDES_PATH.exists():
    overrides = json.load(open(OVERRIDES_PATH, encoding="utf-8")).get("assignments", {})
ov_used = 0
merges, concl, coins, drops = set(), set(), {}, set()
for key, val in overrides.items():
    mm_dd, idx = key.rsplit("-", 1)
    k = (mm_dd, int(idx))
    if isinstance(val, str):
        if val == "MERGE_PREV":
            merges.add(k)
            old = assign.pop(k, None)
            if old:
                claimed.discard(old[0])
        elif val == "CONCLUSIO":
            concl.add(k)
            old = assign.pop(k, None)
            if old:
                claimed.discard(old[0])
        elif val == "DROP":
            drops.add(k)
            old = assign.pop(k, None)
            if old:
                claimed.discard(old[0])
        else:
            old = assign.get(k)
            if old and old[0] != val:
                claimed.discard(old[0])
            prev_holder = next((kk for kk, vv in assign.items() if vv[0] == val and kk != k), None)
            if prev_holder:
                del assign[prev_holder]
            assign[k] = (val, None, "override")
            claimed.add(val)
        ov_used += 1
    elif isinstance(val, dict) and "coin" in val:
        coins[k] = val
        old = assign.pop(k, None)
        if old:
            claimed.discard(old[0])
        ov_used += 1

print(
    f"pass1: {p1} | pass2: {p2} | pass3: {p3} | overrides: {ov_used} | total elogia: {len(folded)}"
)

# ---- report residuals with near-miss diagnostics
unmatched = []
for mm_dd, day in ed.items():
    for idx, el in enumerate(day["elogia"]):
        k = (mm_dd, idx)
        if all(k not in coll for coll in (assign, merges, concl, coins, drops)):
            unmatched.append((mm_dd, idx, el))
unclaimed_1749 = []
for (m, d), pairs in sorted(la1749.items()):
    for cid, text in pairs:
        if cid not in claimed:
            unclaimed_1749.append((m, d, cid, text))
print(f"unmatched 1914 elogia: {len(unmatched)}")
print(f"unclaimed 1749 ids: {len(unclaimed_1749)}")


def near_misses(mm_dd, idx):
    m, d = map(int, mm_dd.split("-"))
    k = (mm_dd, idx)
    out = []
    for cid, c in cand_by_day.get((m, d), {}).items():
        s_stem, s_first, jac_la, s_en, jac_en, total = score(k, cid, c)
        out.append(
            {
                "id": cid,
                "claimed": cid in claimed,
                "s_first": round(s_first, 2),
                "s_stem": round(s_stem, 2),
                "jac_la": round(jac_la, 2),
                "s_en": round(s_en, 2),
                "jac_en": round(jac_en, 2),
                "total": round(total, 2),
            }
        )
    out.sort(key=lambda x: -x["total"])
    return out[:3]


report = {
    "unmatched": [
        {"key": f"{mm_dd}-{idx}", "text": el[:220], "near": near_misses(mm_dd, idx)}
        for mm_dd, idx, el in sorted(
            unmatched, key=lambda x: (tuple(map(int, x[0].split("-"))), x[1])
        )
    ],
    "unclaimed_1749": [
        {"day": f"{m}-{d}", "id": cid, "text": text[:180]} for m, d, cid, text in unclaimed_1749
    ],
    "assignments": {
        f"{k[0]}-{k[1]}": {"id": v[0], "score": v[1], "method": v[2]}
        for k, v in sorted(assign.items())
    },
    "low_scores": [
        {"key": f"{k[0]}-{k[1]}", "id": v[0], "score": v[1], "method": v[2]}
        for k, v in sorted(assign.items())
        if v[1] is not None and v[1] < 1.0
    ],
}
report_path = ROOT / "align_1914_report.json"
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print(f"report -> {report_path}")

if not WRITE:
    sys.exit(0)

if unmatched:
    sys.exit(f"refusing --write with {len(unmatched)} unmatched elogia; resolve via overrides")

# ---- write aligned month files + alignment sidecar + coined deprecated ids
existing_ids = {e["id"] for e in reg["entries"]}
dep_entries, sidecar, coined = [], {}, set()
aligned = {}
for mm_dd in sorted(ed, key=lambda s: tuple(map(int, s.split("-")))):
    day = ed[mm_dd]
    m, d = map(int, mm_dd.split("-"))
    obj = {}
    conclusio = None
    prev_cid = None
    for idx, el in enumerate(day["elogia"]):
        k = (mm_dd, idx)
        if k in drops:
            continue
        if k in merges:
            if prev_cid is None:
                sys.exit(f"MERGE_PREV on first elogium of {mm_dd}")
            obj[prev_cid] = obj[prev_cid].rstrip() + " " + el.lstrip()
            continue
        if k in concl:
            conclusio = (conclusio + " " + el) if conclusio else el
            continue
        if k in coins:
            slug = coins[k]["coin"]
            cid = f"mr:{m:02d}{d:02d}-{slug}"
            if cid in existing_ids or cid in coined:
                sys.exit(f"coined id already exists: {cid}")
            coined.add(cid)
            dep_entries.append(
                {
                    "id": cid,
                    "month": m,
                    "day": d,
                    "entry": idx + 1,
                    "deprecated": True,
                    "attested_in": EDITION_KEY,
                    "subject_la": coins[k]["subject_la"],
                }
            )
            sidecar[cid] = {"method": "coined-deprecated", "score": None}
        else:
            cid, sc, method = assign[k]
            sidecar[cid] = {"method": method, "score": sc}
        if cid in obj:
            sys.exit(f"duplicate id within {mm_dd}: {cid}")
        obj[cid] = el
        prev_cid = cid
    aligned[mm_dd] = {
        "titulus": day["titulus"],
        "elogia": obj,
        **({"conclusio": conclusio} if conclusio else {}),
    }

for _mon in range(1, 13):
    _month = {k.split("-")[1]: v for k, v in aligned.items() if int(k.split("-")[0]) == _mon}
    with open(ED_1914 / f"{_mon:02d}.json", "w", encoding="utf-8") as f:
        json.dump(_month, f, ensure_ascii=False, indent=1)
        f.write("\n")
with open(ED_1914 / "alignment.json", "w", encoding="utf-8") as f:
    json.dump(
        {
            "$comment": "Per-ID alignment provenance for the 1914 English edition: "
            "same-day / cross-day (matched against the 1749 Latin texts and the 2004 "
            "English/Latin texts), override (hand-curated in "
            "scripts/data/align_1914_overrides.json) or coined-deprecated (new ID "
            "attested only in this edition). Scores are matcher totals; all draft.",
            "ids": sidecar,
        },
        f,
        ensure_ascii=False,
        indent=1,
    )
    f.write("\n")
if dep_entries:
    with open(ROOT / "deprecated_ids_1914.json", "w", encoding="utf-8") as f:
        json.dump(dep_entries, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(
        f"coined deprecated: {len(dep_entries)} -> "
        "deprecated_ids_1914.json (merge into crmedr/data/)"
    )
print("aligned month files written")
