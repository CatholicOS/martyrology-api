# Local development stack — design

**Date:** 2026-08-04
**Repos in scope:** `CatholicOS/martyrology-api`, `CatholicOS/martyrology-frontend`
**Reference implementation:** `Liturgical-Calendar/LiturgicalCalendarAPI` + `LiturgicalCalendarFrontend` docker stacks
**Production this mirrors:** `CatholicOS/cdcf-infra` → `auth/docker-compose.prod.yml`

---

## 1. Problem

Martyrology has no local identity or authorization infrastructure. Every flow that
touches Zitadel or OpenFGA is currently verifiable only against production, and two
concrete things are blocked by that:

1. **Tasks 3–7 of `cdcf-infra`'s `2026-08-03-martyrology-oidc-login-client` plan are
   flagged stale.** Six verification steps assume a localhost OIDC client that
   deliberately does not exist in the production Zitadel (the plan's D3). The plan
   itself names the resolution: *"verify against the local stack once it exists."*

2. **`martyrology-api`'s local `.env` disables its own licensing model to stay
   usable.** It sets `MARTYROLOGY_RESTRICTED_EDITIONS=` with the comment: *"with no
   Zitadel/OpenFGA configured, authz fails closed and you could never see the texts
   you legitimately hold."* The workaround is correct given no local authz, but it
   means the redaction path is never exercised outside production.

A local stack removes both. It also gives the forthcoming permission-request and
notification subsystem (§9) somewhere to run.

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | The **full** stack mirrors `cdcf-infra` production topology: single origin behind an nginx proxy, image versions pinned to production's. | The stack exists to verify an OIDC flow that will run against `cdcf-infra`. Redirect URIs, issuer discovery and CSP are exactly what differs between a one-origin and a two-origin issuer; a local pass under a different topology would prove less than it appears to. |
| **D2** | The **minimal** stack omits `zitadel-login` and the proxy, and serves Zitadel directly on `:8080` with `LOGINV2_REQUIRED: false`. | Nothing in the API repo performs an interactive browser sign-in; the API only ever calls `/oauth/v2/introspect`. A login UI with no frontend to log into is inventory without a purpose. This is a deliberate, documented divergence from D1, not an oversight. |
| **D3** | The OpenFGA model and tuples come from a **clone of `cdcf-infra`** performed by a one-shot `authz-seed` service; the local override bind-mounts `../cdcf-infra` instead. | `auth/models/Martyrology{,.tuples}.json` is the authoritative copy that production uploads. Vendoring a second copy into either repo would drift silently. `cdcf-infra` is public, so the default path works from a bare clone with no siblings. |
| **D4** | `martyrology-api` gains a `Dockerfile`, used by the **frontend's** stack and CI but **not** by the API's own stack. The API dev loop stays `uvicorn --factory --reload` on the host. | LitCal's precedent: its minimal stack is infra-only and the API runs on the host via `composer start`. Keeps the Dockerfile out of the everyday API edit loop. |
| **D5** | Ports reuse LitCal's numbers (`5432`, `8080`, `8083/8084`, `3001`, `8088`, `8025`, `8000`, `3000`). | The two compose files read almost line-for-line the same, which is the point of mirroring. Consequence: only one stack runs at a time. |
| **D6** | Generated IDs reach the containers via LitCal's **two-phase `--update-env`**: `up` → provision → `up --force-recreate`. | The store ID, model ID, client IDs and client secrets are all generated at provisioning time. A `.env` file is inspectable when something is wrong, which matters when one of the values is a one-time-emit secret. |
| **D7** | `martyrology-api` gains **`MARTYROLOGY_ZITADEL_INTERNAL_URL`** (empty default → falls back to `zitadel_issuer`), used for introspection only. | `localhost:8080` must stay the browser-facing issuer and the `iss` claim, but inside the API container `localhost` is its own loopback. This is LitCal's `ZITADEL_INTERNAL_URL` precedent for the API specifically, and it is useful in production too, where Plesk's nginx makes the same round trip. |
| **D8** | The API image **clones `crmedr` and `clbdr` at pinned refs** rather than `COPY vendor/`. | `app.py:27` calls `Registry.load(crmedr_path, clbdr_path)` at startup and `registry.py` reads four files from them unconditionally — the API cannot start without them. `vendor/texts` is a **private** submodule, so a recursive clone of a GitHub build context would fail for anyone without access to it. Cloning the two public repos explicitly sidesteps the question. |
| **D9** | The permission-request / notification subsystem is **a separate spec**. This design lands only its infrastructure contract: a `martyrology` database, Alembic, and an `api-migrate` one-shot service. | They share nothing but a Postgres connection. Combining them produces a spec covering two subsystems, which is the kind that gets partially implemented and then diverges from its own plan. |

### Prerequisite already satisfied

`cdcf-infra` PR #23 (merged 2026-08-04) makes `--provision-martyrology-frontend`
select its origin by `--target`: `local` → `http://localhost:3000` with
`devMode=true`, `production` → `https://romanmartyrology.com` with
`devMode=false`, `staging` → skips with a warning. No further `cdcf-infra` change
is required by this design.

**This pins the frontend container's published port to 3000.** Moving it later
requires a matching `cdcf-infra` change.

## 3. Architecture

|  | `martyrology-api/docker-compose.yml` | `martyrology-frontend/docker-compose.yml` |
|---|---|---|
| Compose project | `martyrology-infra` | `martyrology` |
| Purpose | Infra the host-run API talks to | The whole system, containerized |
| API | **absent** — `uvicorn` on the host | container, `martyrology-api:latest`, `:8000` |
| Frontend | absent | container, `:3000` |
| Zitadel | direct on `:8080`, no login v2, no proxy | behind `zitadel-proxy` on `:8080`, login v2 at `/ui/v2/login` |
| Builds from | local context only | GitHub refs by default; override repoints at siblings |

### Minimal stack services

`db` · `zitadel` · `mailpit` · `openfga-migrate` → `openfga` · `authz-seed` ·
`api-migrate` · `adminer`.

Images pinned to production's versions — `zitadel:v4.15.0`, `openfga:v1.15.1`,
`postgres:17` — not `:latest`.

- **`db`** — `scripts/init-db.sql` creates roles and databases for `zitadel`,
  `openfga` and `martyrology`.
- **`zitadel`** — `ZITADEL_EXTERNALDOMAIN: localhost`, `ZITADEL_EXTERNALPORT: 8080`,
  `ZITADEL_EXTERNALSECURE: false`, `LOGINV2_REQUIRED: false` (D2), SMTP → `mailpit`.
  Its data dir is bind-mounted to a gitignored `./.zitadel-data/`, with
  `ZITADEL_FIRSTINSTANCE_PATPATH: /zitadel-data/automation-user.pat`, so the
  host-run `setup-zitadel.sh` can read the PAT it authenticates with.
- **`authz-seed`** — one-shot `alpine` + `bash`/`curl`/`jq`/`git`. Clones
  `cdcf-infra` at `${CDCF_INFRA_REF:-main}` and runs
  `auth/setup-openfga.sh --target local`,
  which creates the `Martyrology` store, uploads the model, and seeds the eight
  `governed_by` plus three `on_platform` tuples. Idempotent by the script's own
  read-then-write-the-difference logic.
- **`api-migrate`** — `build: .`, runs `alembic upgrade head` against the
  `martyrology` database. Ships with a baseline migration and no tables; it exists
  so the subsystem in §9 lands as migrations without touching compose.

### OpenFGA must run with preshared auth — in both stacks

Not a preference. `Settings.authz_enabled` is:

```python
return bool(self.openfga_api_url and self.openfga_store_id and self.openfga_api_token)
```

An OpenFGA running `OPENFGA_AUTHN_METHOD: none` — LitCal's dev-stack default —
leaves `MARTYROLOGY_OPENFGA_API_TOKEN` empty, so `authz_enabled` is **False**, so
every `Authz.check` returns `False` and the entire stack fails closed. It would
come up healthy and verify nothing.

Both stacks therefore run OpenFGA with `OPENFGA_AUTHN_METHOD=preshared` and
`OPENFGA_AUTHN_PRESHARED_KEYS=${OPENFGA_PRESHARED_KEY}`, with the same value set
as `MARTYROLOGY_OPENFGA_API_TOKEN` and consumed by `setup-openfga.sh`, which
already requires `OPENFGA_PRESHARED_KEY` in its env file. This also matches
production, which uses a preshared key.

**To confirm at implementation:** whether the Playground can be enabled alongside
preshared auth on `v1.15.1`. If not, the Playground is dropped and the store is
inspected via `curl` — the same way production is.

### Full stack services

The minimal set plus `db-init`, `zitadel-login`, `zitadel-proxy`,
`martyrology-api`, `martyrology-frontend`.

- **`db-init`** — `image: martyrology-api:latest`, extracts `scripts/init-db.sql`
  into a volume mounted at `/docker-entrypoint-initdb.d`. One source of truth for
  the DB bootstrap, taken from the image rather than duplicated in this repo.
- **`zitadel`** publishes nothing. **`zitadel-proxy`** (`nginx:alpine`) publishes
  `127.0.0.1:8080:80` and routes `/ui/v2/login*` → `zitadel-login:3000`,
  everything else → `zitadel:8080`. `ZITADEL_EXTERNALPORT: 8080` therefore
  describes the proxy. **Port 8081 is unused in this stack.**
- **`martyrology-api`** — `MARTYROLOGY_ZITADEL_ISSUER=http://localhost:8080`,
  `MARTYROLOGY_ZITADEL_INTERNAL_URL=http://zitadel:8080` (D7),
  `MARTYROLOGY_OPENFGA_API_URL=http://openfga:8080`.
- **`martyrology-frontend`** — `extra_hosts: - "localhost:host-gateway"`, matching
  `litcal-frontend` exactly. Auth.js needs server-side discovery and the browser
  redirect to agree on one origin, so `localhost:8080` must resolve from inside
  the container to the published proxy port.

### The nginx conf is a local copy, and that has a cost

`docker/nginx/zitadel.local.conf` in `martyrology-frontend`, **not** a mount of
`cdcf-infra`'s `auth/nginx/zitadel.conf`. Production's `connect-src` allowlist
names the CDCF and LitCal origins and would block `http://localhost:3000`.

Only the CSP line differs; the routing half is stable. The local copy must say so
explicitly and cite the original, because this is a genuine second copy of a file
whose comments carry real reasoning about why the CSP is replaced rather than
appended.

## 4. Changes to `martyrology-api`

| Artifact | Notes |
|---|---|
| `Dockerfile`, `.dockerignore` | Multi-stage, `uv` install, `python:3.12-slim` runtime, `CMD uvicorn … 0.0.0.0:8000`. Clones `crmedr` and `clbdr` at pinned refs (D8). Includes `data/editions`, `scripts/init-db.sql`, `alembic/`. |
| `docker-compose.yml` | Minimal stack (§3). |
| `scripts/init-db.sql` | Roles + databases for `zitadel`, `openfga`, `martyrology`. |
| `alembic/`, `alembic.ini` | Baseline migration only. |
| `pyproject.toml` | Adds `alembic`, `sqlalchemy`, `psycopg`. |
| `config.py` | Adds `database_url` and `zitadel_internal_url`. |
| `auth.py` | Introspection targets `zitadel_internal_url or zitadel_issuer`. Token validation still asserts the public `zitadel_issuer`. |
| `.env.example` | Documents `MARTYROLOGY_DATABASE_URL` and `MARTYROLOGY_ZITADEL_INTERNAL_URL`. |
| `scripts/setup-stack.sh` | Provisioning wrapper (§6). Invokes `--create-org Martyrology --provision-martyrology` only — the minimal stack has no frontend app to provision. |
| `scripts/grant-superuser.sh` | One-shot `platform:martyrology` superuser tuple write. |
| `scripts/smoke.sh` | Bring-up invariants (§7), minimal-stack subset. |

Each repo carries **its own** `setup-stack.sh`, `grant-superuser.sh` and
`smoke.sh`. They are near-identical and deliberately not shared: each provisions
a different Zitadel instance with a different set of actions, and a shared script
would have to branch on which stack invoked it. The frontend's copies are listed
in §5.

`zitadel_internal_url` defaults to `""` and falls back to `zitadel_issuer`, so
existing deployments and the test suite are unaffected. `auth_enabled` and
`authz_enabled` are untouched — the internal URL is a transport detail, never a
posture input.

## 5. Changes to `martyrology-frontend`

`Dockerfile` (Next.js standalone output, node ≥24, `:3000`), `.dockerignore`,
`docker-compose.yml`, `docker-compose.override.example.yml`,
`docker/nginx/zitadel.local.conf`, `.env.example` additions
(`AUTH_SECRET`, `AUTH_URL`, `AUTH_ZITADEL_ID`, `AUTH_ZITADEL_SECRET`), and a
`.gitignore` entry for `docker-compose.override.yml`.

Plus its own `scripts/setup-stack.sh` (which additionally invokes
`--provision-martyrology-frontend`), `scripts/grant-superuser.sh`, and
`scripts/smoke.sh` (the full assertion set, §7).

### The override

Committed as `docker-compose.override.example.yml`, copied to a gitignored
`docker-compose.override.yml`:

- `db-init`, `api-migrate` and `martyrology-api` all get
  `build: context: ../martyrology-api` **together**. Overriding only one leaves
  `docker compose up --build` rebuilding `martyrology-api:latest` from GitHub via
  another service and clobbering the local build — the trap LitCal's override
  comments call out by name.
- `../martyrology-api/src` `:ro`; `../crmedr` and `../clbdr` `:ro` over the
  image's cloned copies.
- **`../martyrology-texts/data/editions` `:ro`**, with `MARTYROLOGY_DATA_PATH`
  extended to include it. This is what makes the restricted-texts path real. It
  works only under the override — `martyrology-texts` is private, so the
  GitHub-default stack necessarily serves the two public-domain editions only.
- `authz-seed` bind-mounts `../cdcf-infra` instead of cloning.

**Frontend iteration is not a bind mount.** A Next.js production image will not
hot-reload from one. The documented loop is `docker compose stop
martyrology-frontend` and `npm run dev` on the host: port 3000 is then free and
the registered callback still matches.

## 6. Bring-up

Run from **`martyrology-frontend/`** — this is the full stack. The minimal stack
is the same sequence run from `martyrology-api/`, minus the frontend service in
step 4 and minus step 5, followed by `uvicorn martyrology_api.app:create_app
--factory --reload` on the host.

```bash
cp .env.example .env                                    # ports, masterkey, preshared key
docker compose up -d                                    # infra; authz-seed creates + seeds the store
./scripts/setup-stack.sh --update-env                   # provisions Zitadel, writes IDs into .env
docker compose up -d --force-recreate martyrology-api martyrology-frontend
# sign in once at http://localhost:3000, then:
./scripts/grant-superuser.sh <your zitadel sub>
```

`setup-stack.sh` waits for Zitadel healthy, clones or reuses `cdcf-infra`, writes
a `.env.local` for the provisioners (`ZITADEL_ISSUER=http://localhost:8080`,
`ZITADEL_INTERNAL_URL=http://127.0.0.1:8080`,
`ZITADEL_PAT_FILE=./.zitadel-data/automation-user.pat`, plus the OpenFGA values),
runs `--create-org Martyrology --provision-martyrology
--provision-martyrology-frontend`, and writes the emitted IDs back into `.env`.

Two properties of this sequence are load-bearing:

- **Step 5 cannot be automated away.** The superuser tuple keys on a Zitadel `sub`
  that does not exist until that account has signed in once.
- **The one-time secret hazard applies locally too.** Both client secrets are
  emitted once, by the run that creates the app. `--update-env` must capture them
  on that run; losing `.env` means rotating in the Zitadel console, not re-reading.

## 7. Verification

A compose stack is not unit-testable, so acceptance is a `scripts/smoke.sh`
asserting the invariants a compose file can actually get wrong:

| # | Assertion | Minimal | Full |
|---|---|---|---|
| 1 | Zitadel healthy; `/.well-known/openid-configuration` served at `http://localhost:8080` | ✅ | ✅ |
| 2 | The `Martyrology` store holds **11** structural tuples (8 `governed_by` + 3 `on_platform`) | ✅ | ✅ |
| 3 | `alembic current` matches `alembic heads` on the `martyrology` database | ✅ | ✅ |
| 4 | `GET /healthz` on the API returns 200 | ✅ | ✅ |
| 5 | An anonymous read of `martyrologium_romanum_2004` returns `metadata.access = "restricted-texts"` with `text: null` | ✅ | ✅ |
| 6 | `http://localhost:8080/ui/v2/login` is served through the proxy | — | ✅ |
| 7 | `GET /api/auth/providers` on the frontend lists `zitadel` | — | ✅ |

Assertion 5 holds in both stacks and in both data configurations: without
`martyrology-texts` mounted the edition is absent and the assertion is skipped
rather than passed silently — the smoke script must distinguish those two
outcomes, since "no such edition" and "redacted" are the same 200 to a careless
check.

Existing `pytest` suites in both repos are untouched by this design.

What the stack then makes verifiable by hand, which is its actual purpose:

- **The stale plan's Task 3 Step 9 and Task 5 Step 8** — local sign-in, revised to
  target the local stack.
- **Task 3's "client authentication method" checkpoint** — whether Auth.js sends
  credentials as form fields (`client_secret_post`, what the app is provisioned
  as) or falls back to HTTP Basic. The local app is created by the same code path
  with the same `OIDC_AUTH_METHOD_TYPE_POST`, so the answer transfers.
- **The restricted-texts read**, with `martyrology-texts` mounted — which also
  retires the `MARTYROLOGY_RESTRICTED_EDITIONS=` workaround in the local `.env`.
- **The open grant-path question** in `cdcf-infra`'s Martyrology handoff. A local
  store makes `can_read_texts` resolution inspectable instead of deduced.

Curation writes (`MARTYROLOGY_LOCAL_GIT_ROOT` against a mounted checkout) are
reachable in this stack but are not an acceptance criterion of it.

## 8. Risks

| Risk | Mitigation |
|---|---|
| `zitadel.local.conf` drifts from `cdcf-infra`'s original. | Only the CSP line differs; the local copy cites the original and says which line is intentionally divergent. |
| The GitHub-default full stack cannot exercise restricted texts. | Accepted and documented. `martyrology-texts` is private; the override path is the supported way to reach it. |
| Pinned `crmedr`/`clbdr` refs in the Dockerfile go stale. | The override bind-mounts host siblings over them, so local work is never blocked by a stale pin. |
| Only one stack runs at a time (D5). | Accepted. Documented in both READMEs. |

## 9. Out of scope

- **The permission-request / notification subsystem** — request submission,
  review, accept/reject, revoke, and notifications, modelled on LitCal's
  `access_requests` / `audit_log`. Its own brainstorm → spec → plan (D9). It lands
  as Alembic migrations plus routes and requires no compose change.
- **Issue #20 for LitCal and CDCF.** `LiturgicalCalendarFrontend` is a single app
  holding production and staging origins together, so making its URLs
  target-dependent under a shared name would strip a working production callback.
  That decision stays with the issue.
- **The production CSP allowlist.** `cdcf-infra`'s `auth/nginx/zitadel.conf`
  lists the CDCF and LitCal origins in `connect-src` but not
  `https://romanmartyrology.com`. A real gap, fixed in `cdcf-infra`, not here.
- **Any staging target.** Martyrology has no staging deployment;
  `--target staging` skips with a warning by design.
