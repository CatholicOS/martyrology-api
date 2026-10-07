# Public-domain editions — open sample data

Digitized texts of public-domain editions of the Roman Martyrology, serving as the open sample data of this API (see [docs/architecture.md](../../docs/architecture.md)): anyone can run and develop the API against these editions, and they are publicly servable as historical editions in their own right.

Edition folder names are [CLBDR](https://github.com/CatholicOS/clbdr) edition IDs.

## Data shape

One file per month; each day carries the printed day heading (`titulus`), the eulogies (`elogia`), and the daily closing formula (`conclusio`). Where an edition has been **aligned** to the [CRMEDR](https://github.com/CatholicOS/crmedr), `elogia` is an object keyed by canonical ID (insertion order = printed order); unaligned editions still carry arrays:

```json
{
  "12": {
    "titulus": "12 Aprilis Pridie Idus Aprilis. xiij. G",
    "elogia": {
      "mr:0412-zeno": "Veronae passio sancti Zenonis Episcopi, qui …",
      "mr:0412-sabas-gothus": "In Cappadocia sancti Sabae Gothi, qui …"
    },
    "conclusio": "Et alibi aliorum plurimorum sanctorum Martyrum et Confessorum, atque sanctarum Virginum. R. Deo gratias."
  }
}
```

Eulogies with no counterpart in the editio altera 2004 (dropped octaves, vigils, and saints removed by the reform) are keyed by **deprecated** canonical IDs coined in the CRMEDR (`deprecated: true` there). Each aligned edition folder carries an `alignment.json` recording, per ID, the match method (`same-day`, `cross-day`, `override`, `coined-deprecated`) and the matcher score — the whole alignment is a mechanical draft for committee review.

These historical prints number no eulogies, so the API returns `entry: null` for every eulogy of a day-structured edition; the order of `elogia` is the printed order.

## Rubrics

A day may carry `rubricae`: the rubrics the print sets among its eulogies, which are instructions to the reader, not eulogies (CatholicOS/crmedr#51). Each has the eulogy it follows as `after`, or null when it opens the day:

```json
"rubricae": [
  { "after": "mr:1225-nativitas-domini", "text": "Quod sequitur, legitur in tono Lectionis consueto; et surgunt omnes." }
]
```

`after` must be a key of the same day's `elogia`. A rubric printed inside a eulogy's sentence (*Hic vox elevatur…*) stays in the eulogy's text. The API returns a day's rubrics as `rubricae`. A single eulogy's view returns only the rubrics that follow it.

## Source

Each edition folder may carry a `source.json` describing the printed copy its texts were taken from; `GET /editions` returns it as `source` (null without one):

```json
{
  "title": "The title as printed on the title page",
  "imprint": "Place: Publisher, year",
  "year": 1916,
  "rights": "The copyright line, or the rights status",
  "isbn": null,
  "note": "Provenance: which copy, how it was digitized"
}
```

Only `title` is required.

## Footnotes

An edition folder may carry a `footnotes.json`: the footnotes the print sets under its eulogies, by canonical ID, in printed order:

```json
{
  "mr:0115-ioannes-baptista-triquerie-et-socii": [
    { "mark": "1", "after": "sociorum,", "text": "Quorum nomina: …" }
  ]
}
```

`mark` is the printed mark as a string (the 2004 Latin numbers its notes from 1 each month; the 1914 English uses asterisks; the 1630 Latin, Baronius's reference letters; his notes' `text` starts with the lemma, without the letter, which is the `mark`: `"mark": "a", "text": "DOMINICI.] Huius…"`). `after` is the phrase the mark follows, which occurs exactly once in the eulogy's text as whole words, or null when it couldn't be anchored (readers then set the mark at the end of the eulogy). The API returns them as each eulogy's `footnotes`, emptied wherever the text is redacted.

## Marginalia

An edition folder may carry a `marginalia.json`: the notes the print sets in the margin beside its eulogies or beside their footnotes, by canonical ID, in printed order:

```json
{
  "mr:0804-dominicus": [
    { "text": "To. 12. An. 1170 n. 62.", "note": "a" }
  ]
}
```

`text` is the margin note as printed. `note` is the `mark` of the eulogy's footnote it stands beside, or null when it stands beside the eulogy's own text. The API returns them as each eulogy's `marginalia`, emptied wherever the text is redacted.

## Editions

| Folder | Edition | Source | Quality |
| --- | --- | --- | --- |
| `martyrologium_romanum_1630/` | Urban VIII revision, 1630, with Baronius's Notationes (public domain) | Romae, Typis Vaticanis, 1630 (Internet Archive `bub_gb_2pQUlbrbtAsC`); OCR to TEI, then proofread against the page images in two passes (proofread + audit): `data/sources/martyrologium_romanum_1630.tei.xml` (the whole book, notes and indices included); digitized by `scripts/digitize_1630.py` | **proofread**: 365/365 days, 2,813 elogia; long s normalized to s, otherwise as printed (u/v, i/j, æ/œ, accents, abbreviation tildes, the printer's own misprints); Baronius's reference letters removed from the eulogies; his Notationes are in `footnotes.json` (3,052 notes, each keyed to the eulogy and word its letter follows, or for a note on the day heading to the day's first eulogy) and the margin notes beside them (mostly references to his *Annales*) in `marginalia.json` (2,410), both built by `scripts/notationes_1630.py`: the notes' printed order from where `scripts/layout_1630.py` found them on the page images, each margin note placed from the page image (`scripts/data/marginalia_1630_placement.json`); open questions in `scripts/data/notationes_1630_review.md`; the closing formula, printed once on 1 January (*Sic semper terminatur lectio Martyrologij*), is given for every day. **Aligned to CRMEDR IDs** (draft) by `scripts/align_1630_ids.py`: 2,771 same-day matches (an ID of the day, through its 1749/2004 Latin text or its name; an ID's name must be in the text), 42 reviewed overrides (`scripts/data/align_1630_overrides.json`: spelling variants and other names of the same subject, such as Peregrinus for `mr:0613-cetheus`), with 6 deprecated IDs coined for it in the CRMEDR (`attested_in: martyrologium_romanum_1630`, CatholicOS/crmedr#72); see `alignment.json`. |
| `martyrologium_romanum_1749/` | Benedict XIV revision, 1749 (public domain) | a 2013 retyping (Eichstätt) of the 1913 Mechelen printing, which follows Leo XIII's 1902 edition and so includes later eulogies; the PDF's text layer, parsed mechanically | **raw, uncorrected OCR**: 365/365 days, 2,842 elogia (after merging OCR continuation fragments), every day with titulus and conclusio; OCR artifacts remain in the texts. **Aligned to CRMEDR IDs** (draft, v2): 1,495 same-day + 128 cross-day matches, 1,219 coined deprecated IDs in nominative lemma form (multi-martyr eulogies keyed by first-named subject with `-et-…`/`-et-socii`; only anonymous groups keep `martyres-<place>`), each with a subject in the CRMEDR `i18n/la.json`; see `alignment.json`. Proofreading and alignment review welcome. |
| `martyrologium_romanum_1914_en_unofficial/` | Unofficial English translation, 1914 (public domain) | scan re-OCRed with tesseract at 300dpi (the embedded text layer had spaces stripped) | **raw, uncorrected OCR**: 365/365 days, 3,031 elogia; day assignment is sequential per month, cross-validated against fuzzy decoding of the blackletter ordinal words (zero disagreements). The `titulus` is reconstructed in clean form ("The Sixteenth Day of April") since the printed blackletter headings OCR poorly; this translation carries no Et-alibi closing formula. OCR artifacts remain (drop-cap first words of each day are often garbled). Proofreading welcome. |

Corrections are welcome as pull requests; the digitization scripts are in [`scripts/`](../../scripts/).
