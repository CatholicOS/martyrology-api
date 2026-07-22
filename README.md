# Roman Martyrology API

A companion API to the [Liturgical Calendar API](https://github.com/Liturgical-Calendar/LiturgicalCalendarAPI), serving the eulogies (elogia) of the Roman Martyrology for any given liturgical day — a project of the [Catholic Digital Commons Foundation](https://github.com/CatholicOS), curated by a **CDCF Project Committee**. (The canonicalized identifiers and data standards this API builds on — [CRMEDR](https://github.com/CatholicOS/crmedr), [CLBDR](https://github.com/CatholicOS/clbdr), [CECDR](https://github.com/CatholicOS/cecdr), [CICLSALDR](https://github.com/CatholicOS/ciclsaldr) — are curated separately by the Catholic Engineering Task Force.)

## The copyright problem, and the architecture that solves it

Unlike the Liturgical Calendar API, which serves no copyrighted texts, a Martyrology API must serve eulogy texts — and the current texts are copyrighted (the Latin editio typica altera 2004 by the Dicastery for Divine Worship, the Italian edition by the CEI). This repository is therefore **public and contains no copyrighted texts**; the texts live in a **private data repository** ([`CatholicOS/martyrology-texts`](https://github.com/CatholicOS/martyrology-texts)) that is attached only at deployment time.

What makes the split workable:

1. **Canonical IDs are the contract.** The API's data model is keyed by the public canonical IDs of the [CRMEDR](https://github.com/CatholicOS/crmedr) (`mr:MMDD-slug`). Everything except the text itself — placement, entry numbers, asterisks, countries, unnumbered-header status — is public registry data.
2. **Public-domain editions as open sample data.** Older editions of the Roman Martyrology are out of copyright — the 1914 editio typica (Latin), the 1749 Benedict XIV edition, and the old English translations. Digitized under the same data contract, they let anyone clone, run and develop the API with real data, and can even be *served* publicly as historical editions. Only the 2004-family texts require the private repository.
3. **The frontend and curation tools stay public.** A curation website (review of draft IDs, cross-edition comparison, proper-eulogy management) contains no texts at rest; it displays what the API serves to authenticated curators. Authentication can reuse the CDCF shared infrastructure (Zitadel / OpenFGA, see [cdcf-infra](https://github.com/CatholicOS/cdcf-infra)).

See [docs/architecture.md](docs/architecture.md) for the data contract, the deployment pattern for the private data, and the API surface sketch.

## Status

Design phase. The data layer exists (CRMEDR registry public; texts extracted and keyed privately); the API surface and implementation stack are being defined. Contributions and discussion are welcome on the issues.

## Running the API

```bash
pip install -e '.[dev]'
cp .env.example .env        # defaults serve the public-domain editions
uvicorn martyrology_api.app:create_app --factory --reload
# then e.g.:
#   GET http://localhost:8000/api/v1/editions
#   GET http://localhost:8000/api/v1/elogia/edition/martyrologium_romanum_1749/01/02
#   docs at http://localhost:8000/docs
pytest                       # runs against tests/fixtures; real-data smoke tests
                             # activate when ../crmedr and ../clbdr are checked out
```

Note: the bare `/elogia/01/01` path resolves (by default) to the 2004 editio typica altera, which is not attached in a public-only clone and so returns an honest 404 — use an explicit `edition/martyrologium_romanum_1749/...` path or a year path (e.g. `1970/01/01`) to reach the public-domain sample editions instead.

The API surface, response model, auth and curation design are specified in
[docs/superpowers/specs/2026-07-22-martyrology-api-v1-design.md](docs/superpowers/specs/2026-07-22-martyrology-api-v1-design.md).

## Licensing

The code in this repository is licensed under Apache-2.0. The eulogy texts of the 2004 editions are **not** part of this repository and are not redistributable; should an agreement with the rights holders be reached, texts could be served publicly without changing this architecture.
