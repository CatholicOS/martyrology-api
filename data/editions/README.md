# Public-domain editions — open sample data

Digitized texts of public-domain editions of the Roman Martyrology, serving as the open sample data of this API (see [docs/architecture.md](../../docs/architecture.md)): anyone can run and develop the API against these editions, and they are publicly servable as historical editions in their own right.

Edition folder names are [CLBDR](https://github.com/CatholicOS/clbdr) edition IDs.

## Data shape

Historical editions are **day-keyed**, not ID-keyed: aligning each historical eulogy to a [CRMEDR](https://github.com/CatholicOS/crmedr) canonical ID is committee work still to come (many entries have no 2004 counterpart). One file per month; each day carries the printed day heading (`titulus`), the eulogy paragraphs in order (`elogia`), and the daily closing formula (`conclusio`):

```json
{
  "12": {
    "titulus": "12 Aprilis Pridie Idus Aprilis. xiij. G",
    "elogia": ["Veronae passio sancti Zenonis Episcopi, qui …", "…"],
    "conclusio": "Et alibi aliorum plurimorum sanctorum Martyrum et Confessorum, atque sanctarum Virginum. R. Deo gratias."
  }
}
```

## Editions

| Folder | Edition | Source | Quality |
| --- | --- | --- | --- |
| `martyrologium_romanum_1749/` | Benedict XIV revision, 1749 (public domain) | scan with OCR text layer, parsed mechanically | **raw, uncorrected OCR**: 365/365 days, 2,938 elogia, every day with titulus and conclusio; OCR artifacts remain in the texts (stray intra-word spaces, occasional misreads). Proofreading welcome. |
| `martyrologium_romanum_1914_en_unofficial/` | Unofficial English translation, 1914 (public domain) | scan re-OCRed with tesseract at 300dpi (the embedded text layer had spaces stripped) | **raw, uncorrected OCR**: 365/365 days, 3,031 elogia; day assignment is sequential per month, cross-validated against fuzzy decoding of the blackletter ordinal words (zero disagreements). The `titulus` is reconstructed in clean form ("The Sixteenth Day of April") since the printed blackletter headings OCR poorly; this translation carries no Et-alibi closing formula. OCR artifacts remain (drop-cap first words of each day are often garbled). Proofreading welcome. |

Corrections are welcome as pull requests; the digitization scripts are in [`scripts/`](../../scripts/).
