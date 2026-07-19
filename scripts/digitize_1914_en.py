#!/usr/bin/env python3
"""Digitize the 1914 unofficial English translation of the Roman Martyrology
from its public-domain scan into day-keyed monthly JSON.

The scan's embedded text layer has all spaces stripped, so the pages are
re-OCRed (PyMuPDF rasterization at 300dpi + tesseract). The book is set in a
blackletter face for day headings, which tesseract mangles predictably
("Che Sixteenth Dap of April", "Movember" for November): headings are matched
with variant-tolerant patterns, month tokens normalized through a variant map,
and days assigned sequentially per month — every assignment is cross-checked
against a fuzzy decode of the ordinal word, and the run reports any
disagreement. Entries are separated by em-dashes before a capital; footnote
lines (starting "*") and running heads are stripped.

Output: data/editions/martyrologium_romanum_1914_en_unofficial/MM.json
Quality: raw, uncorrected OCR — see data/editions/README.md.

Usage:
  pip install pymupdf   # plus tesseract-ocr with eng
  python3 digitize_1914_en.py "/path/to/The Roman Martyrology (1914).pdf" [repo_root]
"""

import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import fitz

MONTH_MAP = {}
for canon, variants in {
    1: ['january', 'januarp', 'fjanuarp', 'fanuarp'],
    2: ['february', 'februarp', 'februaty'],
    3: ['march', 'warch', 'mwarch', 'watch', 'ware', 'warcd', 'warci'],
    4: ['april'],
    5: ['may', 'wap', 'map', 'mav', 'wav'],
    6: ['june', 'jume'],
    7: ['july', 'fulp', 'fjulp', 'fulv', 'julp'],
    8: ['august'],
    9: ['september', 'septenrder'],
    10: ['october', 'dctober', 'dectober'],
    11: ['november', 'movember', 'wovmber'],
    12: ['december', 'decenrber'],
}.items():
    for v in variants:
        MONTH_MAP[v] = canon
MONTH_EN = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
            'August', 'September', 'October', 'November', 'December']
ORDINALS = ['First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth', 'Seventh',
            'Eighth', 'Ninth', 'Tenth', 'Eleventh', 'Twelfth', 'Thirteenth',
            'Fourteenth', 'Fifteenth', 'Sixteenth', 'Seventeenth', 'Eighteenth',
            'Nineteenth', 'Twentieth', 'Twenty-first', 'Twenty-second',
            'Twenty-third', 'Twenty-fourth', 'Twenty-fifth', 'Twenty-sixth',
            'Twenty-seventh', 'Twenty-eighth', 'Twenty-ninth', 'Thirtieth',
            'Thirty-first']
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

HDR = re.compile(r'^.{0,8}\b(?:Che|The|Cbe|Ehe|Ghe|che|he|[CGE]he)\s+(\S{2,25})'
                 r'\s+(?:Dap|Day|Davy|Dav|Bay|Pap|day|Dan|Dayp|Duy|Oap)\s+of\s+(\S{3,12})', re.M)
RUNHEAD = re.compile(r'^\s*[|:.;\-\'"“”]*\s*\d*\s*(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY'
                     r'|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s*[.,!]?\s*\d*\s*[|:.;]*\s*$')


def ocr_pages(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []
    with tempfile.NamedTemporaryFile(suffix='.png') as tmp:
        for i in range(len(doc)):
            doc[i].get_pixmap(dpi=300).save(tmp.name)
            r = subprocess.run(['tesseract', tmp.name, 'stdout', '-l', 'eng', '--psm', '6'],
                               capture_output=True, text=True)
            pages.append(r.stdout)
            if (i + 1) % 50 == 0:
                print(f'OCR {i + 1}/{len(doc)}', flush=True)
    return pages


def content_end(pages):
    for i, t in enumerate(pages):
        if re.match(r'^\s*\S{0,4}\s*INDEX', t[:30]):
            return i
    return len(pages)


def clean_lines(block):
    out, skip_footnote = [], False
    for ln in block.split('\n'):
        s = ln.strip()
        if not s:
            skip_footnote = False
            continue
        if s.startswith('*'):
            skip_footnote = True
            continue
        if skip_footnote or RUNHEAD.match(s) or re.fullmatch(r'\d{1,3}', s):
            continue
        if not re.search(r'[A-Za-z]{3}', s):
            continue
        out.append(s)
    return out


def join_lines(lines):
    buf = ''
    for s in lines:
        if buf.endswith('-') and s[:1].islower():
            buf = buf[:-1] + s
        elif buf:
            buf += ' ' + s
        else:
            buf = s
    return re.sub(r'\s+', ' ', buf)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    repo_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent
    pages = ocr_pages(sys.argv[1])
    text = '\n'.join(pages[:content_end(pages)])
    matches = [m for m in HDR.finditer(text) if MONTH_MAP.get(m.group(2).lower().strip(',.'))]
    seq = defaultdict(int)
    days = {}
    problems = []
    for i, m in enumerate(matches):
        mon = MONTH_MAP[m.group(2).lower().strip(',.')]
        seq[mon] += 1
        d = seq[mon]
        tok = re.sub(r'^c', 't', m.group(1).lower())
        tok = re.sub(r'p', 'y', tok)
        best = max(range(31), key=lambda k: SequenceMatcher(None, tok, ORDINALS[k].lower()).ratio())
        if best + 1 != d:
            problems.append((mon, d, m.group(1), best + 1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = join_lines(clean_lines(text[start:end]))
        parts = [p.strip(' .') for p in re.split(r'[.,;]?\s*[—–]+\s*(?=[A-Z(“"])', body) if p.strip(' .—-')]
        parts = [p + ('.' if not p.endswith('.') else '') for p in parts]
        days[(mon, d)] = {'titulus': f'The {ORDINALS[d - 1]} Day of {MONTH_EN[mon - 1]}',
                          'elogia': parts}
    missing = [(mo + 1, dd) for mo in range(12) for dd in range(1, DAYS[mo] + 1)
               if (mo + 1, dd) not in days]
    if missing or problems:
        print('WARNING - missing days:', missing, '| ordinal disagreements:', problems)
    out_dir = repo_root / 'data' / 'editions' / 'martyrologium_romanum_1914_en_unofficial'
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
