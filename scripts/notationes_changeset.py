"""The 1630 Notationes review as a crmedr-changeset/v1 change-set, decided in the
frontend's /review, and the decisions applied back to the reviewed data that
notationes_1630.py reads (no lxml needed here, so the tests can import it).

Two operations, both written with `decision: null`:

attach_note, id "M-D|<letter>" (the note's key in the overrides file, "M-D|<letter>#2"
for the day's second note read with that letter): a note
whose place is uncertain, with `class`
  "no-mark"           its letter isn't printed in the eulogy: placed by its lemma
  "letter-differs"    its letter differs from the mark it was paired with
  "unanchored"        no phrase of the eulogy could anchor it
  "mark-without-note" a letter in a eulogy with no note found (uid
                      "M-D|<letter>|mark", the mark's key in the overrides file)
and `day`, `mark`, `lemma`, `note` (its text), `proposed` {id, after}, `texts`
(the day's eulogies, ID → 1630 text), `notes` (the day's notes: ref, mark,
lemma), `scan_page`.
  accept  as proposed (for a mark: the book has no note for it)
  edit    {id, after, mark} (for a mark: {note: <ref of the day's note it takes>})
  reject  "DROP", not a note of this day (for a mark: not a reference letter)

place_margin, id "<scan page>|<block key>" (as in layout_1630.json): a margin
note placed with `class` "doubt" (the page-image reviewer was unsure; their
comment in `reasoning`) or "no-image" (placed without the page image), with
`text`, `proposed` {note: "M-D|<letter>"} or {id}, `candidates` (the notes and
eulogies on the page), `scan_page`, `image`.
  accept  as proposed
  edit    {note} or {id}
  reject  "SKIP", not a margin note
"""

import datetime
import re
import unicodedata

SCHEMA = "crmedr-changeset/v1"
EDITION = "martyrologium_romanum_1630"
IMAGE = "https://archive.org/details/bub_gb_2pQUlbrbtAsC/page/n{}"
MISSING, NOT_A_MARK = "MISSING", "NOT A MARK"


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


def image_url(page):
    return IMAGE.format(page - 1)


def new_changeset(operations):
    return {
        "schema": SCHEMA,
        "generated_by": "scripts/notationes_1630.py",
        "generated_at": datetime.date.today().isoformat(),
        "base": {"edition": EDITION, "registry": f"data/editions/{EDITION}/footnotes.json"},
        "operations": operations,
    }


def _md_key(key):
    """Sort key for 'M-D|…': by month, day, then the rest."""
    md, _, rest = key.partition("|")
    m, d = md.split("-")
    return (int(m), int(d), rest)


def _attach(op):
    """The overrides entry (key, value) for a decided attach_note op, or an error."""
    decision, edited = op["decision"], op.get("edited") or {}
    proposed = op.get("proposed") or {}
    mark_op = op.get("class") == "mark-without-note"
    if mark_op:
        key = op.get("uid") or f"{op['id']}|mark"
        if decision == "accept":
            return key, MISSING
        if decision == "reject":
            return key, NOT_A_MARK
        ref = edited.get("note")
        if ref not in {n["ref"] for n in op.get("notes") or []}:
            return None, f"{op['id']}: edit needs `note`, one of the day's notes (got {ref!r})"
        return ref, {"id": proposed.get("id"), "after": proposed.get("after"), "mark": op["mark"]}
    key = op["id"]
    if decision == "reject":
        return key, "DROP"
    eid = edited.get("id", proposed.get("id")) if decision == "edit" else proposed.get("id")
    after = edited["after"] if decision == "edit" and "after" in edited else proposed.get("after")
    mark = edited.get("mark", op["mark"]) if decision == "edit" else op["mark"]
    texts = op.get("texts") or {}
    if eid not in texts:
        return None, f"{key}: {eid!r} is not a eulogy of the day"
    if after is not None and len(occurrences(texts[eid], after)) != 1:
        return None, f"{key}: “{after}” must occur once, as whole words, in {eid}"
    if not (isinstance(mark, str) and re.fullmatch(r"[a-z]", mark)):
        return None, f"{key}: the mark must be one letter a-z (got {mark!r})"
    return key, {"id": eid, "after": after, "mark": mark}


def _place(op):
    """(scan page, block key, the placement value) for a decided place_margin op."""
    page, _, block = op["id"].partition("|")
    decision, edited = op["decision"], op.get("edited") or {}
    if decision == "reject":
        return page, block, "SKIP"
    value = edited if decision == "edit" else op.get("proposed")
    value = {k: v for k, v in (value or {}).items() if k in ("note", "id") and v}
    refs = {c["ref"] for c in op.get("candidates") or []}
    if len(value) != 1:
        return page, block, f"{op['id']}: give either `note` or `id`"
    (ref,) = value.values()
    if decision == "edit" and ref not in refs:
        return page, block, f"{op['id']}: {ref!r} is not a note or eulogy on scan page {page}"
    return page, block, value


def apply_decisions(changeset, overrides, placement):
    """Apply the decided ops of an exported change-set in place to the overrides
    ({"M-D|letter": …}) and the placement ({page: {block key: …}}); undecided ops are
    left. Returns the number applied; raises ValueError listing every op that can't be."""
    errors, n = [], 0
    for op in changeset.get("operations", []):
        decision = op.get("decision")
        if decision is None or op.get("op") not in ("attach_note", "place_margin"):
            continue
        if decision not in ("accept", "edit", "reject"):
            errors.append(f"{op.get('id')}: unknown decision {decision!r}")
            continue
        if op["op"] == "attach_note":
            key, value = _attach(op)
            if key is None:
                errors.append(value)
                continue
            overrides[key] = value
        else:
            page, block, value = _place(op)
            if isinstance(value, str) and value != "SKIP":
                errors.append(value)
                continue
            placement.setdefault(page, {})[block] = value
        n += 1
    if errors:
        raise ValueError("decisions not applied:\n" + "\n".join(errors))
    return n


def sorted_overrides(overrides):
    return {k: overrides[k] for k in sorted(overrides, key=_md_key)}
