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

Eulogies with no counterpart in the editio altera 2004 (dropped octaves, vigils, and saints removed by the reform) are keyed by **deprecated** canonical IDs coined in the CRMEDR (`deprecated: true` there). Each aligned edition folder carries an `alignment.json` recording, per ID, the match method (`same-day`, `cross-day`, `coined-deprecated`) and the matcher score — the whole alignment is a mechanical draft for committee review.

## Editions

| Folder | Edition | Source | Quality |
| --- | --- | --- | --- |
| `martyrologium_romanum_1749/` | Benedict XIV revision, 1749 (public domain) | scan with OCR text layer, parsed mechanically | **raw, uncorrected OCR**: 365/365 days, 2,843 elogia (after merging 95 OCR continuation fragments), every day with titulus and conclusio; OCR artifacts remain in the texts. **Aligned to CRMEDR IDs** (draft, v2): 1,484 same-day + 123 cross-day matches, 1,236 coined deprecated IDs in nominative lemma form, each with a subject in the CRMEDR `i18n/la.json`; see `alignment.json`. Proofreading and alignment review welcome. |
| `martyrologium_romanum_1914_en_unofficial/` | Unofficial English translation, 1914 (public domain) | scan re-OCRed with tesseract at 300dpi (the embedded text layer had spaces stripped) | **raw, uncorrected OCR**: 365/365 days, 3,031 elogia; day assignment is sequential per month, cross-validated against fuzzy decoding of the blackletter ordinal words (zero disagreements). The `titulus` is reconstructed in clean form ("The Sixteenth Day of April") since the printed blackletter headings OCR poorly; this translation carries no Et-alibi closing formula. OCR artifacts remain (drop-cap first words of each day are often garbled). Proofreading welcome. |

Corrections are welcome as pull requests; the digitization scripts are in [`scripts/`](../../scripts/).
