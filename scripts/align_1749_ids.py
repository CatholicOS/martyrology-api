#!/usr/bin/env python3
"""Align the digitized 1749 elogia to CRMEDR canonical IDs and coin deprecated
IDs for elogia with no counterpart in the editio altera 2004.

Consumes the output of digitize_1749.py (elogia as arrays), and rewrites
data/editions/martyrologium_romanum_1749/MM.json with `elogia` as objects
keyed by canonical ID (insertion order = printed order), plus alignment.json
(per-ID method and score). Also emits deprecated_ids.json for the CRMEDR
(commit it there as data/deprecated_ids.json).

Matching (all draft, committee review expected):
  pass 1 (same day)  — registry entries of the same MMDD, scored on slug-stem
                       hits against capitalized tokens, text similarity against
                       the 2004 Latin text, and place similarity; gated on
                       strong name OR strong text evidence; greedy one-to-one.
  pass 2 (cross day) — for the unmatched, any unclaimed registry entry whose
                       slug stems match the elogium's *name zone* (capitalized
                       tokens after a sanctity marker, avoiding place-token
                       false hits), text similarity >= 0.45, and a uniqueness
                       margin. Catches saints repositioned by the reform.
  rest               — coined ID mr:MMDD-<slug> from the 1749 placement and a
                       mechanical name extract (genitive form retained;
                       octaves/vigils prefixed octava-/vigilia-), deduplicated
                       by place token then ordinal; deprecated: true.

Text similarity uses the 2004 Latin texts from the PRIVATE
CatholicOS/martyrology-texts repository (data/editions/martyrologium_romanum_2004).
Without that path the matcher degrades to stem-only evidence and cross-day
matching is disabled.

Usage:
  python3 align_1749_ids.py /path/to/crmedr [/path/to/martyrology-texts] [repo_root]
"""

import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


def fold(s):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z ]', ' ', s.lower().replace('æ', 'e').replace('œ', 'e').replace('j', 'i'))


def foldslug(s):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z]+', '-', s.lower().replace('æ', 'e').replace('œ', 'e')).strip('-')


def cap_tokens(raw):
    raw2 = re.sub(r'\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b', r'\1\2', raw)
    return [fold(t).strip() for t in re.findall(r'[A-Z][a-zA-Z]{2,}', raw2)]


MARKER = re.compile(r'(?:sanct[iaeo]+r?u?m?|beat[iaeo]+r?u?m?)\s+((?:[A-Z][a-zA-Z]+[,\s]+){1,4})', re.I)
RANK6 = {'martyr', 'episco', 'confes', 'virgin', 'presby', 'abbati', 'diacon', 'papae',
         'regis', 'ducis', 'monach', 'militu', 'matron', 'vidua', 'sacerd', 'item', 'eodem'}
RANKWORDS = {'Martyris', 'Martyrum', 'Episcopi', 'Confessoris', 'Confessorum', 'Virginis',
             'Virginum', 'Presbyteri', 'Abbatis', 'Abbatum', 'Diaconi', 'Papae', 'Regis',
             'Reginae', 'Ducis', 'Monachi', 'Monachorum', 'Militum', 'Viduae', 'Sacerdotis',
             'Levitae', 'Pontificis', 'Anachoretae', 'Eremitae', 'Imperatoris', 'Comitis',
             'Matris', 'Item', 'Eodem', 'Ipso', 'Apud', 'Sancti', 'Sanctae', 'Sanctorum',
             'Beati', 'Beatae'}
STOP = {'et', 'de', 'a', 'ab', 'in', 'cum', 'socii'}


def name_zone(raw):
    raw2 = re.sub(r'\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b', r'\1\2', raw)
    out = []
    for m in MARKER.finditer(raw2):
        for t in re.findall(r'[A-Z][a-zA-Z]+', m.group(1)):
            f = fold(t).strip()
            if f and f[:6] not in RANK6:
                out.append(f)
    return out


def slug_stems(mrid):
    parts = mrid.split('-', 1)[1].split('-') if '-' in mrid else []
    return [p[:6] for p in parts if p not in STOP and not p.isdigit() and len(p) >= 4]


def stem_hit(stem, toks):
    for t in toks:
        if t.startswith(stem):
            return 1.0
        if len(t) >= 4 and SequenceMatcher(None, stem, t[:len(stem)]).ratio() >= 0.8:
            return 0.8
    return 0.0


def coin_slug(raw):
    raw2 = re.sub(r'\b([A-Z][a-z]{1,3}) ([a-z]{2,})\b', r'\1\2', raw)
    lead = raw2.lstrip()
    prefix = 'octava-' if re.match(r'Octava\b', lead) else \
             'vigilia-' if re.match(r'Vigilia\b', lead) else ''
    m = MARKER.search(raw2)
    names = []
    if m:
        for t in re.findall(r'[A-Z][a-zA-Z]+', raw2[m.end(1) - len(m.group(1)):m.end(1) + 80][:80]):
            if t in RANKWORDS:
                break
            names.append(t)
            if len(names) == 2:
                break
    if not names:
        names = re.findall(r'[A-Za-z]+', lead)[:3]
    return prefix + '-'.join(foldslug(n) for n in names if foldslug(n))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    crmedr = Path(sys.argv[1])
    texts_repo = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    repo_root = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(__file__).resolve().parent.parent
    ed_dir = repo_root / 'data' / 'editions' / 'martyrologium_romanum_1749'

    ed = {}
    for mon in range(1, 13):
        month = json.load(open(ed_dir / f'{mon:02d}.json', encoding='utf-8'))
        for d, v in month.items():
            if not isinstance(v['elogia'], list):
                sys.exit('elogia already aligned (objects); re-run digitize_1749.py first for a fresh alignment')
            ed[(mon, int(d))] = v

    reg = json.load(open(crmedr / 'data' / 'martyrology_ids.json', encoding='utf-8'))
    current = [e for e in reg['entries'] if not e.get('deprecated')]
    existing_ids = {e['id'] for e in reg['entries']}

    la2004 = {}
    if texts_repo:
        tdir = texts_repo / 'data' / 'editions' / 'martyrologium_romanum_2004'
        for mon in range(1, 13):
            month = json.load(open(tdir / f'{mon:02d}.json', encoding='utf-8'))
            la2004.update({k: fold(v)[:300] for k, v in month.items()})

    by_day = {}
    for e in current:
        by_day.setdefault((e['month'], e['day']), []).append(e['id'])

    folded = {k: [fold(el) for el in v['elogia']] for k, v in ed.items()}
    ctoks = {k: [cap_tokens(el) for el in v['elogia']] for k, v in ed.items()}
    nzone = {k: [name_zone(el) for el in v['elogia']] for k, v in ed.items()}

    def accept(ef, s_stem, s_text):
        if ef.lstrip().startswith(('octava', 'vigilia')):
            return s_text >= 0.42 and s_stem >= 0.5
        return s_stem >= 0.65 or s_text >= 0.42

    assign, claimed = {}, set()
    for key, day in ed.items():
        cands = by_day.get(key, [])
        pairs = []
        for idx in range(len(day['elogia'])):
            ef, ct = folded[key][idx], ctoks[key][idx]
            for c in cands:
                stems = slug_stems(c)
                s_stem = sum(stem_hit(st, ct) for st in stems) / len(stems) if stems else 0
                t = la2004.get(c, '')
                s_text = SequenceMatcher(None, ef[:300], t).ratio() if t else 0
                s_place = SequenceMatcher(None, ef[:14], t[:14]).ratio() if t else 0
                if accept(ef, s_stem, s_text):
                    pairs.append((1.5 * s_stem + 1.3 * s_text + 0.5 * s_place, idx, c))
        pairs.sort(reverse=True)
        used_i, used_c = set(), set()
        for tot, idx, c in pairs:
            if idx in used_i or c in used_c:
                continue
            assign[(key, idx)] = (c, round(tot, 2), 'same-day')
            used_i.add(idx)
            used_c.add(c)
            claimed.add(c)

    if la2004:
        unclaimed = [e['id'] for e in current if e['id'] not in claimed]
        uc_stems = {c: slug_stems(c) for c in unclaimed}
        for key, day in ed.items():
            for idx in range(len(day['elogia'])):
                if (key, idx) in assign:
                    continue
                ef, nz = folded[key][idx], nzone[key][idx]
                if not nz or ef.lstrip().startswith(('octava', 'vigilia')):
                    continue
                best = []
                for c in unclaimed:
                    if c in claimed:
                        continue
                    stems = uc_stems[c]
                    if not stems:
                        continue
                    s_stem = sum(stem_hit(st, nz) for st in stems) / len(stems)
                    if s_stem < 0.75:
                        continue
                    s_text = SequenceMatcher(None, ef[:300], la2004.get(c, '')).ratio()
                    if s_text < 0.45:
                        continue
                    best.append((1.5 * s_stem + 1.3 * s_text, c))
                best.sort(reverse=True)
                if best and (len(best) == 1 or best[0][0] - best[1][0] >= 0.3):
                    assign[(key, idx)] = (best[0][1], round(best[0][0], 2), 'cross-day')
                    claimed.add(best[0][1])

    coined, dep_entries, sidecar = set(), [], {}
    for key, day in sorted(ed.items()):
        m, d = key
        obj = {}
        for idx, el in enumerate(day['elogia']):
            if (key, idx) in assign:
                cid, sc, method = assign[(key, idx)]
                sidecar[cid] = {'method': method, 'score': sc}
            else:
                slug = coin_slug(el) or 'sine-nomine'
                cid = f'mr:{m:02d}{d:02d}-{slug}'
                if cid in existing_ids or cid in coined:
                    pm = re.match(r'\s*(?:Apud|In|Ad)?\s*([A-Z][a-zA-Z]+)', el)
                    cid2 = f'{cid}-{foldslug(pm.group(1)) if pm else "loco"}'
                    n = 2
                    while cid2 in existing_ids or cid2 in coined:
                        cid2 = f'{cid}-{n}'
                        n += 1
                    cid = cid2
                coined.add(cid)
                dep_entries.append({'id': cid, 'month': m, 'day': d, 'entry': idx + 1,
                                    'deprecated': True,
                                    'attested_in': 'martyrologium_romanum_1749'})
                sidecar[cid] = {'method': 'coined-deprecated', 'score': None}
            obj[cid] = el
        day['elogia'] = obj

    for mon in range(1, 13):
        month = {str(d): v for (mo, d), v in sorted(ed.items()) if mo == mon}
        with open(ed_dir / f'{mon:02d}.json', 'w', encoding='utf-8') as f:
            json.dump(month, f, ensure_ascii=False, indent=1)
            f.write('\n')
    with open(ed_dir / 'alignment.json', 'w', encoding='utf-8') as f:
        json.dump({'$comment': 'Per-ID alignment provenance: same-day / cross-day '
                   '(matched to a current CRMEDR ID) or coined-deprecated (no '
                   'counterpart identified; ID coined with deprecated:true in the '
                   'CRMEDR). Scores are matcher totals; all draft.',
                   'ids': sidecar}, f, ensure_ascii=False, indent=1)
        f.write('\n')
    with open(repo_root / 'deprecated_ids.json', 'w', encoding='utf-8') as f:
        json.dump(dep_entries, f, ensure_ascii=False, indent=1)
        f.write('\n')
    matched = sum(1 for v in sidecar.values() if v['method'] != 'coined-deprecated')
    print(f'aligned {matched} elogia, coined {len(dep_entries)} deprecated IDs; '
          f'deprecated_ids.json written to repo root (commit into crmedr/data/)')


if __name__ == '__main__':
    main()
