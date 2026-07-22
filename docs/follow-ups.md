# Known follow-ups (post-v1 merge)

Items identified during review that this wave deliberately does **not** fix.
Each should become its own issue before being picked up.

## Registry / resolver

- **Registry monolith.** `Registry.load` reads CRMEDR + deprecated ids + i18n
  + CLBDR editions in one method with no seams for partial reloads or
  incremental updates; a large registry will pay full-reparse cost on every
  process start.
- **No promulgated-year sanity guard.** `EditionMeta.promulgated_year` is
  derived from `str(promulgated)[:4]` with no validation that the source
  string is actually year-shaped; a malformed CLBDR entry fails silently
  into a wrong int rather than a load-time error.
- **`translation_of`/predecessor/successor ids aren't cross-checked.**
  Nothing verifies these reference ids that exist in the same registry
  (dangling references would only surface as a 404 much later, at request
  time, in an unrelated part of the code).

## Store

- **Flat-sort duplication.** The day-sort key tuple (unnumbered-first, entry
  fallback to `inf`, id) is duplicated between `Registry.ids_for_day` and
  `store.parse_month_file`'s flat branch; a future ordering change is easy to
  apply to only one of the two.
- **No "first path wins" test.** `Store.__init__` does
  `self._dirs.setdefault(d.name, d)` across multiple `data_paths` base dirs,
  so the first path listed silently wins on a naming collision — there is no
  test locking in that precedence, so it could regress unnoticed.

## Catalog / discovery

- **No per-edition placement index.** `GET /elogia?edition=X` calls
  `store.placements(id)` per catalog entry, which itself scans up to 12
  months per edition; that's O(ids × editions × months) in the worst case.
  Fine at current data sizes, but should get a real index before the
  catalog grows into the thousands-of-editions range.

## Auth / authz

- **No wire-shape tests for the Zitadel/OpenFGA request bodies** beyond the
  couple of assertions embedded in the mock transports — a payload-shape
  regression (e.g. a renamed field) wouldn't necessarily be caught.
- **No authz annotation/transport-error tests** distinguishing "OpenFGA said
  no" from "OpenFGA was unreachable" from "OpenFGA said yes but for the
  wrong relation" — `Authz.check` currently fails closed uniformly, which is
  safe but under-tested at the boundary.

## Caching

- **Double-buffering the response body.** `CacheHeadersMiddleware` fully
  buffers the streamed body to compute the ETag; fine for these payload
  sizes, but worth revisiting if large month/catalog payloads become common.
- **`If-None-Match` doesn't handle list values or weak (`W/`) validators** —
  only a single strong-ETag exact match is checked.
- **`response.background` is dropped.** The middleware constructs a new
  `Response` from the buffered body and never copies over
  `response.background`, so any background task attached by a handler would
  silently never run.

## Curation backends

- **LocalGitBackend has a TOCTOU window** between `ensure_branch`'s
  existence check and the actual `git branch` call (and similarly in
  `write_file`'s clone-then-push), which is fine for the current
  single-process test/dev usage but not safe under concurrent writers.
- **`ensure_branch` on an empty bare repo** (no commits at all, so no
  default branch to derive) is unhandled — `_default_branch` would return
  an unexpected string from `symbolic-ref`.
- **`_shape`'s empty-January special case and `_locate`'s scan cost.**
  `_shape` probes month 1 specifically when `edition.json` is absent, which
  is a reasonable heuristic but undocumented; `_locate` scans up to 12
  months per lookup with the anchor month tried first — fine at current
  scale, same class of concern as the catalog placement index above.
- **GitHub client lifecycle.** `GitHubBackend` opens an `httpx.Client` in
  `__init__` and never closes it — no context-manager/`close()` story.
- **409 vs 422 from GitHub's contents API are conflated** into a single
  `ConflictError` with a message-agnostic `str(exc)`; a real 422 (e.g.
  malformed base64, path too long) currently reads to the caller exactly
  like a stale-sha conflict.
- **`create_edition` scaffolds via 13 sequential commits** (one for
  `edition.json`, one per month); a batch write on `VcsBackend` would make
  it one commit + one PR instead (protocol change, deferred).

## Misc

- **Secrets are plain `str`, not `SecretStr`.** `Settings.github_token`,
  `zitadel_client_secret`, etc. are plain strings, so they can end up in
  repr()/logs/tracebacks more easily than if they were `pydantic.SecretStr`.
- **`X-Curation-Branch` is not honored on `GET /elogium/{id}`** — only on
  `GET /elogia/...`. This is a documented limitation, not a bug, but it
  means a curator reviewing a draft cross-edition placement via the
  by-canonical-id endpoint always sees published data.
