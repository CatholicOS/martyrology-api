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

## API surface (sketch)

```
GET /martyrology/{YYYY-MM-DD}?edition=2004_la&locale=la
GET /martyrology/today?edition=cei_it
        → the day's eulogies in order: unnumbered headers first, numbered entries,
          with per-entry {id, entry, asterisk, unnumbered, text}
GET /eulogy/mr:0101-basilius?editions=2004_la,cei_it,1914_la
        → one eulogy across editions, with per-edition placement metadata
GET /editions
        → available editions and their visibility/licensing status
GET /calendars/…   (future)
        → proper-eulogy overlays per nation/diocese/institute, mirroring the
          LitCal API's calendar path structure
```

Design notes:

- **Leap day**: `/martyrology/2025-02-28` serves the four leap-day eulogies after the
  Feb 28 entries (as printed in common years); `/martyrology/2024-02-29` serves them
  under Feb 29 — both from the same four 0229-anchored IDs.
- **Cross-edition divergences** surface naturally: the CRMEDR notes (CEI-only entries,
  placement differences like mr:0610-marcus-antonius-durando) become per-edition
  presence/placement metadata, so a day's reading differs per edition exactly as the
  printed books differ.
- **Curation endpoints** (authenticated; Zitadel/OpenFGA via cdcf-infra): draft-ID
  review status, text correction proposals, proper-eulogy submission — the write-side
  of the committee workflow.

## Open questions

1. Implementation stack: PHP mirroring the LitCal API (shared hosting, shared
   conventions) vs. a lighter static-plus-functions approach; leaning PHP for
   consistency with the LitCal ecosystem.
2. Whether the public-domain 1914/1749 texts get digitized into this repository
   directly or into a public sibling data repository (`martyrology-texts-historical`).
3. Caching/versioning: editions are immutable once published, so aggressive caching
   with edition-versioned URLs is natural.
4. Whether the API also serves the CRMEDR registry itself (making it the runtime face
   of the registry) or leaves that to raw GitHub consumption.
