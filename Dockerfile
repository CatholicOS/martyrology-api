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
