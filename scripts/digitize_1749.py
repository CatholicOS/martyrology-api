#!/usr/bin/env python3
"""Digitize the 1749 (Benedict XIV) edition of the Martyrologium Romanum from
the OCR text layer of its public-domain scan into day-keyed monthly JSON.

Output: data/editions/martyrologium_romanum_1749/MM.json
Quality: raw, uncorrected OCR — see data/editions/README.md.

Usage:
  pip install pypdf
  python3 digitize_1749.py "/path/to/martyrologium_romanum 1749.pdf" [repo_root]
"""

import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

MONTHS = ['Januarii', 'Februarii', 'Martii', 'Aprilis', 'Maji', 'Junii',
          'Julii', 'Augusti', 'Septembris', 'Octobris', 'Novembris', 'Decembris']
HDR = re.compile(
    r'^\s*(\d{1,2})\s+(%s)\b([^\n]*(?:Kalend|Nonas|Nonis|Idus|Idibus|Pridie)[^\n]*)'
    % '|'.join(MONTHS), re.M)
TAIL = re.compile(r'^\s*(?:%s)?\.?\s*[xvij]+\.?\s+[A-Gabcdefg]\s*$' % '|'.join(MONTHS))
CONCL = re.compile(r'Et alibi aliorum.{0,120}?Deo gratias\.?', re.S)
ETALIBI = re.compile(r'Et\s*a\s*l\s*i\s*b\s*i\b')


def lines_of(block):
    out = []
    for ln in block.split('\n'):
        s = ln.strip()
        if not s:
            out.append('')
            continue
        if re.fullmatch(r'\d{1,3}', s):        # page numbers
            continue
        if s in MONTHS or s.upper() in ('MARTYROLOGIUM', 'ROMANUM', 'MARTYROLOGIUM ROMANUM'):
            continue
        out.append(re.sub(r'\s+', ' ', s))
    return out


def paras_blank(lines):
    paras, cur = [], []
    for ln in lines:
        if ln == '':
            if cur:
                paras.append(' '.join(cur))
                cur = []
        else:
            cur.append(ln)
    if cur:
        paras.append(' '.join(cur))
    return paras


def paras_boundary(lines):
    """Fallback where the OCR lost blank lines: split when the previous line
    ends with a period and the next starts uppercase."""
    paras, cur = [], []
    for ln in lines:
        if ln == '':
            continue
        if cur and cur[-1].rstrip().endswith('.') and ln[:1].isupper():
            paras.append(' '.join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        paras.append(' '.join(cur))
    return paras


def split_conclusio(day):
    if 'conclusio' in day or not day['elogia']:
        return
    last = day['elogia'][-1]
    if 'Deo gratias' in last[-40:]:
        m = ETALIBI.search(last)
        if m and m.start() > 0:
            pre = last[:m.start()].rstrip()
            day['elogia'][-1] = pre
            day['conclusio'] = re.sub(r'\s+', ' ', last[m.start():])
            if not pre:
                day['elogia'].pop()
        else:
            day['conclusio'] = last
            day['elogia'].pop()


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    reader = PdfReader(sys.argv[1])
    repo_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent
    text = '\n'.join((p.extract_text() or '') for p in reader.pages)
    matches = list(HDR.finditer(text))
    days = {}
    for i, m in enumerate(matches):
        d, mon = int(m.group(1)), MONTHS.index(m.group(2)) + 1
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        lines = lines_of(text[start:end])
        titulus_extra = ''
        j = 0
        while j < len(lines) and lines[j] == '':
            j += 1
        if j < len(lines) and TAIL.match(lines[j]):
            titulus_extra = ' ' + lines[j]
            lines = lines[:j] + lines[j + 1:]
        paras = paras_blank(lines)
        if len(paras) <= 2 and sum(len(p) for p in paras) > 400:
            paras = paras_boundary(lines)
        merged = []
        for p in paras:
            if merged and (p[:1].islower() or p[:1] in ',;)'):
                merged[-1] += ' ' + p
            else:
                merged.append(p)
        day = {'titulus': re.sub(r'\s+', ' ', f"{m.group(1)} {m.group(2)}{m.group(3).rstrip()}{titulus_extra}").strip(),
               'elogia': []}
        for p in merged:
            cm = CONCL.search(p)
            if cm:
                pre = p[:cm.start()].strip()
                if pre:
                    day['elogia'].append(pre)
                day['conclusio'] = re.sub(r'\s+', ' ', cm.group(0))
                break
            day['elogia'].append(p)
        split_conclusio(day)
        # merge continuation fragments: a paragraph following one that does
        # not end with sentence-final punctuation is the same elogium, split
        # by the OCR (e.g. across a quotation or a page break)
        joined = []
        for p in day['elogia']:
            if joined and not joined[-1].rstrip().endswith(('.', '!', '?')):
                joined[-1] = joined[-1].rstrip() + ' ' + p
            else:
                joined.append(p)
        day['elogia'] = joined
        days[(mon, d)] = day

    out_dir = repo_root / 'data' / 'editions' / 'martyrologium_romanum_1749'
    out_dir.mkdir(parents=True, exist_ok=True)
    for mon in range(1, 13):
        month = {str(d): v for (mo, d), v in sorted(days.items()) if mo == mon}
        with open(out_dir / f'{mon:02d}.json', 'w', encoding='utf-8') as f:
            json.dump(month, f, ensure_ascii=False, indent=1)
            f.write('\n')
    total = sum(len(v['elogia']) for v in days.values())
    print(f'{len(days)} days, {total} elogia -> {out_dir}')


if __name__ == '__main__':
    main()
