# Roman Martyrology API — architecture (draft)

## Principles

1. **Public code, private texts.** Everything in this repository is publishable; the
   copyrighted 2004-family texts live in the private `CatholicOS/martyrology-texts`
   repository and are attached only at deployment. The API must degrade gracefully when
   the private data is absent: public-domain editions and the public registry remain
   fully served.
2. **Canonical IDs as the data contract.** All text data, of any edition, is keyed by
   CRMEDR canonical IDs. An edition is a mapping `id → text` plus per-edition metadata
   (placement, entry number, asterisk), so editions can be added — historical,
   vernacular, proper — without schema changes.
3. **Companion, not fork, of the Liturgical Calendar API.** The LitCal API answers
   "what is celebrated today"; this API answers "what does the Martyrology read today".
   Integration points: shared national/diocesan calendar identifiers (CECDR), shared
   locales, and a combined "liturgy of the day" view where the martyrology of the day
   complements the calendar data.

## Data layers

| Layer | Repository | Visibility |
| --- | --- | --- |
| Registry (IDs, placement, asterisks, unnumbered flags, countries) | [CRMEDR](https://github.com/CatholicOS/crmedr) `data/martyrology_ids.json` | public |
| Texts, 2004 editions (la, it, unofficial en) | `martyrology-texts` `data/texts/MM.json` | **private** |
| Texts, public-domain editions (1914 la, 1749 la, old en translations) | this repository (planned, `data/editions/<edition>/MM.json`) | public |
| Proper eulogies (future: national, diocesan, religious-family propria) | per-owner data, keyed `mr:<owner>:…` via [CECDR](https://github.com/CatholicOS/cecdr) / [CICLSALDR](https://github.com/CatholicOS/ciclsaldr) | per licensing |

### Attaching the private data

The API reads text-data directories from a configurable path (`MARTYROLOGY_DATA_PATH`,
one directory per edition). Deployment options, in order of preference:

1. **Deploy-time clone** of `martyrology-texts` via a read-only deploy key or GitHub
   Actions secret, into a directory outside the public repo working tree;
2. **Git submodule** referencing the private repository (clones without access simply
   skip it — the API detects absence and serves public editions only);
3. a database loaded from the private repo by a migration script (if/when the API
   outgrows flat files).

Option 1 is the recommended default: no submodule friction for public contributors,
no risk of accidentally vendoring private content into the public tree.

## Edition resolution: serving the right texts per date and territory

A request for the martyrology of a *historical* date should serve the texts that were
actually in force on that date, in the place that matters to the requester: January 1st
1750 reads from the 1749 Benedict XIV edition; January 1st 1920 reads from the
1913/1914 typical edition (in English, from its contemporary translation); January 1st
2025 in Italy reads from the CEI 2004 edition.

The resolution rule is simple and mechanical once editions are first-class data:

1. every edition carries a **promulgation date** (from its decree) and an implicit
   **in-force window** — from promulgation until superseded by its successor for the
   same scope;
2. a request context is **(date, territory, locale)**; territory defaults to the
   universal Latin Church, and may be a nation (ISO 3166-1) or, in future, a
   circumscription or institute (CECDR / CICLSALDR keys) with a proprium;
3. the API picks the newest edition in scope whose promulgation precedes the requested
   date; explicit `?edition=` always overrides. Requests before the first typical
   edition (1584, Gregory XIII) return the editions list with an explanatory error.

Because every edition's texts are keyed by the same CRMEDR canonical IDs, resolution
changes *which texts and which day-structure* are served, never the identity model:
entries present in one edition and absent in another (already modeled in the registry
notes) simply appear or not, exactly as the printed books differ.

One historical liturgical nuance, recorded for later: before the 1970 reforms the
martyrology was read at Prime *pridie* — the following day's entries were announced.
Whether a historical-date endpoint should optionally reflect that reading practice
(as opposed to the day's own entries) is left as a presentation-layer question.

### The editions registry

Edition identity must live in its own public registry, not in this API. The natural
scheme extends the keys the CLEDR already uses for the Missal
(`missale_romanum_1970`, `missale_romanum_2002`, …):

```
martyrologium_romanum_1584    Gregory XIII, first typical edition
martyrologium_romanum_1749    Benedict XIV
martyrologium_romanum_1914    Pius X era typical edition
martyrologium_romanum_2001    editio typica (prima)
martyrologium_romanum_2004    editio typica altera  ← CRMEDR anchor
martyrologium_romanum_cei_2004        approved vernacular edition (scope: IT)
martyrologium_romanum_en_1916_unofficial   translation, not an edition (attribute-flagged)
```

with attributes: book, year, nature (typical edition / revised typical edition /
approved vernacular edition / translation), scope (universal or territory), locale,
promulgation decree and date, predecessor/successor.

**The registry exists**: the [Common Liturgical Books Data Repository
(CLBDR)](https://github.com/CatholicOS/clbdr) — a unified registry of all the
liturgical books of the Roman Rite and their editions, which absorbed the former
CRMETDR (whose Missal edition keys are preserved unchanged) and dissolved the
per-book acronym collisions. The martyrology edition line above, with natures,
promulgation data, succession and the vernacular BCP47 pattern
(`martyrologium_romanum_2004_it_IT`), lives there; this API resolves editions against
it.

## API surface

Superseded by the approved design spec:
[superpowers/specs/2026-07-22-martyrology-api-v1-design.md](superpowers/specs/2026-07-22-martyrology-api-v1-design.md)
(implemented in `src/martyrology_api/`).

## Open questions

1. Implementation stack: decided — Python 3.12 + FastAPI (see the design spec,
   [superpowers/specs/2026-07-22-martyrology-api-v1-design.md](superpowers/specs/2026-07-22-martyrology-api-v1-design.md)).
2. Whether the public-domain 1914/1749 texts get digitized into this repository
   directly or into a public sibling data repository (`martyrology-texts-historical`).
   Feasibility notes from the source PDFs: the 1749 Latin scan (539 pp.) carries a
   clean OCR text layer with day headers intact; the 1914 English scan (488 pp.) has a
   text layer with spaces stripped (words run together), so it needs re-OCR or word
   re-segmentation. Digitized historical texts will need alignment to CRMEDR IDs where
   an eulogy continues across editions, and edition-local entries (saints later removed,
   pre-reform structure) will pose identity questions for the committee.
3. Caching/versioning: editions are immutable once published, so aggressive caching
   with edition-versioned URLs is natural.
4. Whether the API also serves the CRMEDR registry itself (making it the runtime face
   of the registry) or leaves that to raw GitHub consumption.
