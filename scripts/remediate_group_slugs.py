#!/usr/bin/env python3
"""Analyse (and optionally apply) the group-slug remediation.

One-off correction (2026-07-20) run *on top of* align_1749_ids.py's output: the
first coining pass mis-slugged multi-martyr eulogies with the *place* name (e.g.
mr:0103-martyres-cilicia for the named "Zosimus and Athanasius"), because its
subject extractor stopped at the intervening genitive class-word (Martyrum,
fratrum) before reaching the names. This script re-derives the named subjects and,
for each deprecated ID whose lead token is a class word (martyres/fratres/milites/
virgines/monachi/presbyteri), decides:
  MATCH  -> the same saints already exist as a current 2004 ID (de-coin);
  RESLUG -> genuine deprecation, re-slug to the first-named subject in nominative
            lemma with -et-<second> (pair) / -et-socii (three or more);
  ANON   -> genuinely anonymous ("plurimorum ... Martyrum"), keep martyres-<place>.
It edits crmedr/data/deprecated_ids.json and the 1749 edition + alignment.json in
place; afterwards re-run crmedr/scripts/extract_registry.py and extract_subjects.py.
The improved extraction should eventually be folded back into align_1749_ids.py so a
fresh digitize -> align run reproduces the corrected slugs directly. Idempotent
(re-running on corrected data leaves only the anonymous groups, a no-op).

Requires the PRIVATE martyrology-texts repo for the 2004 name-set matching.

Usage: remediate_group_slugs.py <crmedr> <api> <texts> [--apply]
"""

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CRMEDR, API, TEXTS = map(Path, sys.argv[1:4])
APPLY = "--apply" in sys.argv


def fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower().replace("æ", "e").replace("œ", "e").replace("j", "i"))


def deaccent(s):
    """Strip combining accents but preserve case (for name detection)."""
    s = unicodedata.normalize("NFKD", s)
    return (
        "".join(c for c in s if not unicodedata.combining(c)).replace("æ", "ae").replace("œ", "oe")
    )


def foldslug(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(
        r"[^a-z]+", "-", s.lower().replace("æ", "e").replace("œ", "e").replace("j", "i")
    ).strip("-")


# ---- lemma map mined from registry + 2004 texts (same as pipeline) ----
reg = json.load(open(CRMEDR / "data" / "martyrology_ids.json", encoding="utf-8"))
current = [e for e in reg["entries"] if not e.get("deprecated")]
texts = {}
for m in range(1, 13):
    texts.update(
        json.load(
            open(
                TEXTS / "data" / "editions" / "martyrologium_romanum_2004" / f"{m:02d}.json",
                encoding="utf-8",
            )
        )
    )


def _mine():
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


lemma = _mine()
LEMMA_MANUAL = {
    "fidei": "fides",
    "spei": "spes",
    "caritatis": "caritas",
    "manuelis": "manuel",
    "sosthenis": "sosthenes",
    "zenaidis": "zenais",
    "irenes": "irene",
    "aristsei": "aristaeus",
    "socratis": "socrates",
    "epistemis": "episteme",
}


def lemmatize(tok):
    if tok in LEMMA_MANUAL:
        return LEMMA_MANUAL[tok]
    if tok in lemma:
        return lemma[tok]
    if tok.endswith("ae"):
        return tok[:-2] + "a"
    if tok.endswith("ii"):
        return tok[:-2] + "ius"
    if tok.endswith("onis"):
        return tok[:-4] + "o"
    if tok.endswith("oris"):
        return tok[:-4] + "or"
    if tok.endswith("icis"):
        return tok[:-4] + "ix"
    if tok.endswith("ntis"):
        return tok[:-4] + "ns"
    if tok.endswith("etis"):
        return tok[:-4] + "es"
    if tok.endswith("i") and len(tok) >= 4:
        return tok[:-1] + "us"
    return tok


RANK = {
    "martyris",
    "martyrum",
    "martyr",
    "episcopi",
    "episcopus",
    "confessoris",
    "confessorum",
    "virginis",
    "virginum",
    "presbyteri",
    "presbyterorum",
    "abbatis",
    "abbatum",
    "diaconi",
    "diaconorum",
    "papae",
    "regis",
    "reginae",
    "ducis",
    "monachi",
    "monachorum",
    "militis",
    "militum",
    "viduae",
    "matronae",
    "sacerdotis",
    "levitae",
    "pontificis",
    "anachoretae",
    "eremitae",
    "imperatoris",
    "comitis",
    "matris",
    "martyres",
    "virgines",
    "confessores",
    "eunuchi",
    "eunuchus",
    "eunuchorum",
    "fratrum",
    "fratris",
    "fratram",
    "sororum",
    "puerorum",
    "pueri",
    "puellae",
    "puellarum",
    "clerici",
    "conjugum",
    "sociorum",
    "filiorum",
    "mulierum",
    "germanorum",
    "virorum",
    "adulescentium",
    "infantium",
    "item",
    "eodem",
    "ipso",
    "ordinis",
    "religiosae",
    "centurionis",
    "ac",
    "atque",
    "senis",
    "senioris",
    "iunioris",
    "junioris",
    "maioris",
    "minoris",
    "magni",
    "cognomento",
    "dicti",
    "eius",
    "eiusdem",
}
CLASS = {
    "martyrum": "martyres",
    "militum": "milites",
    "monachorum": "monachi",
    "virginum": "virgines",
    "fratrum": "fratres",
    "confessorum": "confessores",
    "presbyterorum": "presbyteri",
    "sororum": "sorores",
    "puerorum": "pueri",
    "sanctorum": "sancti",
    "sanctarum": "sanctae",
}
MARK = re.compile(
    r"\b(sanctorum|sanctarum|beatorum|beatarum|sancti|sanctae|sanctae|beati|beatae)\b", re.I
)
CONNECT = {"et", "ac", "atque"}
STOPCAP = {
    "hic",
    "haec",
    "qui",
    "quae",
    "quod",
    "cum",
    "nam",
    "sed",
    "ibi",
    "ibidem",
    "nonnulli",
    "plures",
    "alii",
    "aliorum",
    "postea",
    "deinde",
    "tunc",
    "ubi",
    "sub",
    "inter",
    "apud",
    "natalis",
    "passio",
    "item",
    "eodem",
    "ipso",
    "ipsa",
    "beatus",
    "beata",
    "sanctus",
    "sancta",
    "ceterorum",
    "ceteri",
    "omnium",
    "multorum",
}

FUNCWORDS = {
    "in",
    "ad",
    "ac",
    "et",
    "ex",
    "ob",
    "ut",
    "de",
    "non",
    "sub",
    "cum",
    "per",
    "hic",
    "qui",
    "quae",
    "item",
    "apud",
    "ibi",
    "nam",
    "sed",
    "iam",
    "seu",
    "vel",
    "ab",
    "die",
    "quo",
    "quos",
    "haec",
    "a",
}
JOIN2_STOP = {"et", "ac", "atque", "in", "de", "ex", "ad", "sub", "cum", "qui", "quae", "quo"}


def repair_ocr(s):
    """Rejoin OCR-split marker/class words (a single spurious space inside a word)."""
    s = re.sub(
        r"\bsanct([oa])\s*r?\s*u\s*m\b", lambda m: "sanct" + m.group(1) + "rum", s, flags=re.I
    )
    s = re.sub(r"\bbeat([oa])\s*r?\s*u\s*m\b", lambda m: "beat" + m.group(1) + "rum", s, flags=re.I)
    for w in (
        "Martyrum",
        "Martyris",
        "Virginum",
        "Monachorum",
        "Presbyterorum",
        "Diaconorum",
        "mulierum",
        "fratrum",
        "germanorum",
        "sororum",
        "Militum",
    ):
        # allow one internal space at any position
        pat = "".join(c + r"\s?" for c in w[:-1]) + w[-1]
        s = re.sub(r"\b" + pat + r"\b", w, s)
    return s


def desplit(s):
    # rejoin a capitalized word with a trailing single-letter OCR fragment (Julian i -> Juliani)
    s = re.sub(r"\b([A-Z][a-z]{2,}) ([io])\b", r"\1\2", s)
    return re.sub(
        r"\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b",
        lambda mo: (
            mo.group(0)
            if (mo.group(1).lower() in FUNCWORDS or mo.group(2).lower() in JOIN2_STOP)
            else mo.group(1) + mo.group(2)
        ),
        s,
    )


def persons(raw):
    """Ordered nominative lemmas of the named persons after the first plural marker."""
    raw2 = desplit(repair_ocr(deaccent(raw)))
    m = MARK.search(raw2)
    if not m:
        return [], None
    marker = m.group(1).lower()
    tail = raw2[m.end() : m.end() + 140]
    toks = re.findall(r"[A-Za-z]+|,|\.", tail)
    out, expect = [], True
    for t in toks:
        if t == ".":
            break  # names live in the opening noun phrase, before the biography
        low = t.lower()
        if t == "," or low in CONNECT:
            expect = True
            continue
        if low in RANK or low in CLASS:
            # a class/rank word resets to expecting a name (skip it)
            expect = True
            continue
        if low in STOPCAP:
            break
        if t[0].isupper():
            tf = foldslug(t)
            if tf.endswith(("orum", "arum", "ensis", "ense", "itani")):
                continue  # ethnonym / place-adjective (Massylitanorum, Aegyptiorum, Lemovicensis)
            if expect:
                out.append(lemmatize(tf))
                expect = False
            # else: epithet of previous name -> ignore for identity
        else:
            break
    # drop OCR-garbage leads (single letters / fragments)
    out = [p for p in out if len(p) >= 3]
    return out, marker


def honor(marker, n, first_lemma):
    beat = marker.startswith("beat")
    fem = marker.endswith("arum") or marker in ("sanctae", "beatae")
    if n >= 2:
        if fem:
            return "Beatae" if beat else "Sanctae"
        return "Beati" if beat else "Sancti"
    # single
    if fem or first_lemma.endswith("a"):
        return "Beata" if beat else "Sancta"
    return "Beatus" if beat else "Sanctus"


def disp(lem):
    return lem.replace("-", " ").title()


def new_id_subject(m, d, ps, marker):
    if len(ps) >= 3:
        slug = f"{ps[0]}-et-socii"
        subj = f"{honor(marker, 3, ps[0])} {disp(ps[0])} et socii"
    elif len(ps) == 2:
        slug = f"{ps[0]}-et-{ps[1]}"
        subj = f"{honor(marker, 2, ps[0])} {disp(ps[0])} et {disp(ps[1])}"
    else:
        slug = ps[0]
        subj = f"{honor(marker, 1, ps[0])} {disp(ps[0])}"
    return f"mr:{m:02d}{d:02d}-{slug}", subj


# ---- 2004 person name-sets per current id (for matching) ----
by_day = defaultdict(list)
cur_ps = {}
for e in current:
    by_day[(e["month"], e["day"])].append(e["id"])
    ps, _ = persons(texts.get(e["id"], ""))
    # for singletons the slug itself is the name; add slug lead
    lead = e["id"].split("-", 1)[1].split("-")[0]
    s = set(p[:6] for p in ps)
    if lead not in CLASS.values() and len(lead) >= 4 and not lead.isdigit():
        s.add(lemmatize(lead)[:6])
        s.add(lead[:6])
    cur_ps[e["id"]] = s

# ---- 1749 aligned edition: map id -> (mm,dd,text) and load elogia dicts ----
ED = API / "data" / "editions" / "martyrologium_romanum_1749"
months = {mn: json.load(open(ED / f"{mn:02d}.json", encoding="utf-8")) for mn in range(1, 13)}
id_text = {}
for mn, md in months.items():
    for dd, day in md.items():
        for cid, txt in day["elogia"].items():
            id_text[cid] = (mn, int(dd), txt)

dep_entries = json.load(open(CRMEDR / "data" / "deprecated_ids.json", encoding="utf-8"))
dep_by_id = {e["id"]: e for e in dep_entries}

LEAD = {
    "martyres",
    "fratres",
    "milites",
    "virgines",
    "monachi",
    "confessores",
    "sorores",
    "presbyteri",
}
targets = [e for e in dep_entries if e["id"].split("-", 1)[1].split("-")[0] in LEAD]


def name_stems(ps):
    return set(p[:6] for p in ps)


cur_ids = {e["id"] for e in current}
cur_md = {e["id"]: (e["month"], e["day"]) for e in current}
edition_keys = set(id_text)  # every canonical id the 1749 already keys an elogium with
match_meta = {}  # old_id -> (method, score)


def occupied(cid, m, d):
    """True if cid is already a key in that day's 1749 elogia (1:1 constraint)."""
    day = months.get(m, {}).get(str(d))
    if day is None:
        return False
    return cid in day["elogia"]


# Non-group entries the buggy pipeline swept into martyres-* slugs.
SPECIAL = {
    "mr:1225-martyres-anno": (
        "MATCH",
        "mr:1225-nativitas-domini",
        "",
    ),  # Christmas Proclamation (Kalenda)
    "mr:0912-martyres-lugduni": (
        "RESLUG",
        "mr:0912-sacerdos",
        "Sanctus Sacerdos",
    ),  # St Sacerdos, Bp of Lyon
    "mr:0504-martyres-interritorio": (
        "RESLUG",
        "mr:0504-sacerdos",
        "Sanctus Sacerdos",
    ),  # same St Sacerdos
}
# Cross-day fuzzy matches verified against both texts as false or too dubious to
# assert identity -> keep as genuine deprecations instead of merging:
#   1116 African trio+socii != Soissons pair; 1221 Tuscia vs Rome (common names);
#   0510 Inventio of relics is a distinct commemoration from the July 28 feast.
CROSSDAY_REJECT = {
    "mr:1116-martyres-africa",
    "mr:1221-martyres-tuscia",
    "mr:0510-martyres-mediolani",
}
decisions = []  # (old_id, kind, new_id, subject, detail)
for e in targets:
    oid = e["id"]
    m = e["month"]
    d = e["day"]
    if oid in SPECIAL:
        kind, nid, subj = SPECIAL[oid]
        decisions.append(
            (oid, kind + ("-slug" if kind == "MATCH" else ""), nid, subj, "special-case")
        )
        continue
    txt = id_text.get(oid, (None, None, ""))[2]
    ps, marker = persons(txt)
    if not ps:
        decisions.append((oid, "ANON", oid, e["subject_la"], "no names extracted"))
        continue
    nid, subj = new_id_subject(m, d, ps, marker)
    N1 = name_stems(ps)
    # (1) strongest signal: the correctly-derived slug IS an existing current id
    if nid in cur_ids and not occupied(nid, m, d):
        match_meta[oid] = ("same-day" if cur_md[nid] == (m, d) else "cross-day", None)
        decisions.append((oid, "MATCH-slug", nid, "", f"exact slug identity names={ps}"))
        continue
    # (2) fuzzy name-set match: require >=2 shared names; same day, else strict cross-day
    best = None
    for scope, cand_ids in (("same", by_day.get((m, d), [])), ("cross", list(cur_ids))):
        scored = []
        for cid in cand_ids:
            if occupied(cid, m, d) and cid != oid:
                continue
            # cross-day: reject if the 1749 already covers this saint on its own day
            # (would duplicate the id across two days) -> keep as a genuine deprecation
            if scope == "cross" and cid in edition_keys:
                continue
            N2 = cur_ps.get(cid, set())
            shared = N1 & N2
            if len(shared) < 2:
                continue
            jac = len(shared) / len(N1 | N2)
            if scope == "cross" and jac < 0.66:
                continue
            scored.append((len(shared) + jac, jac, len(shared), cid))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.3):
            best = (scope,) + scored[0]
            break
    # verified-false / dubious cross-day merges -> keep as genuine deprecations
    if best and best[0] == "cross" and oid in CROSSDAY_REJECT:
        best = None
    if best:
        match_meta[oid] = ("same-day" if best[0] == "same" else "cross-day", round(best[1], 2))
        decisions.append(
            (oid, f"MATCH-{best[0]}", best[4], "", f"shared={best[3]} jac={best[2]:.2f} names={ps}")
        )
    else:
        decisions.append((oid, "RESLUG", nid, subj, f"names={ps}"))
# special-case matches: method by day comparison
for oid, (kind, nid, _subj) in SPECIAL.items():
    if kind == "MATCH" and oid in dep_by_id:
        m, d = dep_by_id[oid]["month"], dep_by_id[oid]["day"]
        match_meta[oid] = ("same-day" if cur_md.get(nid) == (m, d) else "cross-day", None)

kinds = Counter(k.split("-")[0] for _, k, *_ in decisions)
print("TARGETS:", len(targets), "| outcomes:", dict(kinds))
# collision checks
reslug_new = [nid for _, k, nid, _, _ in decisions if k == "RESLUG"]
dup = [x for x, n in Counter(reslug_new).items() if n > 1]
other_dep = {e["id"] for e in dep_entries} - {o for o, _, _, _, _ in decisions}
clash_cur = [nid for nid in reslug_new if nid in cur_ids]
clash_dep = [nid for nid in reslug_new if nid in other_dep]
print(
    f"COLLISIONS: reslug-dup={dup!r}  reslug-vs-current={clash_cur!r}  "
    f"reslug-vs-otherdep={clash_dep!r}"
)
print()
for oid, kind, nid, _subj, detail in decisions:
    if kind.startswith("MATCH"):
        print(f"[{kind:11}] {oid:38} -> {nid:38} | {detail}")
for oid, kind, nid, subj, _detail in decisions:
    if kind == "RESLUG":
        print(f"[RESLUG     ] {oid:38} -> {nid:38} | {subj}")
for oid, kind, _nid, _subj, _detail in decisions:
    if kind == "ANON":
        print(f"[ANON keep  ] {oid:38} | {id_text.get(oid, (0, 0, ''))[2][:105]}")

if not APPLY:
    print("\n(dry run; pass --apply to write)")
    sys.exit(0)

# ---- APPLY ----
remap = {}  # old_id -> new_id (MATCH target or RESLUG new id)
new_subject = {}  # new_id -> subject_la (RESLUG only)
drop_dep = set()  # old_ids that become current (MATCH) -> removed from deprecated
for oid, kind, nid, subj, _ in decisions:
    if kind.startswith("MATCH"):
        remap[oid] = nid
        drop_dep.add(oid)
    elif kind == "RESLUG":
        remap[oid] = nid
        new_subject[nid] = subj

# 1) deprecated_ids.json: drop MATCHes, rename+resubject RESLUGs
new_dep = []
for e in dep_entries:
    if e["id"] in drop_dep:
        continue
    if e["id"] in remap:  # RESLUG
        e = dict(e)
        e["id"] = remap[e["id"]]
        e["subject_la"] = new_subject[e["id"]]
    new_dep.append(e)
with open(CRMEDR / "data" / "deprecated_ids.json", "w", encoding="utf-8") as f:
    json.dump(new_dep, f, ensure_ascii=False, indent=1)
    f.write("\n")
print(
    f"deprecated_ids.json: {len(dep_entries)} -> {len(new_dep)} "
    f"({len(drop_dep)} promoted to current)"
)

# 2) 1749 edition MM.json: rekey elogia (preserve printed order); 3) alignment.json
align = json.load(open(ED / "alignment.json", encoding="utf-8"))
sidecar = align["ids"]
for mn, md in months.items():
    for dd, day in md.items():
        new_el = {}
        for cid, txt in day["elogia"].items():
            ncid = remap.get(cid, cid)
            if ncid in new_el:  # same-day collision -> merge texts in printed order (no loss)
                print(f"  MERGE {mn:02d}-{dd}: {cid} -> {ncid} (concatenated)")
                new_el[ncid] = new_el[ncid].rstrip() + " " + txt
            else:
                new_el[ncid] = txt
            if cid in remap:
                if cid in match_meta:
                    method, score = match_meta[cid]
                    sidecar.pop(cid, None)
                    sidecar[ncid] = {"method": method, "score": score}
                else:  # RESLUG: still coined-deprecated, just renamed
                    prev = sidecar.pop(cid, {"method": "coined-deprecated", "score": None})
                    sidecar[ncid] = prev
        day["elogia"] = new_el
    with open(ED / f"{mn:02d}.json", "w", encoding="utf-8") as f:
        json.dump(md, f, ensure_ascii=False, indent=1)
        f.write("\n")
with open(ED / "alignment.json", "w", encoding="utf-8") as f:
    json.dump(align, f, ensure_ascii=False, indent=1)
    f.write("\n")
print(f"1749 edition + alignment.json rekeyed ({len(remap)} ids)")
print("\nNow regenerate: extract_registry.py and extract_subjects.py")
