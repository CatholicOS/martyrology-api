# Roman Martyrology API v1 — design spec

**Date:** 2026-07-22
**Status:** approved design, pre-implementation
**Supersedes:** the "API surface (sketch)" section of [docs/architecture.md](../../architecture.md); all other sections of that document (principles, data layers, edition resolution, editions registry) remain in force and are assumed here.

## Goal

A REST API serving the eulogies (elogia) of the Roman Martyrology — current and historical editions, universal and national — alongside the Liturgical Calendar API, with a comparable governance model. Reads are public for public-domain editions and the registry; the copyrighted 2004-family texts require an approved API key. Curation (creating and editing editions) is authenticated and lands as reviewable git commits.

## Decisions made (with rationale)

1. **Edition selection = resolvers + explicit override.** Human-friendly positional paths (`nation/IT/1970/01/02`) resolve to the newest in-scope edition promulgated ≤ the given year; an explicit edition-addressed form (`edition/{CLBDR-id}/…`) is canonical, unambiguous, and cacheable forever since editions are immutable. Responses always declare what was resolved.
2. **Day paths resolve by printed placement; a separate ID endpoint is edition-independent.** `/{MM}/{DD}/{slug}` answers "what does this book read this day" (matching the printed page, including cross-day placements — the 1749 alignment has 128 of them); `/elogium/{canonical_id}` answers "show me this eulogy across editions".
3. **Writes are git-backed via branches/PRs.** The API commits authenticated writes to the appropriate data repository (public editions in this repo, 2004-family in the private `martyrology-texts`) on a curation branch; committee review is PR review; merge = publish. Git is the audit log.
4. **API keys are Zitadel service-user PATs.** One identity system (shared cdcf-infra Zitadel) for humans (OIDC) and machines (service users); the API validates Bearer tokens by introspection (cached) and authorizes via OpenFGA relations.
5. **Stack: Python 3.12 + FastAPI.** The repo's tooling (`scripts/`) is already Python; pydantic models double as the response contract and generate OpenAPI docs; OpenFGA has an official Python SDK; Zitadel introspection is plain HTTP.
6. **Licensed texts are redacted, not 401'd.** The skeleton of a 2004 day (IDs, entry numbers, asterisks, placement) is public CRMEDR registry data; only texts are copyrighted. Unauthenticated requests get `200` with `text: null` and `metadata.access: "restricted-texts"`.
7. **DELETE is part of the curation surface.** Removing an eulogy *from an edition* (misalignment, OCR ghost) is a normal curation act and lands as a reviewable commit.

## 1. Read surface

Base prefix `/api/v1`. Path grammar after the prefix: `[scope] [year] [date…]` where

- **scope** is `nation/{ISO 3166-1 alpha-2}`, or `edition/{CLBDR edition id}`, or absent (universal Latin Church);
- **year** is a single 4-digit segment (edition-resolution input, only meaningful with a resolver scope — never with `edition/`);
- **date** is `{MM}` or `{MM}/{DD}` or `{MM}/{DD}/{slug}`, 2-digit month/day.

Segment disambiguation is mechanical: 4 digits = year, 2 digits = month.

```
GET /api/v1/editions                                    discovery
GET /api/v1/elogia                                      catalog of canonical IDs
GET /api/v1/elogia/{MM}                                 whole month, universal latest (2004 editio typica altera)
GET /api/v1/elogia/{MM}/{DD}                            one day
GET /api/v1/elogia/{MM}/{DD}/{slug}                     one eulogy as placed that day
GET /api/v1/elogia/{YYYY}/{MM}[/{DD}[/{slug}]]          universal, edition in force ≤ YYYY
GET /api/v1/elogia/nation/{ISO}/[{YYYY}/]{MM}[/{DD}[/{slug}]]
GET /api/v1/elogia/edition/{edition_id}/{MM}[/{DD}[/{slug}]]
GET /api/v1/elogium/{canonical_id}                      one eulogy across editions
```

### Query parameters

- `locale` — resolver input alongside nation/year (BCP47, shared convention with LitCal). Default derived from `Accept-Language`, falling back to the resolved edition's own locale. Example: Latin in Italy = `nation/IT/01/02?locale=la` (resolves to the Latin editio typica rather than the CEI edition).
- `edition` — query-string equivalent of the `edition/` path form; always overrides resolution.
- `editions` — on `/elogium/{id}` only: comma-separated CLBDR ids to include (default: all editions in which the ID is present).

### Resolution rules

As in architecture.md: pick the newest edition whose scope covers the request context and whose promulgation date precedes the requested year; territory defaults to universal; explicit `edition` always wins. Requests resolving before 1584 (first typical edition) return a `404` problem document that lists `/editions` as the remedy. Unknown nation, month, day, slug, or edition id → `404` problem document.

### Single-eulogy semantics

- `/{MM}/{DD}/{slug}` looks up the eulogy **as placed** on that day in the resolved edition. The slug is the ID minus the `mr:MMDD-` prefix. For cross-day placements, the path day is the *printed* day (e.g. `edition/martyrologium_romanum_1749/01/01/concordius` serves `mr:0102-concordius` printed under Jan 1 in that book); the response's `anchor_day` field carries the canonical anchor.
- `/elogium/{canonical_id}` takes the full `mr:MMDD-slug` ID (URL-encoded `:` accepted literally) and is edition-independent.

### Leap day

`/elogia/02/29` serves the four 0229-anchored eulogies; `/elogia/02/28` serves Feb 28 only. Clients rendering a common year append the 02/29 entries after 02/28, as printed in the books. Documented behavior, no magic.

## 2. Response model

### Day

```json
{
  "metadata": {
    "edition": "martyrologium_romanum_1749",
    "edition_metadata": {
      "book": "martyrologium_romanum",
      "year": 1749,
      "nature": "revised_typical_edition",
      "scope": "universal",
      "locale": "la",
      "promulgation": { "decree": "…", "date": "1749-…" }
    },
    "resolved_from": { "nation": "IT", "year": 1970, "locale": "it" },
    "month": 1,
    "day": 2,
    "access": "public"
  },
  "titulus": "…printed day heading…",
  "elogia": [
    {
      "id": "mr:0102-concordius",
      "entry": 1,
      "asterisk": false,
      "unnumbered": false,
      "anchor_day": "01-02",
      "text": "…"
    }
  ],
  "conclusio": "…daily closing formula, null if the edition has none…"
}
```

- `elogia` is an **array in printed order** (arrays make order explicit for JSON clients; the flat-file keyed-object shape stays as-is on disk).
- `resolved_from` is present only when a resolver (not `edition/`) chose the edition; it echoes the request context.
- `metadata.access` is `"public"` or `"restricted-texts"` (see §4).

### Month

```json
{ "metadata": { …as above, "month": 1, "day": null… },
  "days": { "01": { "titulus": …, "elogia": […], "conclusio": … }, "02": …, … } }
```

### Single eulogy (day-scoped path)

The day response reduced to one element of `elogia`, same `metadata` envelope.

### Cross-edition eulogy (`/elogium/{id}`)

```json
{
  "id": "mr:0102-concordius",
  "subject": { "la": "Concordius", "it": "…", "…": "…" },
  "anchor_day": "01-02",
  "deprecated": false,
  "editions": {
    "martyrologium_romanum_2004": { "day_printed": "01-02", "entry": 3, "asterisk": false, "unnumbered": false, "text": "…" },
    "martyrologium_romanum_1749": { "day_printed": "01-01", "entry": 7, "asterisk": false, "unnumbered": false, "text": "…" }
  }
}
```

`subject` comes from CRMEDR `i18n/*.json`; editions where the ID is absent are simply not listed.

### Catalog (`/elogia`)

Array of `{ id, subject, anchor_day, deprecated }` from the CRMEDR registry. With `?edition=`, adds per-ID `{ present, day_printed, entry }` for that edition — analogous to LitCal's `/events`, and like it, the catalog varies slightly per edition.

### Discovery (`/editions`)

```json
{
  "editions": [
    {
      "edition_id": "martyrologium_romanum_2004_it_IT",
      "book": "martyrologium_romanum",
      "year": 2004,
      "nature": "approved_vernacular_edition",
      "scope": { "type": "nation", "nation": "IT" },
      "locale": "it-IT",
      "promulgation": { "decree": "…", "date": "…" },
      "predecessor": "…", "successor": null,
      "governance": { "governing_body": "Conferenza Episcopale Italiana", "type": "bishops_conference", "nation": "IT" },
      "availability": { "status": "restricted-texts", "note": "© CEI; texts require an approved API key" }
    }
  ]
}
```

`availability.status` ∈ `public` | `restricted-texts` | `unavailable` (registered in CLBDR but no texts attached in this deployment). `governance` here is the **legal** governance (Dicastery, conference) — metadata for display, distinct from operational curation rights (§4).

### Errors

RFC 9457 `application/problem+json` throughout: `400` malformed grammar, `404` unknown resource / pre-1584 resolution (with an `editions` link in the problem document), `401` invalid/expired token on any request that presents one (an *absent* token on a restricted read is not an error — it yields the redacted `200` of §3), `403` FGA-denied write, `422` invalid write payload, `409` write conflict (branch diverged).

## 3. Auth & authorization

### Authentication (Zitadel, shared cdcf-infra instance)

- **Humans** (curators, reviewers): OIDC login on the curation frontend; the API receives their access token as `Authorization: Bearer`.
- **Machines** (approved consumers of licensed texts): a Zitadel **service user** per consumer; its personal access token is the API key, sent as `Authorization: Bearer`. No separate key store: issuance, revocation, and audit live in Zitadel.
- The API validates tokens via Zitadel's introspection endpoint with a short-lived cache (~5 min); anonymous requests skip introspection entirely.

### Authorization (OpenFGA)

```
model
  schema 1.1

type user

type governing_body            # operational committees: committee:universal, committee:IT, …
  relations
    define admin:    [user]
    define curator:  [user] or admin
    define reviewer: [user] or admin

type edition
  relations
    define governed_by:       [governing_body]
    define restricted_reader: [user]              # API-key consumers of licensed texts
    define can_read_texts: restricted_reader or curator from governed_by or admin from governed_by
    define can_edit:   curator from governed_by
    define can_review: reviewer from governed_by
    define can_admin:  admin from governed_by
```

- Public-domain editions never trigger a `can_read_texts` check.
- Approving an API key = one tuple: `(user:svc-<consumer>, restricted_reader, edition:martyrologium_romanum_2004)` (and siblings per licensed edition).
- Delegating a national edition to its committee = one tuple: `(governing_body:committee-IT, governed_by, edition:martyrologium_romanum_2004_it_IT)`.
- Legal governance (Dicastery, CEI) is *metadata* in `/editions`; OpenFGA holds only *operational* CDCF-committee rights.

### Licensed-text redaction

For editions whose `availability.status` is `restricted-texts`, a request without a valid `can_read_texts` grant returns **`200`** with every `text: null`, `metadata.access: "restricted-texts"`, and a `metadata.access_info` URL describing how to request a key. Structure, IDs, entry numbers, asterisks, and placement are served — they are public registry data. Authorized responses carry full texts with `Cache-Control: private`.

## 4. Write surface (curation)

All writes require a Bearer token and the FGA relation noted; all writes validate then commit to the appropriate data repository.

```
PUT    /api/v1/editions/{edition_id}                  create edition (metadata + empty month files)   can_admin
PATCH  /api/v1/editions/{edition_id}                  edit edition metadata                           can_admin
PUT    /api/v1/editions/{edition_id}/{MM}             replace a month file (bulk digitization)        can_edit
PATCH  /api/v1/editions/{edition_id}/{MM}/{DD}        edit a day (titulus, conclusio, order)          can_edit
PUT    /api/v1/editions/{edition_id}/elogia/{id}      create/replace one eulogy text                  can_edit
PATCH  /api/v1/editions/{edition_id}/elogia/{id}      partial correction                              can_edit
DELETE /api/v1/editions/{edition_id}/elogia/{id}      remove eulogy from this edition                 can_edit
```

### Mechanics

- In production deployments, writes go through the **GitHub REST API** (contents + pulls endpoints) — no server-side git working tree. Public-domain editions target this repository; 2004-family editions target the private `martyrology-texts` repository. The server holds a deploy token with rights on both; OpenFGA decides who may trigger which write. Development and tests use the `LocalGitBackend` over local bare repositories instead (§5).
- Each write lands as a commit on branch `curation/{zitadel-username}/{topic}` (created on first write, `topic` supplied by the client or defaulted to the date). Commits carry the curator's name/email from their Zitadel profile as author.
- Write responses return `{ branch, commit_sha, pr_url }`; a PR is opened lazily on first commit to a branch. **Merge = publish**: deployments sync merged data; the live API never serves unmerged drafts by default.
- A curator can read their own draft state back by sending `X-Curation-Branch: curation/<user>/<topic>` on any read endpoint (requires `can_edit` on the edition; ignored otherwise).
- `PUT /editions/{edition_id}` requires the id to already exist in the CLBDR — edition *identity* is registered there first; this API creates the *texts* skeleton.

### Validation

- Month files validate against the JSON schema of the data shape (titulus / elogia / conclusio per day, `data/editions/README.md`).
- Eulogy IDs must exist in the CRMEDR (including its deprecated IDs). Coining a *new* ID remains a registry-side act in CRMEDR, as today — the API rejects unknown IDs with `422` and a pointer to the registry workflow.
- Day placement writes must keep each ID unique within the edition.
- `409` when the target branch has diverged from the client's last-seen state (`If-Match` with the file's blob SHA, per the GitHub contents API).

## 5. Implementation shape

**Stack:** Python 3.12, FastAPI, pydantic v2 models (single source of truth for response shapes and generated OpenAPI docs), httpx for Zitadel introspection, `openfga-sdk` for authz, GitHub REST via httpx.

**Modules** (each independently testable, communicating through typed interfaces):

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `registry` | Load CRMEDR IDs/subjects and CLBDR edition metadata | flat files / pinned fetch |
| `store` | Read edition month files from `MARTYROLOGY_DATA_PATH` (one dir per edition), in-memory index; degrades gracefully when private editions are absent | `registry` |
| `resolver` | (scope, year, locale) → edition id | `registry` |
| `auth` | Bearer validation via Zitadel introspection, cached | — |
| `authz` | OpenFGA check wrapper | `auth` |
| `licensing` | Redaction of restricted texts per §3 | `authz`, `registry` |
| `writer` | VCS backend interface: `GitHubBackend` (prod), `LocalGitBackend` (dev/tests against a local bare repo) | `authz` |
| `api` | Thin FastAPI routers: read, discovery, curation | all of the above |

**Caching:**

- `edition/…`-addressed GETs: `Cache-Control: public, max-age=31536000, immutable` + ETag (editions are immutable once published).
- Resolver-addressed GETs: `Cache-Control: public, max-age=86400` + ETag (resolution changes only when a new edition is promulgated).
- Restricted texts (authorized): `Cache-Control: private`.

**Configuration:** `MARTYROLOGY_DATA_PATH`, Zitadel issuer/client credentials, OpenFGA store/model ids, GitHub tokens — all env vars; every external service optional in dev (absence = public-only, read-only mode).

## 6. Testing

- **Golden-file tests** over the two digitized public-domain editions (1749, 1914-en): known days in, exact JSON out.
- **Grammar contract tests**: the `[scope][year][date]` parser, including 4-vs-2-digit disambiguation, bad segments, leap day.
- **Resolver tests**: (nation, year, locale) matrices against a fixture CLBDR registry, incl. pre-1584.
- **Authz tests**: OpenFGA checks against a local test store (official Docker image) in CI, a stub in unit tests; redaction behavior for anonymous / keyed / curator identities.
- **Write-path tests**: `LocalGitBackend` against a temp bare repo — branch creation, commit authorship, conflict (`409`) behavior — no network, no GitHub, no Zitadel needed to develop.

## Out of scope for v1 (recorded, deliberate)

- Proper-eulogy overlays per nation/diocese/institute (CECDR/CICLSALDR keys) — the edition model already accommodates them as future editions/overlays.
- A combined "liturgy of the day" view with LitCal — response shapes are designed so LitCal can embed a day's elogia later.
- The pre-1970 *pridie* reading practice (martyrology read at Prime for the following day) — presentation-layer, per architecture.md.
- Serving the CRMEDR registry itself beyond the `/elogia` catalog.
- Draft review states beyond PR semantics (the git/PR workflow is the review system).
