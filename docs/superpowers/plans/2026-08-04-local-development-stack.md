# Local Development Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up local Zitadel + OpenFGA infrastructure for Martyrology in two Docker Compose stacks — an infra-only one in `martyrology-api` and a fully containerized one in `martyrology-frontend` — so the OIDC, authorization and restricted-texts paths become verifiable without production.

**Architecture:** `martyrology-api/docker-compose.yml` runs Postgres, Zitadel, OpenFGA, Mailpit and Adminer for a host-run `uvicorn`. `martyrology-frontend/docker-compose.yml` runs the same plus `zitadel-login`, an nginx proxy giving Zitadel a single origin, and containers for both applications — building from GitHub refs by default, with a gitignored override that repoints builds at local sibling checkouts. The authoritative OpenFGA model is cloned from `cdcf-infra` rather than vendored.

**Tech Stack:** Docker Compose, Postgres 17, Zitadel v4.15.0, OpenFGA v1.15.1, nginx:alpine, Python 3.12 + uv + Alembic + SQLAlchemy, Node 24 + Next.js 16 standalone.

**Spec:** `docs/superpowers/specs/2026-08-04-local-development-stack-design.md`

## Global Constraints

- **Image versions are pinned to production's**, never `:latest`: `ghcr.io/zitadel/zitadel:v4.15.0`, `ghcr.io/zitadel/zitadel-login:v4.15.0`, `openfga/openfga:v1.15.1`, `postgres:17`, `nginx:alpine`, `adminer:latest`, `axllent/mailpit:latest`, `alpine:3.21`.
- **Ports reuse LiturgicalCalendar's numbers** (spec D5): Postgres `5432`, Zitadel `8080`, OpenFGA HTTP `8083`, OpenFGA gRPC `8084`, OpenFGA Playground `3001`, Adminer `8088`, Mailpit `8025`, API `8000`, frontend `3000`. Port `8081` is deliberately unused. All published ports bind `127.0.0.1` only.
- **The frontend's published port is `3000` and cannot change.** `cdcf-infra`'s `--target local` registers `http://localhost:3000/api/auth/callback/zitadel` as the OIDC redirect URI.
- **OpenFGA runs with `OPENFGA_AUTHN_METHOD=preshared` in both stacks.** `Settings.authz_enabled` requires a non-empty `openfga_api_token`; a tokenless OpenFGA makes the whole stack fail closed while reporting healthy.
- **Compose project names differ**: `martyrology-infra` (API repo) and `martyrology` (frontend repo), so the two stacks never share volumes.
- **`MARTYROLOGY_ZITADEL_INTERNAL_URL` is transport-only.** It must never influence `auth_enabled`, which keys off `zitadel_issuer` alone.
- Python `>=3.12`; Node `>=24`.
- The `Martyrology` OpenFGA store holds exactly **11** structural tuples: 8 `governed_by` + 3 `on_platform`.
- Every commit is GPG-signed (`git commit -S`). Never bypass signing.
- Work in `martyrology-api` on branch `feat/local-dev-stack`; in `martyrology-frontend` on branch `feat/local-dev-stack`.

## File Structure

**`martyrology-api`** (Tasks 1–8)

| File | Responsibility |
|---|---|
| `src/martyrology_api/config.py` | Adds `zitadel_internal_url`, `database_url` |
| `src/martyrology_api/auth.py` | Introspection targets the internal URL |
| `src/martyrology_api/app.py` | Passes the new setting through |
| `alembic.ini`, `alembic/env.py`, `alembic/versions/` | Migration contract; no tables yet |
| `Dockerfile`, `.dockerignore` | API image, consumed by the frontend stack and CI |
| `scripts/init-db.sql` | Roles + databases for zitadel, openfga, martyrology |
| `docker-compose.yml` | Minimal stack |
| `.env.example` | Stack knobs |
| `scripts/setup-stack.sh` | Provisions Zitadel, discovers OpenFGA IDs, writes `.env` |
| `scripts/grant-superuser.sh` | One-shot superuser tuple write |
| `scripts/smoke.sh` | Bring-up invariants |

**`martyrology-frontend`** (Tasks 9–13)

| File | Responsibility |
|---|---|
| `Dockerfile`, `.dockerignore` | Next.js standalone image |
| `docker/nginx/zitadel.local.conf` | Single-origin routing + localhost CSP |
| `docker-compose.yml` | Full stack |
| `docker-compose.override.example.yml` | Local sibling builds + private texts |
| `.env.example` | Stack knobs incl. Auth.js vars |
| `scripts/{setup-stack,grant-superuser,smoke}.sh` | Full-stack variants |

---

## Task 1: `MARTYROLOGY_ZITADEL_INTERNAL_URL`

**Files:**
- Modify: `src/martyrology_api/config.py`
- Modify: `src/martyrology_api/auth.py:18-108`
- Modify: `src/martyrology_api/app.py:31-35`
- Test: `tests/test_auth.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.zitadel_internal_url: str` (default `""`); `Authenticator.__init__(issuer, client_id, client_secret, project_id="", internal_url="", cache_ttl=300, cache_max=10_000, transport=None)`; attribute `Authenticator.internal_url: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
def mock_transport_recording(seen: list[str], sub: str):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"active": True, "sub": sub})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_internal_url_is_used_for_introspection():
    seen: list[str] = []
    a = Authenticator(
        "https://auth.example",
        "cid",
        "sec",
        internal_url="http://zitadel:8080",
        transport=mock_transport_recording(seen, "u1"),
    )
    ident = await a.identity("tok-internal")
    assert ident is not None
    assert ident.subject == "u1"
    assert seen == ["http://zitadel:8080/oauth/v2/introspect"]


@pytest.mark.asyncio
async def test_internal_url_defaults_to_issuer_and_strips_trailing_slash():
    seen: list[str] = []
    a = Authenticator(
        "https://auth.example/", "cid", "sec", transport=mock_transport_recording(seen, "u2")
    )
    await a.identity("tok-default")
    assert seen == ["https://auth.example/oauth/v2/introspect"]


@pytest.mark.asyncio
async def test_internal_url_does_not_resurrect_auth_when_issuer_is_empty():
    # Transport-only override: an internal URL must never make a
    # deliberately-disabled authenticator start answering.
    a = Authenticator("", "cid", "sec", internal_url="http://zitadel:8080")
    assert await a.identity("tok-no-issuer") is None
```

Append to `tests/test_config.py`:

```python
def test_zitadel_internal_url_defaults_empty_and_does_not_affect_posture():
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.zitadel_internal_url == ""

    s2 = Settings(  # pyright: ignore[reportCallIssue]
        _env_file=None, zitadel_internal_url="http://zitadel:8080"
    )
    assert s2.zitadel_internal_url == "http://zitadel:8080"
    assert s2.auth_enabled is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py -k internal_url tests/test_config.py -k internal_url -v`
Expected: FAIL — `TypeError: Authenticator.__init__() got an unexpected keyword argument 'internal_url'`, and `AttributeError`/validation error for `zitadel_internal_url`.

- [ ] **Step 3: Add the setting**

In `src/martyrology_api/config.py`, immediately after the `zitadel_project_id` line:

```python
    # Transport-only override for the introspection endpoint. Empty = use
    # zitadel_issuer. Set when the browser-facing issuer is not reachable from
    # inside the API process: in Docker `localhost` is the container's own
    # loopback, and behind Plesk nginx terminates upstream. This is NEVER an
    # auth-posture input — `auth_enabled` still keys off zitadel_issuer alone.
    zitadel_internal_url: str = ""
```

- [ ] **Step 4: Use it for introspection**

In `src/martyrology_api/auth.py`, add the parameter to `Authenticator.__init__` after `project_id`:

```python
        project_id: str = "",
        internal_url: str = "",
```

and set the attribute immediately after `self.project_id = project_id`:

```python
        # Where introspection is actually sent. `issuer` stays the public,
        # browser-facing value asserted in the `iss` claim; only the transport
        # target moves.
        self.internal_url = (internal_url or issuer).rstrip("/")
```

In `identity()`, change the POST URL only — leave the `if not self.issuer: return None` guard exactly as it is:

```python
                    f"{self.internal_url}/oauth/v2/introspect",
```

- [ ] **Step 5: Pass it through at the call site**

In `src/martyrology_api/app.py`, the `Authenticator(...)` construction becomes:

```python
    app.state.authenticator = Authenticator(
        settings.zitadel_issuer,
        settings.zitadel_client_id,
        settings.zitadel_client_secret,
        settings.zitadel_project_id,
        settings.zitadel_internal_url,
    )
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: PASS, including the pre-existing `tests/test_auth.py` cases that construct `Authenticator` with three positional arguments.

- [ ] **Step 7: Document it**

In `.env.example`, immediately after the `MARTYROLOGY_ZITADEL_PROJECT_ID` block:

```bash
# Transport-only: where to send introspection when the public issuer is not
# reachable from inside the API process (docker networks, reverse proxies).
# Empty = use MARTYROLOGY_ZITADEL_ISSUER. Does not affect auth_enabled.
MARTYROLOGY_ZITADEL_INTERNAL_URL=
```

- [ ] **Step 8: Lint, type-check and commit**

```bash
ruff check src tests && ruff format --check src tests && pyright
git add src/martyrology_api/config.py src/martyrology_api/auth.py src/martyrology_api/app.py tests/test_auth.py tests/test_config.py .env.example
git commit -S -m "Add MARTYROLOGY_ZITADEL_INTERNAL_URL for introspection transport"
```

---

## Task 2: Database URL setting and Alembic scaffold

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/martyrology_api/config.py`
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_baseline.py`
- Test: `tests/test_migrations.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `Settings.database_url: str` (default `""`); an Alembic tree with exactly one head, revision id `0001_baseline`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migrations.py`:

```python
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def test_alembic_tree_has_exactly_one_head():
    # A second head means two migrations claim the same parent — `alembic
    # upgrade head` then fails at deploy time rather than here.
    assert len(_script_directory().get_heads()) == 1


def test_baseline_revision_exists():
    revisions = {r.revision for r in _script_directory().walk_revisions()}
    assert "0001_baseline" in revisions
```

Append to `tests/test_config.py`:

```python
def test_database_url_defaults_empty():
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.database_url == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_migrations.py tests/test_config.py::test_database_url_defaults_empty -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'alembic'`.

- [ ] **Step 3: Add the dependencies**

In `pyproject.toml`, extend `[project].dependencies`:

```toml
dependencies = [
  "fastapi>=0.140.0",
  "uvicorn>=0.51.0",
  "pydantic>=2.13.4",
  "pydantic-settings>=2.3",
  "httpx2>=2.9.1",
  "alembic>=1.14",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.2",
]
```

Then run `uv sync --extra dev` to refresh `uv.lock` and the virtualenv.

- [ ] **Step 4: Add the setting**

In `src/martyrology_api/config.py`, after `zitadel_internal_url`:

```python
    # Postgres DSN for the `martyrology` database. Empty = no database
    # configured; nothing in the API reads it yet. It exists so the
    # permission-request and notification subsystem lands as migrations
    # without a compose change. See the local-development-stack design, D9.
    database_url: str = ""
```

- [ ] **Step 5: Create the Alembic tree**

`alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`alembic/env.py`:

```python
"""Alembic environment.

The DSN comes from MARTYROLOGY_DATABASE_URL rather than alembic.ini so the
one-shot `api-migrate` compose service and a developer shell configure it the
same way. There is no target metadata yet: this tree exists to establish the
migration contract, and autogenerate is deliberately not wired up until the
permission-request subsystem introduces models.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = os.environ.get("MARTYROLOGY_DATABASE_URL", "")
if DATABASE_URL:
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`alembic/versions/0001_baseline.py`:

```python
"""Baseline — establishes the migration contract, creates no tables.

martyrology-api has no application tables yet. This revision exists so that a
fresh `alembic upgrade head` succeeds against an empty `martyrology` database
and stamps a version, which is what the `api-migrate` compose service asserts.
The permission-request and notification subsystem adds real tables on top.

Revision ID: 0001_baseline
Revises:
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_migrations.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 7: Verify Alembic runs against a real database**

```bash
docker run --rm -d --name mig-check -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:17
sleep 5
MARTYROLOGY_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres \
  uv run alembic upgrade head
MARTYROLOGY_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres \
  uv run alembic current
docker rm -f mig-check
```

Expected: `alembic current` prints `0001_baseline (head)`.

- [ ] **Step 8: Document and commit**

Add to `.env.example`, after the internal-URL block:

```bash
# Postgres DSN for the `martyrology` database. Empty outside the docker stack.
# MARTYROLOGY_DATABASE_URL=postgresql+psycopg://martyrology:martyrology@localhost:5432/martyrology
```

```bash
ruff check src tests && pyright && pytest -q
git add pyproject.toml uv.lock src/martyrology_api/config.py alembic alembic.ini tests/test_migrations.py tests/test_config.py .env.example
git commit -S -m "Add the martyrology database setting and an Alembic baseline"
```

---

## Task 3: API Dockerfile

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `scripts/init-db.sql`

**Interfaces:**
- Consumes: the Alembic tree from Task 2.
- Produces: image `martyrology-api:latest` — serves on `:8000`, contains `/app/alembic`, `/app/scripts/init-db.sql`, `/data/crmedr`, `/data/clbdr`; env defaults `MARTYROLOGY_CRMEDR_PATH=/data/crmedr`, `MARTYROLOGY_CLBDR_PATH=/data/clbdr`, `MARTYROLOGY_DATA_PATH=/app/data/editions`.

- [ ] **Step 1: Write `scripts/init-db.sql`**

```sql
-- Bootstrap for the local development stack's Postgres.
--
-- Runs once, on first initialisation of an empty postgres_data volume, via
-- /docker-entrypoint-initdb.d. Creates roles and databases only; no application
-- DDL lives here. Table DDL for the `martyrology` database belongs in
-- alembic/versions/ and is applied by the api-migrate service.
--
-- Zitadel creates its own database from the admin credentials it is given, so
-- only the openfga and martyrology databases are created here.

CREATE ROLE openfga WITH LOGIN PASSWORD 'openfga_secure_password';
CREATE DATABASE openfga OWNER openfga;

CREATE ROLE martyrology WITH LOGIN PASSWORD 'martyrology_secure_password';
CREATE DATABASE martyrology OWNER martyrology;
```

- [ ] **Step 2: Write `.dockerignore`**

```
# Deny-all, then re-admit exactly what the image needs. `vendor/` is excluded
# on purpose: vendor/texts is a PRIVATE submodule, and crmedr/clbdr are cloned
# in the build instead (see the Dockerfile).
*
!pyproject.toml
!uv.lock
!src
!data
!alembic
!alembic.ini
!scripts
scripts/__pycache__
```

- [ ] **Step 3: Write the `Dockerfile`**

```dockerfile
# martyrology-api image.
#
# Consumed by martyrology-frontend's full docker stack and by CI. The API
# repo's own compose stack is infra-only — local API development runs
# `uvicorn --factory --reload` on the host, so this image is never in that
# edit loop. See docs/superpowers/specs/2026-08-04-local-development-stack-design.md, D4.

FROM python:3.12-slim AS build

# Pinned refs for the two data repositories. Override at build time with
# --build-arg when a specific data revision is needed.
ARG CRMEDR_REF=main
ARG CLBDR_REF=main

RUN apt-get update -y && \
    apt-get install -y --no-install-suggests --no-install-recommends \
        git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

# app.py calls Registry.load(crmedr_path, clbdr_path) at startup and
# registry.py reads four files from them unconditionally — the API cannot boot
# without these. They are CLONED rather than COPYed from vendor/ because
# vendor/texts is a PRIVATE submodule: a recursive clone of a GitHub build
# context would fail for anyone without access to it.
RUN git clone --depth 1 --branch "$CRMEDR_REF" \
        https://github.com/CatholicOS/crmedr.git /data/crmedr && \
    git clone --depth 1 --branch "$CLBDR_REF" \
        https://github.com/CatholicOS/clbdr.git /data/clbdr && \
    rm -rf /data/crmedr/.git /data/clbdr/.git


FROM python:3.12-slim AS main

WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY --from=build /data /data
COPY src ./src
COPY data ./data
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts/init-db.sql ./scripts/init-db.sql

ENV PATH="/app/.venv/bin:$PATH" \
    MARTYROLOGY_CRMEDR_PATH=/data/crmedr \
    MARTYROLOGY_CLBDR_PATH=/data/clbdr \
    MARTYROLOGY_DATA_PATH=/app/data/editions

EXPOSE 8000

CMD ["uvicorn", "martyrology_api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Build the image**

Run: `docker build -t martyrology-api:latest .`
Expected: build succeeds; the two `git clone` lines report cloning `crmedr` and `clbdr`.

- [ ] **Step 5: Verify the container serves and carries its payload**

```bash
docker run --rm -d --name mr-api-check -p 18000:8000 martyrology-api:latest
sleep 4
curl -sf http://127.0.0.1:18000/healthz && echo " <- healthz OK"
curl -sf http://127.0.0.1:18000/api/v1/editions | head -c 200 && echo
docker exec mr-api-check ls /app/scripts/init-db.sql /app/alembic.ini /data/clbdr/data/editions.json
docker rm -f mr-api-check
```

Expected: `/healthz` returns 200; `/api/v1/editions` returns JSON; all three `ls` paths exist. A failure of `Registry.load` would have crashed the container before `/healthz` could answer, so a 200 here proves the cloned data repos are wired correctly.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore scripts/init-db.sql
git commit -S -m "Add the API Dockerfile and database bootstrap SQL"
```

---

## Task 4: Minimal stack — Postgres, Zitadel, Mailpit, Adminer

**Files:**
- Create: `docker-compose.yml`, `.env.example` additions, `.gitignore` additions

**Interfaces:**
- Consumes: `scripts/init-db.sql` from Task 3.
- Produces: compose project `martyrology-infra` with services `db`, `zitadel`, `mailpit`, `adminer`; a host directory `./.zitadel-data/` containing `automation-user.pat`.

- [ ] **Step 1: Extend `.gitignore`**

```
.zitadel-data/
.stack-out/
docker-compose.override.yml
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
# Martyrology local development stack — INFRA ONLY.
#
# The API itself is NOT here. Run it on the host:
#   uvicorn martyrology_api.app:create_app --factory --reload
#
# This mirrors LiturgicalCalendarAPI's stack, which likewise runs its API on the
# host and containerizes only the infrastructure. The fully containerized stack
# lives in the martyrology-frontend repo.
#
# Deliberate divergence from cdcf-infra production: there is no zitadel-login
# service and no nginx proxy. Nothing here performs an interactive browser
# sign-in — the API only ever calls /oauth/v2/introspect — so Zitadel is served
# directly and Login V2 is switched off. The frontend repo's stack is the one
# that mirrors production's single-origin topology.
#
# Bring-up: see README.md → "Local development stack".

name: martyrology-infra

services:
  db:
    image: postgres:17
    restart: unless-stopped
    environment:
      PGUSER: postgres
      POSTGRES_PASSWORD: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 30s
      retries: 5
    ports:
      - "127.0.0.1:${DB_PORT:-5432}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data:rw
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    networks:
      - martyrology

  zitadel:
    image: ghcr.io/zitadel/zitadel:v4.15.0
    restart: unless-stopped
    command: 'start-from-init --masterkey "${ZITADEL_MASTERKEY:-MasterkeyNeedsToHave32Characters}"'
    environment:
      ZITADEL_EXTERNALDOMAIN: localhost
      ZITADEL_EXTERNALPORT: 8080
      ZITADEL_EXTERNALSECURE: false
      ZITADEL_TLS_ENABLED: false

      ZITADEL_DATABASE_POSTGRES_HOST: db
      ZITADEL_DATABASE_POSTGRES_PORT: 5432
      ZITADEL_DATABASE_POSTGRES_DATABASE: zitadel
      ZITADEL_DATABASE_POSTGRES_ADMIN_USERNAME: postgres
      ZITADEL_DATABASE_POSTGRES_ADMIN_PASSWORD: postgres
      ZITADEL_DATABASE_POSTGRES_ADMIN_SSL_MODE: disable
      ZITADEL_DATABASE_POSTGRES_USER_USERNAME: zitadel
      ZITADEL_DATABASE_POSTGRES_USER_PASSWORD: zitadel
      ZITADEL_DATABASE_POSTGRES_USER_SSL_MODE: disable

      # No Login V2 in this stack — there is no frontend to log in to, and the
      # v2 UI would have nothing routing to it. Zitadel's built-in console
      # login is used for the admin console.
      ZITADEL_DEFAULTINSTANCE_FEATURES_LOGINV2_REQUIRED: false

      # The automation PAT setup-zitadel.sh authenticates with. Written into
      # the bind-mounted ./.zitadel-data/ so the host-run script can read it.
      # The filename matches setup-zitadel.sh's ZITADEL_PAT_FILE convention.
      ZITADEL_FIRSTINSTANCE_PATPATH: /zitadel-data/automation-user.pat
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_USERNAME: automation-user
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_NAME: Automation User
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_PAT_EXPIRATIONDATE: '2030-01-01T00:00:00Z'

      ZITADEL_FIRSTINSTANCE_ORG_NAME: "Martyrology"
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_USERNAME: root
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORD: RootPassword1!
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORDCHANGEREQUIRED: false

      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_SMTP_HOST: mailpit:1025
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_SMTP_USER: ""
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_SMTP_PASSWORD: ""
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_TLS: false
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_FROM: noreply@martyrology.localhost
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_FROMNAME: Martyrology

      ZITADEL_LOG_LEVEL: info
    healthcheck:
      test: ["CMD", "/app/zitadel", "ready"]
      interval: 10s
      timeout: 60s
      retries: 5
      start_period: 10s
    user: "0"
    volumes:
      - ./.zitadel-data:/zitadel-data:delegated
    ports:
      - "127.0.0.1:${ZITADEL_PORT:-8080}:8080"
    networks:
      - martyrology
    depends_on:
      db:
        condition: service_healthy
      mailpit:
        condition: service_started

  mailpit:
    image: axllent/mailpit:latest
    restart: unless-stopped
    environment:
      MP_SMTP_AUTH_ACCEPT_ANY: 1
      MP_SMTP_AUTH_ALLOW_INSECURE: 1
    ports:
      - "127.0.0.1:${MAILPIT_PORT:-8025}:8025"
    networks:
      - martyrology

  adminer:
    image: adminer:latest
    restart: unless-stopped
    environment:
      ADMINER_DEFAULT_SERVER: db
      ADMINER_DESIGN: lucas-sandery
    ports:
      - "127.0.0.1:${ADMINER_PORT:-8088}:8080"
    networks:
      - martyrology
    depends_on:
      - db

networks:
  martyrology:
    driver: bridge

volumes:
  postgres_data:
    driver: local
```

- [ ] **Step 3: Write `.env.example` stack section**

Append to `.env.example`:

```bash
# --- Local development stack (docker compose) ---------------------------
# Copy to .env before `docker compose up -d`. Ports match LiturgicalCalendar's
# stack, so only one of the two can run at a time.
DB_PORT=5432
ZITADEL_PORT=8080
MAILPIT_PORT=8025
ADMINER_PORT=8088
OPENFGA_HTTP_PORT=8083
OPENFGA_GRPC_PORT=8084
OPENFGA_PLAYGROUND_PORT=3001

# Must be EXACTLY 32 characters. Generate with: openssl rand -hex 16
ZITADEL_MASTERKEY=MasterkeyNeedsToHave32Characters

# OpenFGA preshared key. REQUIRED, not optional: Settings.authz_enabled is
# false when MARTYROLOGY_OPENFGA_API_TOKEN is empty, which silently denies
# every authorization check while the stack reports healthy.
OPENFGA_PRESHARED_KEY=local-dev-preshared-key

# Ref of CatholicOS/cdcf-infra the authz-seed service clones for the OpenFGA
# model and tuples.
CDCF_INFRA_REF=main
```

- [ ] **Step 4: Bring the stack up**

```bash
cp -n .env.example .env
docker compose up -d
docker compose ps
```

Expected: `db` and `zitadel` reach `healthy`; `mailpit` and `adminer` are `running`.

- [ ] **Step 5: Verify the databases and the PAT**

```bash
docker compose exec -T db psql -U postgres -tAc \
  "SELECT datname FROM pg_database WHERE datname IN ('zitadel','openfga','martyrology') ORDER BY 1"
test -s ./.zitadel-data/automation-user.pat && echo "PAT written"
curl -sf http://localhost:8080/.well-known/openid-configuration | head -c 120 && echo
```

Expected: three database names printed (`martyrology`, `openfga`, `zitadel`); `PAT written`; discovery JSON served.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example .gitignore
git commit -S -m "Add the minimal stack: Postgres, Zitadel, Mailpit, Adminer"
```

---

## Task 5: OpenFGA and the `authz-seed` one-shot

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the `db` and network from Task 4; `OPENFGA_PRESHARED_KEY` from `.env`.
- Produces: services `openfga-migrate`, `openfga`, `authz-seed`; an OpenFGA store named `Martyrology` holding 11 structural tuples, reachable at `http://openfga:8080` inside the network and `http://localhost:8083` from the host.

- [ ] **Step 1: Add the services to `docker-compose.yml`**

Insert before the `adminer` service:

```yaml
  openfga-migrate:
    image: openfga/openfga:v1.15.1
    command: migrate
    environment:
      OPENFGA_DATASTORE_ENGINE: postgres
      OPENFGA_DATASTORE_URI: postgres://openfga:openfga_secure_password@db:5432/openfga?sslmode=disable
    networks:
      - martyrology
    restart: "no"
    depends_on:
      db:
        condition: service_healthy

  openfga:
    image: openfga/openfga:v1.15.1
    command: run
    restart: unless-stopped
    environment:
      OPENFGA_DATASTORE_ENGINE: postgres
      OPENFGA_DATASTORE_URI: postgres://openfga:openfga_secure_password@db:5432/openfga?sslmode=disable
      # Preshared auth is REQUIRED, not a hardening choice. Settings.authz_enabled
      # is `bool(api_url and store_id and api_token)`, so a tokenless OpenFGA
      # leaves MARTYROLOGY_OPENFGA_API_TOKEN empty, authz disabled, and every
      # check denied — while the stack reports perfectly healthy.
      OPENFGA_AUTHN_METHOD: preshared
      OPENFGA_AUTHN_PRESHARED_KEYS: "${OPENFGA_PRESHARED_KEY:-local-dev-preshared-key}"
      OPENFGA_PLAYGROUND_ENABLED: "${OPENFGA_PLAYGROUND_ENABLED:-false}"
    healthcheck:
      test: ["CMD", "/usr/local/bin/grpc_health_probe", "-addr=localhost:8081"]
      interval: 10s
      timeout: 30s
      retries: 5
      start_period: 10s
    ports:
      - "127.0.0.1:${OPENFGA_HTTP_PORT:-8083}:8080"
      - "127.0.0.1:${OPENFGA_GRPC_PORT:-8084}:8081"
      - "127.0.0.1:${OPENFGA_PLAYGROUND_PORT:-3001}:3000"
    networks:
      - martyrology
    depends_on:
      openfga-migrate:
        condition: service_completed_successfully

  # Creates the Martyrology store, uploads the authorization model, and seeds
  # the 8 governed_by + 3 on_platform structural tuples.
  #
  # The model is CLONED from cdcf-infra rather than vendored into this repo:
  # auth/models/Martyrology{,.tuples}.json is the authoritative copy production
  # uploads, and a second copy here would drift silently. cdcf-infra is public,
  # so this works from a bare clone with no sibling checkouts.
  #
  # Idempotent: setup-openfga.sh reads the store's existing tuples first and
  # writes only the difference. A second run reports everything already present.
  authz-seed:
    image: alpine:3.21
    restart: "no"
    environment:
      CDCF_INFRA_REF: "${CDCF_INFRA_REF:-main}"
      OPENFGA_PRESHARED_KEY: "${OPENFGA_PRESHARED_KEY:-local-dev-preshared-key}"
    entrypoint:
      - /bin/sh
      - -c
      - |
        set -eu
        apk add --no-cache bash curl jq git >/dev/null
        rm -rf /tmp/cdcf-infra
        git clone --depth 1 --branch "$$CDCF_INFRA_REF" \
          https://github.com/CatholicOS/cdcf-infra.git /tmp/cdcf-infra
        cd /tmp/cdcf-infra/auth
        cat > .env.local <<EOF
        OPENFGA_API_URL=http://openfga:8080
        OPENFGA_INTERNAL_URL=http://openfga:8080
        OPENFGA_PRESHARED_KEY=$$OPENFGA_PRESHARED_KEY
        EOF
        ./setup-openfga.sh --target local --create-martyrology-store
    networks:
      - martyrology
    depends_on:
      openfga:
        condition: service_healthy
```

- [ ] **Step 2: Run it**

```bash
docker compose up -d
docker compose logs authz-seed
```

Expected: the log ends with `✓ Uploaded model: <id>` followed by `✓ Wrote 11 new structural tuple(s) (11 declared in file)`.

- [ ] **Step 3: Verify the store contents**

```bash
KEY=$(grep '^OPENFGA_PRESHARED_KEY=' .env | cut -d= -f2)
STORE=$(curl -sf -H "Authorization: Bearer $KEY" http://localhost:8083/stores \
  | jq -r '.stores[] | select(.name=="Martyrology") | .id')
echo "store: $STORE"
curl -sf -X POST "http://localhost:8083/stores/$STORE/read" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{}' | jq '.tuples | length'
```

Expected: a store ID is printed, and the tuple count is `11`.

- [ ] **Step 4: Verify idempotency**

Run: `docker compose up -d --force-recreate authz-seed && docker compose logs --tail=5 authz-seed`
Expected: `All 11 structural tuple(s) already present — nothing to write`.

- [ ] **Step 5: Determine whether the Playground works with preshared auth**

```bash
OPENFGA_PLAYGROUND_ENABLED=true docker compose up -d --force-recreate openfga
sleep 5
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/playground
docker compose logs --tail=20 openfga
```

If OpenFGA refuses to start or the Playground 404s, remove the three Playground lines (`OPENFGA_PLAYGROUND_ENABLED`, the `3001` port mapping, `OPENFGA_PLAYGROUND_PORT` in `.env.example`) and add a comment saying preshared auth precludes it — the spec anticipates this outcome. Otherwise leave it as an opt-in default-off knob. Record which happened in the commit message.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example
git commit -S -m "Add OpenFGA and seed the Martyrology store from cdcf-infra"
```

---

## Task 6: `api-migrate` one-shot

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: `db` from Task 4; the Alembic tree from Task 2; the `Dockerfile` from Task 3.
- Produces: service `api-migrate`, which leaves the `martyrology` database stamped at `0001_baseline`.

- [ ] **Step 1: Add the service**

Insert after `authz-seed` in `docker-compose.yml`:

```yaml
  # One-shot Alembic runner for the `martyrology` database.
  #
  # scripts/init-db.sql (run by db on first init) is bootstrap-only: roles and
  # empty databases. Application-table DDL lives in alembic/versions/ and is
  # applied here. Today that is a single baseline revision creating nothing —
  # the service exists so the permission-request and notification subsystem
  # lands as migrations without a compose change.
  #
  # Re-runnable and a no-op when up to date. Rebuild with
  # `docker compose up -d --build api-migrate` so newly-pulled migrations land
  # in the image before it runs.
  api-migrate:
    build: .
    image: martyrology-api:latest
    command: ["alembic", "upgrade", "head"]
    environment:
      MARTYROLOGY_DATABASE_URL: postgresql+psycopg://martyrology:martyrology_secure_password@db:5432/martyrology
    networks:
      - martyrology
    restart: "no"
    depends_on:
      db:
        condition: service_healthy
```

- [ ] **Step 2: Run it**

```bash
docker compose up -d --build api-migrate
docker compose logs api-migrate
```

Expected: `Running upgrade -> 0001_baseline, Baseline`.

- [ ] **Step 3: Verify the stamp**

```bash
docker compose run --rm --entrypoint alembic api-migrate current
```

Expected: `0001_baseline (head)`.

- [ ] **Step 4: Verify it is a no-op on a second run**

Run: `docker compose up -d --force-recreate api-migrate && docker compose logs --tail=5 api-migrate`
Expected: no `Running upgrade` line; the container exits 0.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -S -m "Add the api-migrate one-shot Alembic service"
```

---

## Task 7: `setup-stack.sh` and `grant-superuser.sh`

**Files:**
- Create: `scripts/setup-stack.sh`, `scripts/grant-superuser.sh`

**Interfaces:**
- Consumes: the running stack from Tasks 4–6; `./.zitadel-data/automation-user.pat`.
- Produces: `.env` gains `MARTYROLOGY_ZITADEL_ISSUER`, `MARTYROLOGY_ZITADEL_INTERNAL_URL`, `MARTYROLOGY_ZITADEL_CLIENT_ID`, `MARTYROLOGY_ZITADEL_CLIENT_SECRET`, `MARTYROLOGY_ZITADEL_PROJECT_ID`, `MARTYROLOGY_OPENFGA_API_URL`, `MARTYROLOGY_OPENFGA_STORE_ID`, `MARTYROLOGY_OPENFGA_MODEL_ID`, `MARTYROLOGY_OPENFGA_API_TOKEN`.

- [ ] **Step 1: Write `scripts/setup-stack.sh`**

```bash
#!/usr/bin/env bash
#
# setup-stack.sh — provision the local Zitadel and discover the OpenFGA IDs,
# then write both into .env.
#
# Phase 2 of the three-phase bring-up (see README.md → "Local development
# stack"). The store ID, model ID, client ID and client secret are all
# GENERATED at provisioning time, so they cannot be committed; this script
# captures them.
#
# ⚠ The client secret is emitted ONCE, by the run that creates the app.
# Zitadel's ListApplications API does not return secrets, so a re-run against
# an existing app cannot recover it. If .env is lost, rotate in the console:
#   Martyrology Org → Projects → MartyrologyAPI → Apps → Regenerate Client Secret
#
# Usage: ./scripts/setup-stack.sh --update-env

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

UPDATE_ENV=0
[[ "${1:-}" == "--update-env" ]] && UPDATE_ENV=1
if [[ $UPDATE_ENV -eq 0 ]]; then
    echo "Usage: $0 --update-env" >&2
    exit 64
fi

ENV_FILE=".env"
PAT_FILE="./.zitadel-data/automation-user.pat"
ZITADEL_PORT="$(grep -E '^ZITADEL_PORT=' "$ENV_FILE" | cut -d= -f2 || true)"
ZITADEL_PORT="${ZITADEL_PORT:-8080}"
OPENFGA_HTTP_PORT="$(grep -E '^OPENFGA_HTTP_PORT=' "$ENV_FILE" | cut -d= -f2 || true)"
OPENFGA_HTTP_PORT="${OPENFGA_HTTP_PORT:-8083}"
PRESHARED_KEY="$(grep -E '^OPENFGA_PRESHARED_KEY=' "$ENV_FILE" | cut -d= -f2)"
CDCF_INFRA_REF="$(grep -E '^CDCF_INFRA_REF=' "$ENV_FILE" | cut -d= -f2 || true)"
CDCF_INFRA_REF="${CDCF_INFRA_REF:-main}"

ISSUER="http://localhost:${ZITADEL_PORT}"
WORKDIR=".stack-out"
mkdir -p "$WORKDIR"

# --- wait for Zitadel -----------------------------------------------------
echo "Waiting for Zitadel at $ISSUER ..."
for _ in $(seq 1 60); do
    if curl -sf "$ISSUER/.well-known/openid-configuration" >/dev/null; then break; fi
    sleep 2
done
curl -sf "$ISSUER/.well-known/openid-configuration" >/dev/null \
    || { echo "Zitadel never became ready" >&2; exit 1; }
[[ -s "$PAT_FILE" ]] || { echo "PAT not found at $PAT_FILE" >&2; exit 1; }

# --- clone or refresh cdcf-infra -----------------------------------------
INFRA_DIR="$WORKDIR/cdcf-infra"
if [[ -d "$INFRA_DIR/.git" ]]; then
    git -C "$INFRA_DIR" fetch --quiet origin "$CDCF_INFRA_REF"
    git -C "$INFRA_DIR" checkout --quiet "FETCH_HEAD"
else
    git clone --quiet --depth 1 --branch "$CDCF_INFRA_REF" \
        https://github.com/CatholicOS/cdcf-infra.git "$INFRA_DIR"
fi

# --- provision Zitadel ----------------------------------------------------
# ZITADEL_PAT_FILE must be absolute: setup-zitadel.sh runs from auth/.
cat > "$INFRA_DIR/auth/.env.local" <<EOF
ZITADEL_ISSUER=$ISSUER
ZITADEL_INTERNAL_URL=$ISSUER
ZITADEL_PAT_FILE=$(cd "$(dirname "$PAT_FILE")" && pwd)/$(basename "$PAT_FILE")
ZITADEL_ADMIN_EMAIL=root@martyrology.localhost
EOF

OUT="$WORKDIR/zitadel-provision.out"
(
    cd "$INFRA_DIR/auth"
    ./setup-zitadel.sh --target local \
        --create-org Martyrology \
        --provision-martyrology
) | tee "$OUT"

# The handoff block prints `KEY=value` lines, some with a trailing comment.
# Colours are suppressed automatically because stdout is a pipe.
val() { sed -n "s/^$1=\([^ ]*\).*/\1/p" "$OUT" | head -1; }

CLIENT_ID="$(val MARTYROLOGY_ZITADEL_CLIENT_ID)"
CLIENT_SECRET="$(val MARTYROLOGY_ZITADEL_CLIENT_SECRET)"
PROJECT_ID="$(val ZITADEL_PROJECT_ID)"

[[ -n "$CLIENT_ID" ]]  || { echo "No client ID in provisioner output" >&2; exit 1; }
[[ -n "$PROJECT_ID" ]] || { echo "No project ID in provisioner output" >&2; exit 1; }

# --- discover the OpenFGA IDs --------------------------------------------
# Queried from the API rather than parsed out of setup-openfga.sh's output:
# the store already exists (authz-seed created it), and an API read is stable
# where output parsing is not.
FGA="http://localhost:${OPENFGA_HTTP_PORT}"
STORE_ID="$(curl -sf -H "Authorization: Bearer $PRESHARED_KEY" "$FGA/stores" \
    | jq -r '.stores[] | select(.name=="Martyrology") | .id' | head -1)"
[[ -n "$STORE_ID" ]] || { echo "No Martyrology store found at $FGA" >&2; exit 1; }

MODEL_ID="$(curl -sf -H "Authorization: Bearer $PRESHARED_KEY" \
    "$FGA/stores/$STORE_ID/authorization-models?page_size=1" \
    | jq -r '.authorization_models[0].id')"
[[ -n "$MODEL_ID" && "$MODEL_ID" != "null" ]] \
    || { echo "No authorization model in store $STORE_ID" >&2; exit 1; }

# --- write .env -----------------------------------------------------------
set_env() {
    local key="$1" value="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

set_env MARTYROLOGY_ZITADEL_ISSUER       "$ISSUER"
set_env MARTYROLOGY_ZITADEL_INTERNAL_URL "$ISSUER"
set_env MARTYROLOGY_ZITADEL_CLIENT_ID    "$CLIENT_ID"
set_env MARTYROLOGY_ZITADEL_PROJECT_ID   "$PROJECT_ID"
set_env MARTYROLOGY_OPENFGA_API_URL      "$FGA"
set_env MARTYROLOGY_OPENFGA_STORE_ID     "$STORE_ID"
set_env MARTYROLOGY_OPENFGA_MODEL_ID     "$MODEL_ID"
set_env MARTYROLOGY_OPENFGA_API_TOKEN    "$PRESHARED_KEY"

if [[ -n "$CLIENT_SECRET" ]]; then
    set_env MARTYROLOGY_ZITADEL_CLIENT_SECRET "$CLIENT_SECRET"
    echo "✓ Client secret captured (one-time emit)."
else
    echo "⚠ No client secret emitted — the app already existed." >&2
    echo "  Existing MARTYROLOGY_ZITADEL_CLIENT_SECRET in .env left untouched." >&2
    grep -qE '^MARTYROLOGY_ZITADEL_CLIENT_SECRET=.+' "$ENV_FILE" \
        || echo "  .env has NO secret. Rotate it in the Zitadel console." >&2
fi

echo
echo "✓ .env updated. Restart the API to pick up the new values."
```

Make it executable: `chmod +x scripts/setup-stack.sh`.

- [ ] **Step 2: Write `scripts/grant-superuser.sh`**

```bash
#!/usr/bin/env bash
#
# grant-superuser.sh — write the platform:martyrology superuser tuple.
#
# Out-of-band by design, exactly as in production. The API's
# /api/v1/admin/permissions endpoint fixes its object type to governance_body,
# so platform: tuples are structurally unreachable through it — otherwise any
# body admin could mint themselves a superuser. Every superuser grant, not just
# the first, is made this way.
#
# The `sub` only exists after that account has signed in once, which is why
# this cannot be folded into setup-stack.sh.
#
# Usage:   ./scripts/grant-superuser.sh <zitadel-sub>
# Revoke:  ./scripts/grant-superuser.sh <zitadel-sub> --revoke

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SUB="${1:-}"
[[ -n "$SUB" ]] || { echo "Usage: $0 <zitadel-sub> [--revoke]" >&2; exit 64; }
OP="writes"
[[ "${2:-}" == "--revoke" ]] && OP="deletes"

ENV_FILE=".env"
API_URL="$(grep -E '^MARTYROLOGY_OPENFGA_API_URL=' "$ENV_FILE" | cut -d= -f2-)"
STORE_ID="$(grep -E '^MARTYROLOGY_OPENFGA_STORE_ID=' "$ENV_FILE" | cut -d= -f2)"
TOKEN="$(grep -E '^MARTYROLOGY_OPENFGA_API_TOKEN=' "$ENV_FILE" | cut -d= -f2)"

for v in API_URL STORE_ID TOKEN; do
    [[ -n "${!v}" ]] || { echo "$v missing from $ENV_FILE — run setup-stack.sh first" >&2; exit 1; }
done

curl -sS --fail-with-body -X POST "$API_URL/stores/$STORE_ID/write" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"$OP\":{\"tuple_keys\":[{\"user\":\"user:$SUB\",\"relation\":\"superuser\",\"object\":\"platform:martyrology\"}]}}"

echo
echo "✓ $OP superuser tuple for user:$SUB"
```

Make it executable: `chmod +x scripts/grant-superuser.sh`.

- [ ] **Step 3: Run the provisioner**

```bash
./scripts/setup-stack.sh --update-env
grep -E '^MARTYROLOGY_(ZITADEL|OPENFGA)_' .env
```

Expected: all nine keys present and non-empty, including `MARTYROLOGY_ZITADEL_CLIENT_SECRET`.

- [ ] **Step 4: Verify idempotency and the secret warning**

Run: `./scripts/setup-stack.sh --update-env`
Expected: succeeds; prints `⚠ No client secret emitted — the app already existed.`; the existing `.env` secret is unchanged (`grep MARTYROLOGY_ZITADEL_CLIENT_SECRET .env` shows the same value as Step 3).

- [ ] **Step 5: Verify the API accepts the configuration**

```bash
set -a; . ./.env; set +a
uv run uvicorn martyrology_api.app:create_app --factory --port 8000 &
sleep 4
curl -sf http://localhost:8000/healthz && echo " <- healthz OK"
kill %1
```

Expected: 200, and **no** `OpenFGA is partially configured` warning in the uvicorn log — that warning firing means one of the four OpenFGA values did not land.

- [ ] **Step 6: Verify the superuser grant**

```bash
# Any sub works for this check; the tuple write does not validate existence.
./scripts/grant-superuser.sh 000000000000000000
KEY=$(grep '^OPENFGA_PRESHARED_KEY=' .env | cut -d= -f2)
STORE=$(grep '^MARTYROLOGY_OPENFGA_STORE_ID=' .env | cut -d= -f2)
curl -sf -X POST "http://localhost:8083/stores/$STORE/check" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"tuple_key":{"user":"user:000000000000000000","relation":"can_read_texts","object":"edition:martyrologium_romanum_2004"}}'
./scripts/grant-superuser.sh 000000000000000000 --revoke
```

Expected: `{"allowed":true}` — proving the `platform → on_platform → admin → editor → reader → can_read_texts` chain resolves. The revoke leaves the store clean.

- [ ] **Step 7: Commit**

```bash
git add scripts/setup-stack.sh scripts/grant-superuser.sh
git commit -S -m "Add local stack provisioning and superuser grant scripts"
```

---

## Task 8: Minimal-stack smoke test and README

**Files:**
- Create: `scripts/smoke.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 4–7.
- Produces: `scripts/smoke.sh`, exit 0 on a healthy stack.

- [ ] **Step 1: Write `scripts/smoke.sh`**

```bash
#!/usr/bin/env bash
#
# smoke.sh — assert the bring-up invariants a compose file can get wrong.
#
# Not a substitute for pytest: this checks wiring, not behaviour. Run it after
# `setup-stack.sh --update-env` with the API running on the host.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
set -a; . ./.env; set +a

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  ✓ %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  ✗ %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  ~ %s\n' "$1"; SKIP=$((SKIP+1)); }

API="http://localhost:${API_PORT:-8000}"
FGA="${MARTYROLOGY_OPENFGA_API_URL:-http://localhost:8083}"
ISSUER="${MARTYROLOGY_ZITADEL_ISSUER:-http://localhost:8080}"

echo "1. Zitadel discovery"
curl -sf "$ISSUER/.well-known/openid-configuration" | jq -e '.issuer' >/dev/null \
    && ok "discovery served at $ISSUER" || bad "no discovery document at $ISSUER"

echo "2. OpenFGA structural tuples"
COUNT=$(curl -sf -X POST "$FGA/stores/$MARTYROLOGY_OPENFGA_STORE_ID/read" \
    -H "Authorization: Bearer $MARTYROLOGY_OPENFGA_API_TOKEN" \
    -H "Content-Type: application/json" -d '{}' | jq '.tuples | length')
[[ "$COUNT" == "11" ]] && ok "11 structural tuples" || bad "expected 11 tuples, got ${COUNT:-none}"

echo "3. Alembic is at head"
CUR=$(docker compose run --rm --entrypoint alembic api-migrate current 2>/dev/null | tr -d '\r')
grep -q '(head)' <<<"$CUR" && ok "alembic current is at head" || bad "alembic not at head: $CUR"

echo "4. API health"
curl -sf "$API/healthz" >/dev/null && ok "GET /healthz 200" || bad "GET /healthz failed"

echo "5. Anonymous read of a restricted edition is redacted"
# "no such edition" and "redacted" are BOTH 200-shaped to a careless check, so
# distinguish them: absent edition => skip, present => must be redacted.
BODY=$(curl -sf "$API/api/v1/elogia/edition/martyrologium_romanum_2004/01/02" 2>/dev/null)
if [[ -z "$BODY" ]]; then
    skip "martyrologium_romanum_2004 not attached (martyrology-texts not mounted)"
else
    ACCESS=$(jq -r '.metadata.access // empty' <<<"$BODY")
    TEXT=$(jq -r '.elogia[0].text // "null"' <<<"$BODY")
    [[ "$ACCESS" == "restricted-texts" && "$TEXT" == "null" ]] \
        && ok "access=restricted-texts with text=null" \
        || bad "expected redaction, got access=$ACCESS text=$TEXT"
fi

echo
printf 'passed %d, failed %d, skipped %d\n' "$PASS" "$FAIL" "$SKIP"
[[ $FAIL -eq 0 ]]
```

Make it executable: `chmod +x scripts/smoke.sh`.

- [ ] **Step 2: Run it**

```bash
set -a; . ./.env; set +a
uv run uvicorn martyrology_api.app:create_app --factory --port 8000 &
sleep 4
./scripts/smoke.sh
kill %1
```

Expected: assertions 1–4 pass; assertion 5 skips (this repo's `data/editions` holds only the two public-domain editions). Exit code 0.

- [ ] **Step 3: Add the README section**

Insert after the existing development instructions in `README.md`:

````markdown
## Local development stack

Brings up Zitadel and OpenFGA locally so auth and authorization can be
exercised without production. The API itself is **not** containerized here —
run it on the host. The fully containerized stack lives in
[`martyrology-frontend`](https://github.com/CatholicOS/martyrology-frontend).

Requires Docker with Compose v2. Ports match LiturgicalCalendar's stack, so
only one of the two can run at a time.

```bash
cp .env.example .env                    # 1. stack knobs
docker compose up -d                    # 2. infra; the store is seeded automatically
./scripts/setup-stack.sh --update-env   # 3. provision Zitadel, write IDs into .env
set -a; . ./.env; set +a                # 4. run the API against it
uvicorn martyrology_api.app:create_app --factory --reload
./scripts/smoke.sh                      # 5. verify
```

| Service | URL | Credentials |
| --- | --- | --- |
| Zitadel console | <http://localhost:8080/ui/console> | `root@martyrology.localhost` / `RootPassword1!` |
| OpenFGA API | <http://localhost:8083> | Bearer `OPENFGA_PRESHARED_KEY` from `.env` |
| Adminer | <http://localhost:8088> | server `db`, user `postgres`, password `postgres` |
| Mailpit | <http://localhost:8025> | — |

To grant yourself platform superuser (after signing in once, so a `sub`
exists — find it under Martyrology Org → Users → your user → ID):

```bash
./scripts/grant-superuser.sh <your-sub>
```

**The OIDC client secret is emitted once.** `setup-stack.sh` captures it into
`.env` on the run that creates the app; a re-run cannot recover it. If `.env`
is lost, regenerate the secret in the Zitadel console.

**`OPENFGA_PRESHARED_KEY` is required, not optional.** `Settings.authz_enabled`
is false when `MARTYROLOGY_OPENFGA_API_TOKEN` is empty, which denies every
authorization check while the stack reports healthy.
````

- [ ] **Step 4: Commit and open the PR**

```bash
git add scripts/smoke.sh README.md
git commit -S -m "Add the minimal-stack smoke test and document the bring-up"
git push -u origin feat/local-dev-stack
gh pr create --title "Local development stack: infra-only compose for martyrology-api" \
  --body "Implements Tasks 1-8 of docs/superpowers/plans/2026-08-04-local-development-stack.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Task 9: Frontend Dockerfile

**Repo:** `martyrology-frontend`, branch `feat/local-dev-stack`

**Files:**
- Create: `Dockerfile`, `.dockerignore`

**Interfaces:**
- Consumes: `next.config.ts`, which already sets `output: "standalone"`.
- Produces: image `martyrology-frontend:latest`, serving on `:3000`, honouring `API_BASE` at runtime.

- [ ] **Step 1: Write `.dockerignore`**

```
node_modules
.next
.git
.env
.env.*
!.env.example
coverage
*.tsbuildinfo
docker-compose.override.yml
```

- [ ] **Step 2: Write the `Dockerfile`**

```dockerfile
# martyrology-frontend image.
#
# next.config.ts already sets output: "standalone" for the Plesk deploy, which
# is exactly what a lean container wants: a self-contained server.js plus a
# pruned node_modules.
#
# NOTE: this is a PRODUCTION image. It does not hot-reload from a bind mount.
# For frontend iteration, stop this service and run `npm run dev` on the host —
# port 3000 is then free and the registered OIDC callback still matches.

FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-slim AS main
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 3: Build and run**

```bash
docker build -t martyrology-frontend:latest .
docker run --rm -d --name mr-fe-check -p 13000:3000 martyrology-frontend:latest
sleep 5
curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:13000/
docker rm -f mr-fe-check
```

Expected: build succeeds; the request returns `200`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -S -m "Add the frontend Dockerfile"
```

---

## Task 10: Full stack — infrastructure and the single-origin proxy

**Repo:** `martyrology-frontend`

**Files:**
- Create: `docker-compose.yml`, `docker/nginx/zitadel.local.conf`, `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: image `martyrology-api:latest` (Task 3) for `db-init`.
- Produces: compose project `martyrology` with `db-init`, `db`, `zitadel`, `zitadel-login`, `zitadel-proxy`, `mailpit`, `adminer`; Zitadel reachable on the single origin `http://localhost:8080`, with the v2 login UI at `/ui/v2/login`.

- [ ] **Step 1: Extend `.gitignore`**

```
.zitadel-data/
.stack-out/
docker-compose.override.yml
```

- [ ] **Step 2: Write `docker/nginx/zitadel.local.conf`**

```nginx
# Local-development copy of cdcf-infra's auth/nginx/zitadel.conf.
#
# ⚠ This is a SECOND COPY of a file whose comments carry real reasoning. Only
# the Content-Security-Policy differs — the routing half must stay in step with
# the original at:
#   https://github.com/CatholicOS/cdcf-infra/blob/main/auth/nginx/zitadel.conf
#
# Why the CSP differs: production's connect-src allowlists the CDCF and LitCal
# origins. It does not include this stack's origins, so reusing it verbatim
# would block the local frontend's post-login RSC prefetch. The mechanism is
# unchanged — proxy_hide_header strips the upstream header, because multi-CSP
# semantics are intersection and appending alone cannot widen anything.
#
# Routing:
#   /ui/v2/login*   -> zitadel-login:3000
#   everything else -> zitadel:8080

upstream zitadel_backend {
    server zitadel:8080;
}

upstream zitadel_login {
    server zitadel-login:3000;
}

server {
    listen 80 default_server;
    server_name _;

    client_max_body_size 10m;

    location /ui/v2/login {
        proxy_pass http://zitadel_login;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # http, not https: this stack terminates nothing and runs over plain
        # HTTP, matching ZITADEL_EXTERNALSECURE=false.
        proxy_set_header X-Forwarded-Proto http;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Connection '';

        proxy_hide_header Content-Security-Policy;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self' http://localhost:3000 http://localhost:8080; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' http://zitadel:8080; frame-ancestors 'none'; object-src 'none'" always;
    }

    location / {
        proxy_pass http://zitadel_backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto http;
        proxy_set_header X-Forwarded-Host $host;
        proxy_request_buffering off;
        proxy_buffering off;
    }
}
```

- [ ] **Step 3: Write the infrastructure half of `docker-compose.yml`**

```yaml
# Martyrology full development stack.
#
# Mirrors cdcf-infra production topology: Zitadel and the v2 login UI behind a
# single nginx origin, with image versions pinned to production's. That
# fidelity is the point — this stack exists to verify an OIDC flow that will
# run against cdcf-infra, and redirect URIs, issuer discovery and CSP are
# exactly what a two-origin issuer gets wrong.
#
# Builds from GitHub refs by default so a bare clone stands the whole system up.
# For local sibling checkouts:
#   cp docker-compose.override.example.yml docker-compose.override.yml
#
# Bring-up: see README.md → "Local development stack".
#
# Port 8081 is deliberately unused: login v2 sits behind the proxy at
# :8080/ui/v2/login rather than on its own origin.

name: martyrology

services:
  # Extracts init-db.sql from the API image, so the database bootstrap has one
  # source of truth and no copy is maintained in this repo.
  db-init:
    image: martyrology-api:latest
    build: https://github.com/CatholicOS/martyrology-api.git#main
    entrypoint: ["cp", "/app/scripts/init-db.sql", "/init/01-init.sql"]
    volumes:
      - db_init_scripts:/init
    restart: "no"

  db:
    image: postgres:17
    restart: unless-stopped
    environment:
      PGUSER: postgres
      POSTGRES_PASSWORD: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 30s
      retries: 5
    ports:
      - "127.0.0.1:${DB_PORT:-5432}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data:rw
      - db_init_scripts:/docker-entrypoint-initdb.d:ro
    networks:
      - martyrology
    depends_on:
      db-init:
        condition: service_completed_successfully

  # Publishes NOTHING. zitadel-proxy owns the :8080 origin.
  zitadel:
    image: ghcr.io/zitadel/zitadel:v4.15.0
    restart: unless-stopped
    command: 'start-from-init --masterkey "${ZITADEL_MASTERKEY:-MasterkeyNeedsToHave32Characters}"'
    environment:
      ZITADEL_EXTERNALDOMAIN: localhost
      # Describes the PROXY's published port, which is the origin browsers and
      # OIDC clients see.
      ZITADEL_EXTERNALPORT: 8080
      ZITADEL_EXTERNALSECURE: false
      ZITADEL_TLS_ENABLED: false

      ZITADEL_DATABASE_POSTGRES_HOST: db
      ZITADEL_DATABASE_POSTGRES_PORT: 5432
      ZITADEL_DATABASE_POSTGRES_DATABASE: zitadel
      ZITADEL_DATABASE_POSTGRES_ADMIN_USERNAME: postgres
      ZITADEL_DATABASE_POSTGRES_ADMIN_PASSWORD: postgres
      ZITADEL_DATABASE_POSTGRES_ADMIN_SSL_MODE: disable
      ZITADEL_DATABASE_POSTGRES_USER_USERNAME: zitadel
      ZITADEL_DATABASE_POSTGRES_USER_PASSWORD: zitadel
      ZITADEL_DATABASE_POSTGRES_USER_SSL_MODE: disable

      ZITADEL_FIRSTINSTANCE_LOGINCLIENTPATPATH: /zitadel-data/login-client.pat
      ZITADEL_FIRSTINSTANCE_ORG_LOGINCLIENT_MACHINE_USERNAME: login-client
      ZITADEL_FIRSTINSTANCE_ORG_LOGINCLIENT_MACHINE_NAME: Login V2 Client
      ZITADEL_FIRSTINSTANCE_ORG_LOGINCLIENT_PAT_EXPIRATIONDATE: '2030-01-01T00:00:00Z'

      # Single origin: the login UI lives under the proxy, not on its own port.
      ZITADEL_DEFAULTINSTANCE_FEATURES_LOGINV2_REQUIRED: true
      ZITADEL_DEFAULTINSTANCE_FEATURES_LOGINV2_BASEURI: http://localhost:8080/ui/v2/login
      ZITADEL_OIDC_DEFAULTLOGINURLV2: http://localhost:8080/ui/v2/login/login?authRequest=
      ZITADEL_OIDC_DEFAULTLOGOUTURLV2: http://localhost:8080/ui/v2/login/logout?post_logout_redirect=

      ZITADEL_FIRSTINSTANCE_PATPATH: /zitadel-data/automation-user.pat
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_USERNAME: automation-user
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_MACHINE_NAME: Automation User
      ZITADEL_FIRSTINSTANCE_ORG_MACHINE_PAT_EXPIRATIONDATE: '2030-01-01T00:00:00Z'

      ZITADEL_FIRSTINSTANCE_ORG_NAME: "Martyrology"
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_USERNAME: root
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORD: RootPassword1!
      ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORDCHANGEREQUIRED: false

      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_SMTP_HOST: mailpit:1025
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_SMTP_USER: ""
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_SMTP_PASSWORD: ""
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_TLS: false
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_FROM: noreply@martyrology.localhost
      ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_FROMNAME: Martyrology

      ZITADEL_LOG_LEVEL: info
    healthcheck:
      test: ["CMD", "/app/zitadel", "ready"]
      interval: 10s
      timeout: 60s
      retries: 5
      start_period: 10s
    user: "0"
    volumes:
      - ./.zitadel-data:/zitadel-data:delegated
    networks:
      - martyrology
    depends_on:
      db:
        condition: service_healthy
      mailpit:
        condition: service_started

  zitadel-login:
    image: ghcr.io/zitadel/zitadel-login:v4.15.0
    restart: unless-stopped
    environment:
      # Reaches the backend over the docker network, not through the proxy.
      ZITADEL_API_URL: http://zitadel:8080
      NEXT_PUBLIC_BASE_PATH: /ui/v2/login
      ZITADEL_SERVICE_USER_TOKEN_FILE: /zitadel-data/login-client.pat
      EMAIL_VERIFICATION: true
    user: "0"
    volumes:
      - ./.zitadel-data:/zitadel-data:ro
    networks:
      - martyrology
    depends_on:
      zitadel:
        condition: service_healthy
        restart: false

  zitadel-proxy:
    image: nginx:alpine
    restart: unless-stopped
    volumes:
      - ./docker/nginx/zitadel.local.conf:/etc/nginx/conf.d/default.conf:ro
    ports:
      - "127.0.0.1:${ZITADEL_PORT:-8080}:80"
    networks:
      - martyrology
    depends_on:
      - zitadel
      - zitadel-login

  mailpit:
    image: axllent/mailpit:latest
    restart: unless-stopped
    environment:
      MP_SMTP_AUTH_ACCEPT_ANY: 1
      MP_SMTP_AUTH_ALLOW_INSECURE: 1
    ports:
      - "127.0.0.1:${MAILPIT_PORT:-8025}:8025"
    networks:
      - martyrology

  adminer:
    image: adminer:latest
    restart: unless-stopped
    environment:
      ADMINER_DEFAULT_SERVER: db
      ADMINER_DESIGN: lucas-sandery
    ports:
      - "127.0.0.1:${ADMINER_PORT:-8088}:8080"
    networks:
      - martyrology
    depends_on:
      - db

networks:
  martyrology:
    driver: bridge

volumes:
  postgres_data:
    driver: local
  db_init_scripts:
    driver: local
```

- [ ] **Step 4: Write `.env.example`**

```bash
# Frontend runtime
API_BASE=http://localhost:8000

# --- Local development stack (docker compose) ---------------------------
DB_PORT=5432
ZITADEL_PORT=8080
MAILPIT_PORT=8025
ADMINER_PORT=8088
OPENFGA_HTTP_PORT=8083
OPENFGA_GRPC_PORT=8084
OPENFGA_PLAYGROUND_PORT=3001
API_PORT=8000
# Fixed by cdcf-infra: --target local registers
# http://localhost:3000/api/auth/callback/zitadel as the OIDC redirect URI.
FRONTEND_PORT=3000

# Must be EXACTLY 32 characters. Generate with: openssl rand -hex 16
ZITADEL_MASTERKEY=MasterkeyNeedsToHave32Characters

# REQUIRED: an empty MARTYROLOGY_OPENFGA_API_TOKEN disables authorization
# entirely while the stack still reports healthy.
OPENFGA_PRESHARED_KEY=local-dev-preshared-key

CDCF_INFRA_REF=main
MARTYROLOGY_API_REF=main
MARTYROLOGY_FRONTEND_REF=main

# Auth.js — written by ./scripts/setup-stack.sh --update-env
AUTH_URL=http://localhost:3000
# Generate with: openssl rand -base64 32
AUTH_SECRET=
AUTH_ZITADEL_ID=
AUTH_ZITADEL_SECRET=
```

- [ ] **Step 5: Bring it up and verify the single origin**

```bash
cp -n .env.example .env
docker compose up -d
sleep 20
curl -sf http://localhost:8080/.well-known/openid-configuration | jq -r '.issuer'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/ui/v2/login/login
curl -sI http://localhost:8080/ui/v2/login/login | grep -i content-security-policy
```

Expected: issuer is `http://localhost:8080`; the login path returns 200 or 3xx (not 404 — a 404 means the proxy is routing to the backend); the CSP header's `connect-src` contains `http://localhost:3000`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml docker/nginx/zitadel.local.conf .env.example .gitignore
git commit -S -m "Add the full stack's infrastructure and single-origin Zitadel proxy"
```

---

## Task 11: Full stack — OpenFGA, migrations, and the two application services

**Repo:** `martyrology-frontend`

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the network and `db` from Task 10.
- Produces: services `openfga-migrate`, `openfga`, `authz-seed`, `api-migrate`, `martyrology-api` (`:8000`), `martyrology-frontend` (`:3000`).

- [ ] **Step 1: Add OpenFGA, the seeder, and the migrator**

Insert before `adminer`. These three are identical to the API repo's stack except that `api-migrate` reuses `martyrology-api:latest` rather than building:

```yaml
  openfga-migrate:
    image: openfga/openfga:v1.15.1
    command: migrate
    environment:
      OPENFGA_DATASTORE_ENGINE: postgres
      OPENFGA_DATASTORE_URI: postgres://openfga:openfga_secure_password@db:5432/openfga?sslmode=disable
    networks:
      - martyrology
    restart: "no"
    depends_on:
      db:
        condition: service_healthy

  openfga:
    image: openfga/openfga:v1.15.1
    command: run
    restart: unless-stopped
    environment:
      OPENFGA_DATASTORE_ENGINE: postgres
      OPENFGA_DATASTORE_URI: postgres://openfga:openfga_secure_password@db:5432/openfga?sslmode=disable
      # Required — see the API repo's compose for why a tokenless OpenFGA
      # silently disables authorization.
      OPENFGA_AUTHN_METHOD: preshared
      OPENFGA_AUTHN_PRESHARED_KEYS: "${OPENFGA_PRESHARED_KEY:-local-dev-preshared-key}"
      OPENFGA_PLAYGROUND_ENABLED: "${OPENFGA_PLAYGROUND_ENABLED:-false}"
    healthcheck:
      test: ["CMD", "/usr/local/bin/grpc_health_probe", "-addr=localhost:8081"]
      interval: 10s
      timeout: 30s
      retries: 5
      start_period: 10s
    ports:
      - "127.0.0.1:${OPENFGA_HTTP_PORT:-8083}:8080"
      - "127.0.0.1:${OPENFGA_GRPC_PORT:-8084}:8081"
      - "127.0.0.1:${OPENFGA_PLAYGROUND_PORT:-3001}:3000"
    networks:
      - martyrology
    depends_on:
      openfga-migrate:
        condition: service_completed_successfully

  authz-seed:
    image: alpine:3.21
    restart: "no"
    environment:
      CDCF_INFRA_REF: "${CDCF_INFRA_REF:-main}"
      OPENFGA_PRESHARED_KEY: "${OPENFGA_PRESHARED_KEY:-local-dev-preshared-key}"
    entrypoint:
      - /bin/sh
      - -c
      - |
        set -eu
        apk add --no-cache bash curl jq git >/dev/null
        rm -rf /tmp/cdcf-infra
        git clone --depth 1 --branch "$$CDCF_INFRA_REF" \
          https://github.com/CatholicOS/cdcf-infra.git /tmp/cdcf-infra
        cd /tmp/cdcf-infra/auth
        cat > .env.local <<EOF
        OPENFGA_API_URL=http://openfga:8080
        OPENFGA_INTERNAL_URL=http://openfga:8080
        OPENFGA_PRESHARED_KEY=$$OPENFGA_PRESHARED_KEY
        EOF
        ./setup-openfga.sh --target local --create-martyrology-store
    networks:
      - martyrology
    depends_on:
      openfga:
        condition: service_healthy

  api-migrate:
    image: martyrology-api:latest
    command: ["alembic", "upgrade", "head"]
    environment:
      MARTYROLOGY_DATABASE_URL: postgresql+psycopg://martyrology:martyrology_secure_password@db:5432/martyrology
    networks:
      - martyrology
    restart: "no"
    depends_on:
      db:
        condition: service_healthy
      db-init:
        condition: service_completed_successfully
```

- [ ] **Step 2: Add the two application services**

Insert after `api-migrate`:

```yaml
  martyrology-api:
    image: martyrology-api:latest
    build: https://github.com/CatholicOS/martyrology-api.git#main
    restart: unless-stopped
    environment:
      MARTYROLOGY_ZITADEL_ISSUER: http://localhost:${ZITADEL_PORT:-8080}
      # The issuer above is the browser-facing origin and is what the `iss`
      # claim carries. Inside this container `localhost` is its own loopback,
      # so introspection is sent over the docker network instead. Transport
      # only — it never affects auth_enabled.
      MARTYROLOGY_ZITADEL_INTERNAL_URL: http://zitadel:8080
      MARTYROLOGY_ZITADEL_CLIENT_ID: ${MARTYROLOGY_ZITADEL_CLIENT_ID:-}
      MARTYROLOGY_ZITADEL_CLIENT_SECRET: ${MARTYROLOGY_ZITADEL_CLIENT_SECRET:-}
      MARTYROLOGY_ZITADEL_PROJECT_ID: ${MARTYROLOGY_ZITADEL_PROJECT_ID:-}
      MARTYROLOGY_OPENFGA_API_URL: http://openfga:8080
      MARTYROLOGY_OPENFGA_STORE_ID: ${MARTYROLOGY_OPENFGA_STORE_ID:-}
      MARTYROLOGY_OPENFGA_MODEL_ID: ${MARTYROLOGY_OPENFGA_MODEL_ID:-}
      MARTYROLOGY_OPENFGA_API_TOKEN: ${OPENFGA_PRESHARED_KEY:-local-dev-preshared-key}
      MARTYROLOGY_DATABASE_URL: postgresql+psycopg://martyrology:martyrology_secure_password@db:5432/martyrology
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)\""]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    ports:
      - "127.0.0.1:${API_PORT:-8000}:8000"
    networks:
      - martyrology
    depends_on:
      zitadel:
        condition: service_healthy
      openfga:
        condition: service_healthy
      authz-seed:
        condition: service_completed_successfully
      api-migrate:
        condition: service_completed_successfully

  martyrology-frontend:
    image: martyrology-frontend:latest
    build: https://github.com/CatholicOS/martyrology-frontend.git#main
    restart: unless-stopped
    # Auth.js must reach the issuer server-side (discovery, token exchange) at
    # the SAME origin the browser is redirected to, so `localhost` has to mean
    # the host here rather than this container's loopback. Same pattern as
    # LiturgicalCalendarFrontend's litcal-frontend service.
    extra_hosts:
      - "localhost:host-gateway"
    environment:
      # Server-side proxy calls route over the docker network.
      API_BASE: http://martyrology-api:8000
      AUTH_URL: http://localhost:${FRONTEND_PORT:-3000}
      AUTH_SECRET: ${AUTH_SECRET:-}
      AUTH_ZITADEL_ID: ${AUTH_ZITADEL_ID:-}
      AUTH_ZITADEL_SECRET: ${AUTH_ZITADEL_SECRET:-}
      AUTH_ZITADEL_ISSUER: http://localhost:${ZITADEL_PORT:-8080}
    ports:
      - "127.0.0.1:${FRONTEND_PORT:-3000}:3000"
    networks:
      - martyrology
    depends_on:
      martyrology-api:
        condition: service_healthy
```

- [ ] **Step 3: Bring the whole stack up**

```bash
docker compose up -d
sleep 45
docker compose ps
```

Expected: `db`, `zitadel`, `openfga`, `martyrology-api` healthy; `martyrology-frontend`, `zitadel-proxy`, `zitadel-login`, `mailpit`, `adminer` running; `db-init`, `openfga-migrate`, `authz-seed`, `api-migrate` exited 0.

- [ ] **Step 4: Verify both applications answer**

```bash
curl -sf http://localhost:8000/healthz && echo " <- API OK"
curl -sf http://localhost:8000/api/v1/editions | jq 'length'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/
docker compose logs martyrology-api | grep -i "partially configured" || echo "no partial-config warning"
```

Expected: API healthy, editions listed, frontend returns 200, and `no partial-config warning` — the OpenFGA token reached the container even before `setup-stack.sh` has run, because it comes straight from `OPENFGA_PRESHARED_KEY`.

- [ ] **Step 5: Verify the API reaches Zitadel over the internal URL**

```bash
docker compose exec -T martyrology-api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://zitadel:8080/.well-known/openid-configuration').status)"
```

Expected: `200`. This is the network path `MARTYROLOGY_ZITADEL_INTERNAL_URL` uses; if it fails, introspection fails the same way and every authenticated request 401s.

Note the asymmetry that makes this check worth running: the document served here reports its issuer as `http://localhost:8080` (the external origin), not `http://zitadel:8080`. That is correct and is exactly the split the setting exists for — the public issuer is what the `iss` claim carries, while the transport target is internal.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -S -m "Add OpenFGA, migrations and both application services to the full stack"
```

---

## Task 12: Local-sibling override

**Repo:** `martyrology-frontend`

**Files:**
- Create: `docker-compose.override.example.yml`

**Interfaces:**
- Consumes: the services from Tasks 10–11.
- Produces: an example override that repoints all `martyrology-api:latest` builds at `../martyrology-api` and attaches the private texts.

- [ ] **Step 1: Write `docker-compose.override.example.yml`**

```yaml
# Local sibling builds. Copy to docker-compose.override.yml (gitignored):
#
#   cp docker-compose.override.example.yml docker-compose.override.yml
#   docker compose up -d --build
#
# Compose merges the override automatically. Services omitted here keep
# building from their GitHub refs.
#
# Expected sibling layout:
#   ../martyrology-api
#   ../martyrology-texts   (private — restricted editions)
#   ../crmedr  ../clbdr    (public data repos)
#   ../cdcf-infra          (OpenFGA model + provisioning scripts)

services:
  # ⚠ All THREE services that use martyrology-api:latest must be overridden
  # together. Overriding only one leaves `docker compose up --build` rebuilding
  # martyrology-api:latest from GitHub via another service, clobbering the
  # local build with no error.
  db-init:
    build:
      context: ../martyrology-api

  api-migrate:
    build:
      context: ../martyrology-api

  martyrology-api:
    build:
      context: ../martyrology-api
    environment:
      # Attach the private 2004-family texts alongside the public-domain
      # editions baked into the image. This is the ONLY way the restricted
      # -texts path becomes exercisable: martyrology-texts is private, so the
      # GitHub-default stack necessarily serves the public editions only.
      MARTYROLOGY_DATA_PATH: /app/data/editions:/app/data/texts
    volumes:
      # :ro — the API never writes to its source tree. The venv is deliberately
      # NOT mounted, so the image's installed dependencies are used.
      - ../martyrology-api/src:/app/src:ro
      - ../martyrology-texts/data/editions:/app/data/texts:ro
      # Live data-repo edits. The image clones these at build time; mounting
      # host checkouts over them means a stale pinned ref never blocks work.
      - ../crmedr:/data/crmedr:ro
      - ../clbdr:/data/clbdr:ro

  # Use the sibling checkout instead of cloning, so local model or tuple edits
  # are seeded without pushing them first.
  authz-seed:
    volumes:
      - ../cdcf-infra:/cdcf-infra:ro
    entrypoint:
      - /bin/sh
      - -c
      - |
        set -eu
        apk add --no-cache bash curl jq >/dev/null
        rm -rf /tmp/auth && cp -r /cdcf-infra/auth /tmp/auth
        cd /tmp/auth
        cat > .env.local <<EOF
        OPENFGA_API_URL=http://openfga:8080
        OPENFGA_INTERNAL_URL=http://openfga:8080
        OPENFGA_PRESHARED_KEY=$$OPENFGA_PRESHARED_KEY
        EOF
        ./setup-openfga.sh --target local --create-martyrology-store

  martyrology-frontend:
    build:
      context: .
    # NO source bind mount. This is a production Next.js image and will not
    # hot-reload from one. For frontend iteration:
    #   docker compose stop martyrology-frontend
    #   npm run dev
    # Port 3000 is then free and the registered OIDC callback still matches.
```

- [ ] **Step 2: Verify the override builds locally**

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose build martyrology-api
docker compose up -d --force-recreate martyrology-api
sleep 8
docker compose exec -T martyrology-api ls /app/data/texts | head -3
```

Expected: the build uses the local context (no `git clone` of `martyrology-api` in the output), and `/app/data/texts` lists edition directories from the private repo.

- [ ] **Step 3: Verify the restricted edition is now attached**

```bash
curl -sf "http://localhost:8000/api/v1/elogia/edition/martyrologium_romanum_2004/01/02" \
  | jq '{access: .metadata.access, text: .elogia[0].text}'
```

Expected: `{"access": "restricted-texts", "text": null}` — the edition resolves (so it is attached) and is redacted for an anonymous caller (so authorization is live).

- [ ] **Step 4: Verify the sibling seeder path**

```bash
docker compose up -d --force-recreate authz-seed
docker compose logs --tail=5 authz-seed
```

Expected: `All 11 structural tuple(s) already present — nothing to write`, with no `git clone` line.

- [ ] **Step 5: Commit**

```bash
rm docker-compose.override.yml
git add docker-compose.override.example.yml
git commit -S -m "Add the local-sibling compose override example"
```

---

## Task 13: Full-stack scripts, smoke test and README

**Repo:** `martyrology-frontend`

**Files:**
- Create: `scripts/setup-stack.sh`, `scripts/grant-superuser.sh`, `scripts/smoke.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 9–12.
- Produces: `.env` gains the `MARTYROLOGY_*` and `AUTH_*` values; `scripts/smoke.sh` exits 0.

- [ ] **Step 1: Copy the API repo's scripts and adapt them**

Copy `scripts/setup-stack.sh` and `scripts/grant-superuser.sh` verbatim from `martyrology-api`, then make exactly three changes to `setup-stack.sh`:

1. Provision the frontend app too — the `./setup-zitadel.sh` invocation becomes:

```bash
    ./setup-zitadel.sh --target local \
        --create-org Martyrology \
        --provision-martyrology \
        --provision-martyrology-frontend
```

2. Capture the frontend app's credentials, after the existing `val` extractions:

```bash
AUTH_ID="$(val AUTH_ZITADEL_ID)"
AUTH_SECRET_VAL="$(val AUTH_ZITADEL_SECRET)"
[[ -n "$AUTH_ID" ]] || { echo "No frontend client ID in provisioner output" >&2; exit 1; }
```

3. Write the Auth.js block, after the existing `set_env` calls:

```bash
set_env AUTH_URL             "http://localhost:${FRONTEND_PORT:-3000}"
set_env AUTH_ZITADEL_ISSUER  "$ISSUER"
set_env AUTH_ZITADEL_ID      "$AUTH_ID"

# AUTH_SECRET is ours to generate, not Zitadel's to emit. Generate once and
# keep it: regenerating invalidates every existing session cookie.
if ! grep -qE '^AUTH_SECRET=.+' "$ENV_FILE"; then
    set_env AUTH_SECRET "$(openssl rand -base64 32)"
fi

if [[ -n "$AUTH_SECRET_VAL" ]]; then
    set_env AUTH_ZITADEL_SECRET "$AUTH_SECRET_VAL"
    echo "✓ Frontend client secret captured (one-time emit)."
else
    echo "⚠ No frontend client secret emitted — the app already existed." >&2
    grep -qE '^AUTH_ZITADEL_SECRET=.+' "$ENV_FILE" \
        || echo "  .env has NO frontend secret. Rotate it in the Zitadel console." >&2
fi
```

Then `chmod +x scripts/setup-stack.sh scripts/grant-superuser.sh`.

- [ ] **Step 2: Write `scripts/smoke.sh`**

```bash
#!/usr/bin/env bash
#
# smoke.sh — full-stack bring-up invariants.
#
# Checks wiring, not behaviour. Run after `setup-stack.sh --update-env` and a
# `docker compose up -d --force-recreate` of the application services.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
set -a; . ./.env; set +a

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  ✓ %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  ✗ %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  ~ %s\n' "$1"; SKIP=$((SKIP+1)); }

API="http://localhost:${API_PORT:-8000}"
FE="http://localhost:${FRONTEND_PORT:-3000}"
FGA="${MARTYROLOGY_OPENFGA_API_URL:-http://localhost:8083}"
ISSUER="http://localhost:${ZITADEL_PORT:-8080}"

echo "1. Zitadel discovery on the single origin"
[[ "$(curl -sf "$ISSUER/.well-known/openid-configuration" | jq -r '.issuer')" == "$ISSUER" ]] \
    && ok "issuer is $ISSUER" || bad "discovery missing or issuer mismatch"

echo "2. OpenFGA structural tuples"
COUNT=$(curl -sf -X POST "$FGA/stores/$MARTYROLOGY_OPENFGA_STORE_ID/read" \
    -H "Authorization: Bearer $MARTYROLOGY_OPENFGA_API_TOKEN" \
    -H "Content-Type: application/json" -d '{}' | jq '.tuples | length')
[[ "$COUNT" == "11" ]] && ok "11 structural tuples" || bad "expected 11 tuples, got ${COUNT:-none}"

echo "3. Alembic is at head"
CUR=$(docker compose run --rm --entrypoint alembic api-migrate current 2>/dev/null | tr -d '\r')
grep -q '(head)' <<<"$CUR" && ok "alembic current is at head" || bad "alembic not at head: $CUR"

echo "4. API health"
curl -sf "$API/healthz" >/dev/null && ok "GET /healthz 200" || bad "GET /healthz failed"

echo "5. Anonymous read of a restricted edition is redacted"
BODY=$(curl -sf "$API/api/v1/elogia/edition/martyrologium_romanum_2004/01/02" 2>/dev/null)
if [[ -z "$BODY" ]]; then
    skip "martyrologium_romanum_2004 not attached (no override / no martyrology-texts)"
else
    ACCESS=$(jq -r '.metadata.access // empty' <<<"$BODY")
    TEXT=$(jq -r '.elogia[0].text // "null"' <<<"$BODY")
    [[ "$ACCESS" == "restricted-texts" && "$TEXT" == "null" ]] \
        && ok "access=restricted-texts with text=null" \
        || bad "expected redaction, got access=$ACCESS text=$TEXT"
fi

echo "6. Login V2 is served through the proxy"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$ISSUER/ui/v2/login/login")
[[ "$CODE" != "404" && -n "$CODE" ]] \
    && ok "/ui/v2/login/login -> $CODE" \
    || bad "/ui/v2/login/login returned 404 — proxy is routing to the backend"

echo "7. Auth.js provider"
PROVIDERS=$(curl -sf "$FE/api/auth/providers" 2>/dev/null)
if [[ -z "$PROVIDERS" ]]; then
    # Auth.js is introduced by the OIDC login-client plan, not by this stack.
    skip "no /api/auth/providers — Auth.js not yet wired into the frontend"
else
    jq -e '.zitadel' >/dev/null <<<"$PROVIDERS" \
        && ok "zitadel provider registered" || bad "zitadel missing from providers"
fi

echo
printf 'passed %d, failed %d, skipped %d\n' "$PASS" "$FAIL" "$SKIP"
[[ $FAIL -eq 0 ]]
```

`chmod +x scripts/smoke.sh`.

- [ ] **Step 3: Run the whole bring-up from scratch**

```bash
docker compose down -v
rm -rf .zitadel-data .stack-out
cp -n .env.example .env
docker compose up -d
./scripts/setup-stack.sh --update-env
docker compose up -d --force-recreate martyrology-api martyrology-frontend
./scripts/smoke.sh
```

Expected: assertions 1–4 and 6 pass; assertion 5 skips without the override; assertion 7 skips (Auth.js is not in the frontend yet — it arrives with the OIDC login-client plan). Exit code 0.

- [ ] **Step 4: Add the README section**

````markdown
## Local development stack

Runs the whole system — Zitadel, OpenFGA, Postgres, the API and this frontend —
in Docker. Mirrors `cdcf-infra` production topology: Zitadel and its v2 login UI
share one origin behind an nginx proxy, with image versions pinned to
production's.

Requires Docker with Compose v2. Ports match LiturgicalCalendar's stack, so only
one of the two can run at a time.

```bash
cp .env.example .env
docker compose up -d
./scripts/setup-stack.sh --update-env
docker compose up -d --force-recreate martyrology-api martyrology-frontend
./scripts/smoke.sh
```

Then sign in at <http://localhost:3000>, find your `sub` in the Zitadel console
(Martyrology Org → Users → your user → ID), and grant yourself platform
superuser:

```bash
./scripts/grant-superuser.sh <your-sub>
```

| Service | URL | Credentials |
| --- | --- | --- |
| Frontend | <http://localhost:3000> | — |
| API | <http://localhost:8000> | — |
| Zitadel console | <http://localhost:8080/ui/console> | `root@martyrology.localhost` / `RootPassword1!` |
| OpenFGA API | <http://localhost:8083> | Bearer `OPENFGA_PRESHARED_KEY` from `.env` |
| Adminer | <http://localhost:8088> | server `db`, user `postgres`, password `postgres` |
| Mailpit | <http://localhost:8025> | — |

### Building from local checkouts

By default every service builds from its GitHub ref, so a bare clone stands the
whole system up. To build from sibling checkouts instead:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose up -d --build
```

The override also mounts `../martyrology-texts`, which is **the only way** the
restricted-texts path becomes exercisable — that repo is private, so the
GitHub-default stack serves the two public-domain editions only.

### Iterating on the frontend

This image is a production Next.js build and does not hot-reload from a bind
mount. Stop the container and use the dev server:

```bash
docker compose stop martyrology-frontend
npm run dev
```

Port 3000 is then free and the registered OIDC callback still matches.

### Gotchas

- **The OIDC client secrets are emitted once.** `setup-stack.sh` captures them
  into `.env` on the run that creates each app; a re-run cannot recover them.
  If `.env` is lost, regenerate in the Zitadel console.
- **`OPENFGA_PRESHARED_KEY` is required.** The API's `authz_enabled` is false
  when its token is empty, which denies every authorization check while the
  stack reports healthy.
- **Port 3000 is fixed.** `cdcf-infra` registers
  `http://localhost:3000/api/auth/callback/zitadel` for `--target local`.
````

- [ ] **Step 5: Commit and open the PR**

```bash
git add scripts/setup-stack.sh scripts/grant-superuser.sh scripts/smoke.sh README.md
git commit -S -m "Add full-stack provisioning, smoke test and documentation"
git push -u origin feat/local-dev-stack
gh pr create --title "Local development stack: full containerized stack" \
  --body "Implements Tasks 9-13 of martyrology-api's docs/superpowers/plans/2026-08-04-local-development-stack.md.

Depends on the martyrology-api PR landing first — db-init, api-migrate and martyrology-api all build the API image, which needs its Dockerfile on main.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Task 14: Un-stale the OIDC login-client plan

**Repo:** `cdcf-infra`, branch `docs/unstale-oidc-tasks`

**Files:**
- Modify: `docs/superpowers/plans/2026-08-03-martyrology-oidc-login-client.md:398-423`

**Interfaces:**
- Consumes: the working stack from Tasks 1–13.
- Produces: a revised stale-notice block naming the local stack as the verification target.

- [ ] **Step 1: Replace the stale-notice block**

Replace the blockquote at lines 398–423 with:

```markdown
> ## Tasks 3-7: local verification now runs against the local stack
>
> **Added 2026-08-03, resolved 2026-08-04.** These tasks were written assuming a
> localhost Zitadel client existed. It does — but in the *local* Zitadel, not the
> production one, which is the spec's D3 rather than a departure from it.
>
> Two things landed since:
>
> - **`--provision-martyrology-frontend` is target-aware** (PR #23). `--target local`
>   registers `http://localhost:3000/api/auth/callback/zitadel` with `devMode=true`
>   against a local Zitadel; `--target production` is byte-identical to before.
> - **The local stack exists** — see `martyrology-api`'s
>   `docs/superpowers/specs/2026-08-04-local-development-stack-design.md`, and the
>   bring-up in `martyrology-frontend`'s README.
>
> So the affected steps are performed as written, against
> `http://localhost:3000` with the local stack running, taking
> `AUTH_ZITADEL_ID` / `AUTH_ZITADEL_SECRET` from the `.env` that
> `./scripts/setup-stack.sh --update-env` writes:
>
> - **Task 3 Step 9**, **Task 5 Step 8** — run against the local stack.
> - **Task 6 Step 2** — "the dev value" means the local stack's `AUTH_SECRET`,
>   which `setup-stack.sh` generates. Production's must differ.
> - **Task 7 Steps 1, 3, 5** — there is **one app per instance**, not two apps in
>   one instance. The handoff table lists the production app; local sign-in is
>   documented as a property of the local stack. Do not claim "both apps live in
>   the MartyrologyAPI project" of a single Zitadel.
>
> The code and unit tests in Tasks 3, 4 and 5 were never affected — they mock the
> session and never contact Zitadel.
```

- [ ] **Step 2: Verify no other text contradicts it**

Run: `grep -n "stale\|No such client exists\|localhost client" docs/superpowers/plans/2026-08-03-martyrology-oidc-login-client.md`
Expected: only the revised block and the Global Constraints note at line 23, which remains correct — there is still no localhost client in the *production* Zitadel.

- [ ] **Step 3: Commit and open the PR**

```bash
git add docs/superpowers/plans/2026-08-03-martyrology-oidc-login-client.md
git commit -S -m "Point Tasks 3-7's local verification at the local stack"
git push -u origin docs/unstale-oidc-tasks
gh pr create --title "Un-stale Tasks 3-7 of the Martyrology OIDC plan" \
  --body "The local stack these tasks were waiting on now exists.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Execution order

Tasks 1–8 (`martyrology-api`) must land before Tasks 9–13, because `db-init`,
`api-migrate` and `martyrology-api` all build the API image from `main`. Task 14
requires both stacks working.

## Deferred to its own spec

The permission-request and notification subsystem — submission, review,
accept/reject, revoke, notifications — is **not** in this plan. It lands as
Alembic migrations plus routes on the contract Task 2 and Task 6 establish, and
requires no compose change.
