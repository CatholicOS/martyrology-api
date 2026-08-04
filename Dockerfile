# martyrology-api image.
#
# Consumed by martyrology-frontend's full docker stack and by CI. The API
# repo's own compose stack is infra-only — local API development runs
# `uvicorn --factory --reload` on the host, so this image is never in that
# edit loop. See docs/superpowers/specs/2026-08-04-local-development-stack-design.md, D4.

FROM python:3.12.13-slim AS build

# Pinned refs for the two data repositories — these SHAs are the commits this
# repo's own vendor/ submodules record, so the image and vendor/ agree. To
# bump the data revision intentionally, pass a new SHA with --build-arg.
ARG CRMEDR_REF=51740e79584f64940f9e3f98615b000ef5f77e92
ARG CLBDR_REF=ecb147b47b47368fbdefeb2074c5770ebb7c8f9d

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
#
# CRMEDR_REF/CLBDR_REF are commit SHAs, not branch names, so `git clone
# --branch` can't be used (it only accepts refs GitHub advertises, not
# arbitrary SHAs). init+fetch+checkout fetches the exact commit instead.
RUN git init /data/crmedr && \
    git -C /data/crmedr remote add origin https://github.com/CatholicOS/crmedr.git && \
    git -C /data/crmedr fetch --depth 1 origin "$CRMEDR_REF" && \
    git -C /data/crmedr checkout FETCH_HEAD && \
    git init /data/clbdr && \
    git -C /data/clbdr remote add origin https://github.com/CatholicOS/clbdr.git && \
    git -C /data/clbdr fetch --depth 1 origin "$CLBDR_REF" && \
    git -C /data/clbdr checkout FETCH_HEAD && \
    rm -rf /data/crmedr/.git /data/clbdr/.git


FROM python:3.12.13-slim AS main

WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY --from=build /data /data
# Load-bearing, not redundant with the copied .venv: `uv sync` in the build
# stage produced an editable install whose .pth file points at the literal
# path /app/src, so this WORKDIR/COPY pair must keep matching the build
# stage's or every import breaks silently at first boot.
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
