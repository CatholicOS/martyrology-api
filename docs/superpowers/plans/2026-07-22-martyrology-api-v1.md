# Roman Martyrology API v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI service specified in `docs/superpowers/specs/2026-07-22-martyrology-api-v1-design.md`: public reads of martyrology editions with edition resolution, Zitadel/OpenFGA-gated licensed texts, and git-backed curation writes.

**Architecture:** Flat-file editions (two on-disk shapes: day-structured and flat `id → text`) are indexed in memory by a `Store` built over the CRMEDR/CLBDR registries; a pure-function path grammar feeds a resolver that picks the edition; auth/authz/licensing wrap the read pipeline; curation writes go through a `VcsBackend` abstraction (local bare git repos in dev/tests, GitHub REST in prod).

**Tech Stack:** Python 3.12, FastAPI, pydantic v2 + pydantic-settings, httpx (Zitadel introspection, OpenFGA check, GitHub API — OpenFGA is called via its plain HTTP `check` endpoint with httpx rather than `openfga-sdk`: one fewer dependency, identical wire call, trivially stubbed; deliberate deviation from the spec's SDK mention), pytest.

## Global Constraints

- Python `>=3.12`; FastAPI + pydantic v2; all HTTP out-calls via `httpx` with injectable transport.
- Base path prefix: `/api/v1`. Errors: RFC 9457 `application/problem+json` everywhere.
- Canonical IDs: `mr:MMDD-slug` (regex `^mr:(\d{4})-([a-z0-9-]+)$`).
- On-disk month files: `{edition_dir}/{MM}.json` with zero-padded file names; **day-structured** shape keys days as *unpadded strings* (`"1"`…`"31"`), **flat** shape keys are canonical IDs.
- Restricted editions default: `martyrologium_romanum_2004`, `martyrologium_romanum_2004_it_IT`, `martyrologium_romanum_2004_en_unofficial`.
- Cache headers: `edition/…`-addressed reads `public, max-age=31536000, immutable`; resolver reads `public, max-age=86400`; authorized restricted reads `private, max-age=0`; all with ETag + `304` on `If-None-Match`.
- Curation branches: `curation/{username}/{topic}` (topic defaults to `edits`); merge = publish.
- Env vars use prefix `MARTYROLOGY_` (so the spec's `MARTYROLOGY_DATA_PATH` = field `data_path`).
- TDD throughout: every task writes the failing test first. Commit after every green test cycle.
- All source under `src/martyrology_api/`, tests under `tests/`, fixtures under `tests/fixtures/`.

## File Structure

```
pyproject.toml
src/martyrology_api/
  __init__.py
  config.py          # Settings (env)
  problems.py        # RFC 9457 problems + FastAPI handlers
  registry.py        # CRMEDR ids + CLBDR editions loaders
  store.py           # month-file parsing (both shapes), in-memory indexes
  resolver.py        # (nation, year, locale) -> edition
  grammar.py         # /elogia path parser (pure function)
  auth.py            # Zitadel introspection
  authz.py           # OpenFGA check wrapper
  licensing.py       # restricted-edition logic + redaction
  models.py          # pydantic response models
  caching.py         # cache-control + ETag middleware
  writer/
    __init__.py
    base.py          # VcsBackend protocol, ConflictError
    local.py         # LocalGitBackend (bare repos, subprocess git)
    github.py        # GitHubBackend (contents/refs/pulls REST)
    validation.py    # month-payload validation
    service.py       # CurationService
  routers/
    __init__.py
    read.py          # /elogia/**, /elogium/{id}
    discovery.py     # /editions, /elogia catalog
    curation.py      # PUT/PATCH/DELETE /editions/**
  app.py             # create_app()
tests/
  conftest.py
  fixtures/          # mini CRMEDR, CLBDR, public+private edition dirs
  test_config.py  test_problems.py  test_registry.py  test_store.py
  test_resolver.py  test_grammar.py  test_read_api.py  test_discovery_api.py
  test_auth.py  test_authz.py  test_licensing_api.py  test_caching.py
  test_writer_local.py  test_validation.py  test_curation_api.py
  test_github_backend.py  test_smoke_realdata.py
```

---

### Task 1: Project scaffolding, Settings, problem responses

**Files:**
- Create: `pyproject.toml`, `src/martyrology_api/__init__.py`, `src/martyrology_api/config.py`, `src/martyrology_api/problems.py`
- Test: `tests/test_config.py`, `tests/test_problems.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Settings` (fields below, env prefix `MARTYROLOGY_`); `ApiProblem(status, title, detail=None, type_slug="about:blank", **extensions)` exception; `install_problem_handlers(app)`; `problem_response(exc) -> JSONResponse` with media type `application/problem+json`.

- [ ] **Step 1: Write `pyproject.toml` and package init**

```toml
[project]
name = "martyrology-api"
version = "0.1.0"
description = "Roman Martyrology API"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.111",
  "uvicorn>=0.30",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/martyrology_api"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`src/martyrology_api/__init__.py`:

```python
__version__ = "0.1.0"
```

Run: `pip install -e '.[dev]'`
Expected: installs cleanly.

- [ ] **Step 2: Write failing tests for Settings and problems**

`tests/test_config.py`:

```python
from martyrology_api.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.data_path == "data/editions"
    assert "martyrologium_romanum_2004" in s.restricted_set
    assert s.auth_enabled is False
    assert s.authz_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("MARTYROLOGY_DATA_PATH", "/a:/b")
    s = Settings(_env_file=None)
    assert [str(p) for p in s.data_path_list] == ["/a", "/b"]
```

`tests/test_problems.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from martyrology_api.problems import ApiProblem, install_problem_handlers


def make_app():
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/boom")
    def boom():
        raise ApiProblem(404, "Not found", detail="no such day", type_slug="unknown-day")

    return app


def test_problem_shape():
    client = TestClient(make_app())
    r = client.get("/boom")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Not found"
    assert body["detail"] == "no such day"
    assert body["type"].endswith("unknown-day")
    assert body["status"] == 404


def test_validation_errors_are_problems():
    app = make_app()

    @app.get("/typed/{n}")
    def typed(n: int):
        return {"n": n}

    client = TestClient(app)
    r = client.get("/typed/xx")
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_config.py tests/test_problems.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'martyrology_api.config'`.

- [ ] **Step 4: Implement `config.py`**

```python
import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MARTYROLOGY_", "env_file": ".env", "extra": "ignore"}

    data_path: str = "data/editions"  # os.pathsep-separated base dirs, one edition dir each
    crmedr_path: Path = Path("../crmedr")
    clbdr_path: Path = Path("../clbdr")
    restricted_editions: str = (
        "martyrologium_romanum_2004,"
        "martyrologium_romanum_2004_it_IT,"
        "martyrologium_romanum_2004_en_unofficial"
    )
    access_info_url: str = "https://github.com/CatholicOS/martyrology-api#licensing"

    zitadel_issuer: str = ""
    zitadel_client_id: str = ""
    zitadel_client_secret: str = ""

    openfga_api_url: str = ""
    openfga_store_id: str = ""
    openfga_model_id: str = ""

    github_token: str = ""
    public_repo: str = "CatholicOS/martyrology-api"
    private_repo: str = "CatholicOS/martyrology-texts"
    repo_data_prefix: str = "data/editions"
    local_git_root: str = ""  # when set, use LocalGitBackend rooted here

    @property
    def data_path_list(self) -> list[Path]:
        return [Path(p) for p in self.data_path.split(os.pathsep) if p]

    @property
    def restricted_set(self) -> set[str]:
        return {e.strip() for e in self.restricted_editions.split(",") if e.strip()}

    @property
    def auth_enabled(self) -> bool:
        return bool(self.zitadel_issuer)

    @property
    def authz_enabled(self) -> bool:
        return bool(self.openfga_api_url and self.openfga_store_id)
```

- [ ] **Step 5: Implement `problems.py`**

```python
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_TYPE_BASE = "https://romanmartyrology.com/problems/"


class ApiProblem(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None,
                 type_slug: str = "about:blank", **extensions):
        self.status = status
        self.title = title
        self.detail = detail
        self.type_slug = type_slug
        self.extensions = extensions
        super().__init__(title)


def problem_response(exc: ApiProblem) -> JSONResponse:
    body = {
        "type": exc.type_slug if exc.type_slug == "about:blank"
                else PROBLEM_TYPE_BASE + exc.type_slug,
        "title": exc.title,
        "status": exc.status,
    }
    if exc.detail:
        body["detail"] = exc.detail
    body.update(exc.extensions)
    return JSONResponse(body, status_code=exc.status,
                        media_type="application/problem+json")


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def _api_problem(request: Request, exc: ApiProblem):
        return problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return problem_response(
            ApiProblem(400, "Malformed request", detail=str(exc.errors()),
                       type_slug="malformed-request"))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_config.py tests/test_problems.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/martyrology_api tests/test_config.py tests/test_problems.py
git commit -m "feat: scaffold martyrology_api package with Settings and RFC 9457 problems"
```

---

### Task 2: Test fixtures (mini CRMEDR, CLBDR, edition data)

**Files:**
- Create: `tests/fixtures/crmedr/data/martyrology_ids.json`, `tests/fixtures/crmedr/data/deprecated_ids.json`, `tests/fixtures/crmedr/i18n/la.json`, `tests/fixtures/crmedr/i18n/en.json`, `tests/fixtures/clbdr/data/editions.json`, `tests/fixtures/editions_public/martyrologium_romanum_1749/01.json`, `tests/fixtures/editions_public/martyrologium_romanum_1749/02.json`, `tests/fixtures/editions_private/martyrologium_romanum_2004/01.json`, `tests/fixtures/editions_private/martyrologium_romanum_2004/02.json`, `tests/fixtures/editions_private/martyrologium_romanum_2004_it_IT/01.json`, `tests/conftest.py`
- Test: (fixtures only — verified by a conftest smoke assertion)

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixtures `fixtures_dir: Path`, `crmedr_path: Path`, `clbdr_path: Path`, `data_paths: list[Path]` (public+private edition base dirs). The fixture data itself: cross-day case `mr:0102-concordius` printed under Jan 1 in 1749; deprecated id `mr:0101-circumcisio-domini` (1749 only); leap-day ids `mr:0228-romanus`, `mr:0229-oswaldus`; nation edition `martyrologium_romanum_2004_it_IT`; CLBDR includes `martyrologium_romanum_1584` with **no texts** and one non-martyrology entry to prove book filtering.

- [ ] **Step 1: Write the fixture files**

`tests/fixtures/crmedr/data/martyrology_ids.json` (mirrors the real shape: top-level `entries` list):

```json
{
  "$comment": "test fixture",
  "anchor_edition": "martyrologium_romanum_2004",
  "entries": [
    {"id": "mr:0101-maria-dei-genetrix", "month": 1, "day": 1, "entry": 1, "asterisk": false, "country": null, "unnumbered": true},
    {"id": "mr:0101-basilius", "month": 1, "day": 1, "entry": 2, "asterisk": true, "country": null, "unnumbered": false},
    {"id": "mr:0102-concordius", "month": 1, "day": 2, "entry": 1, "asterisk": false, "country": null, "unnumbered": false},
    {"id": "mr:0102-argeus-et-socii", "month": 1, "day": 2, "entry": 2, "asterisk": false, "country": "TR", "unnumbered": false},
    {"id": "mr:0228-romanus", "month": 2, "day": 28, "entry": 1, "asterisk": false, "country": null, "unnumbered": false},
    {"id": "mr:0229-oswaldus", "month": 2, "day": 29, "entry": 1, "asterisk": false, "country": null, "unnumbered": false}
  ]
}
```

`tests/fixtures/crmedr/data/deprecated_ids.json` (real shape: bare list):

```json
[
  {"id": "mr:0101-circumcisio-domini", "month": 1, "day": 1, "entry": 1, "deprecated": true, "attested_in": "martyrologium_romanum_1749", "subject_la": "Circumcisio Domini"}
]
```

`tests/fixtures/crmedr/i18n/la.json`:

```json
{
  "mr:0101-maria-dei-genetrix": "Sancta Maria Dei Genetrix",
  "mr:0101-basilius": "Sanctus Basilius",
  "mr:0102-concordius": "Sanctus Concordius",
  "mr:0102-argeus-et-socii": "Sanctus Argeus et socii",
  "mr:0228-romanus": "Sanctus Romanus",
  "mr:0229-oswaldus": "Sanctus Oswaldus",
  "mr:0101-circumcisio-domini": "Circumcisio Domini"
}
```

`tests/fixtures/crmedr/i18n/en.json`:

```json
{
  "mr:0101-maria-dei-genetrix": "Holy Mary, Mother of God",
  "mr:0102-concordius": "Saint Concordius"
}
```

`tests/fixtures/clbdr/data/editions.json` (real shape; note the missal entry that must be filtered out, and 1584 which has no texts anywhere):

```json
{
  "$comment": "test fixture",
  "entries": [
    {"id": "missale_romanum_1570", "book": "book:missale-romanum", "nature": "editio_typica", "language": "la", "scope": "universal", "promulgated": "1570-07-14"},
    {"id": "martyrologium_romanum_1584", "book": "book:martyrologium-romanum", "nature": "editio_typica", "language": "la", "scope": "universal", "promulgated": "1584", "successor": "martyrologium_romanum_1749"},
    {"id": "martyrologium_romanum_1749", "book": "book:martyrologium-romanum", "nature": "editio_typica_recognita", "language": "la", "scope": "universal", "promulgated": "1749", "predecessor": "martyrologium_romanum_1584", "successor": "martyrologium_romanum_2004"},
    {"id": "martyrologium_romanum_2004", "book": "book:martyrologium-romanum", "nature": "editio_typica_altera", "language": "la", "scope": "universal", "promulgated": "2004", "decree": "Congregatio de Cultu Divino, 29 iunii 2004", "predecessor": "martyrologium_romanum_1749"},
    {"id": "martyrologium_romanum_2004_it_IT", "book": "book:martyrologium-romanum", "nature": "editio_vernacula", "language": "it-IT", "scope": "IT", "promulgated": "2004", "translation_of": "martyrologium_romanum_2004"},
    {"id": "martyrologium_romanum_2004_en_unofficial", "book": "book:martyrologium-romanum", "nature": "translatio", "language": "en", "scope": "universal", "promulgated": "2004", "translation_of": "martyrologium_romanum_2004"}
  ]
}
```

`tests/fixtures/editions_public/martyrologium_romanum_1749/01.json` (day-structured, unpadded day keys; **concordius cross-day-printed under Jan 1**):

```json
{
  "1": {
    "titulus": "1 Januarii. Kalendis Januarii.",
    "elogia": {
      "mr:0101-circumcisio-domini": "Circumcisio Domini nostri Jesu Christi.",
      "mr:0102-concordius": "Spoleti sancti Concordii, Presbyteri et Martyris."
    },
    "conclusio": "Et alibi aliorum plurimorum sanctorum Martyrum. R. Deo gratias."
  },
  "2": {
    "titulus": "2 Januarii. Quarto Nonas Januarii.",
    "elogia": {
      "mr:0102-argeus-et-socii": "Tomis sanctorum Argei et sociorum Martyrum."
    },
    "conclusio": "Et alibi aliorum plurimorum sanctorum Martyrum. R. Deo gratias."
  }
}
```

`tests/fixtures/editions_public/martyrologium_romanum_1749/02.json`:

```json
{
  "28": {
    "titulus": "28 Februarii.",
    "elogia": {"mr:0228-romanus": "Sancti Romani abbatis."},
    "conclusio": "Et alibi aliorum. R. Deo gratias."
  },
  "29": {
    "titulus": "29 Februarii (bissextilis).",
    "elogia": {"mr:0229-oswaldus": "Sancti Oswaldi episcopi."},
    "conclusio": "Et alibi aliorum. R. Deo gratias."
  }
}
```

`tests/fixtures/editions_private/martyrologium_romanum_2004/01.json` (flat `id → text`):

```json
{
  "mr:0101-maria-dei-genetrix": "In octava Nativitatis Domini, sollemnitas sanctae Dei Genetricis Mariae.",
  "mr:0101-basilius": "Memoria sanctorum Basilii Magni et Gregorii Nazianzeni.",
  "mr:0102-concordius": "Spoleti in Umbria, sancti Concordii, presbyteri et martyris.",
  "mr:0102-argeus-et-socii": "Tomis in Scythia, sanctorum Argei et sociorum martyrum."
}
```

`tests/fixtures/editions_private/martyrologium_romanum_2004/02.json`:

```json
{
  "mr:0228-romanus": "In monte Iurensi, sancti Romani abbatis.",
  "mr:0229-oswaldus": "Eboraci in Anglia, sancti Oswaldi episcopi."
}
```

`tests/fixtures/editions_private/martyrologium_romanum_2004_it_IT/01.json`:

```json
{
  "mr:0101-maria-dei-genetrix": "Ottava del Natale, solennità di Maria santissima Madre di Dio.",
  "mr:0102-concordius": "A Spoleto, san Concordio, sacerdote e martire."
}
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def crmedr_path() -> Path:
    return FIXTURES / "crmedr"


@pytest.fixture
def clbdr_path() -> Path:
    return FIXTURES / "clbdr"


@pytest.fixture
def data_paths() -> list[Path]:
    return [FIXTURES / "editions_public", FIXTURES / "editions_private"]


def test_fixture_sanity():
    ids = json.loads((FIXTURES / "crmedr/data/martyrology_ids.json").read_text())
    assert any(e["id"] == "mr:0102-concordius" for e in ids["entries"])
```

- [ ] **Step 3: Run the sanity check**

Run: `pytest tests/conftest.py -v`
Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures tests/conftest.py
git commit -m "test: add mini CRMEDR/CLBDR/edition fixtures (both on-disk shapes, cross-day case)"
```

---

### Task 3: Registry loader (CRMEDR + CLBDR)

**Files:**
- Create: `src/martyrology_api/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: fixture paths from Task 2.
- Produces:
  - `IdEntry` frozen dataclass: `id: str, month: int, day: int, entry: int, asterisk: bool = False, country: str | None = None, unnumbered: bool = False, deprecated: bool = False, attested_in: str | None = None`
  - `EditionMeta` frozen dataclass: `id: str, nature: str, language: str, scope: str, promulgated: str, promulgated_year: int, decree: str | None = None, predecessor: str | None = None, successor: str | None = None, translation_of: str | None = None, note: str | None = None`
  - `class Registry` with attributes `entries: dict[str, IdEntry]` (current **and** deprecated merged), `editions: dict[str, EditionMeta]` (martyrology book only); methods `subjects(locale: str) -> dict[str, str]` (falls back to `{}` for unknown locale; deprecated ids get their `subject_la` merged into the `la` map), `ids_for_day(month: int, day: int) -> list[IdEntry]` (current ids only, sorted unnumbered-first then by `entry`); classmethod `Registry.load(crmedr_path: Path, clbdr_path: Path) -> Registry`
  - Module functions `anchor_day(canonical_id: str) -> tuple[int, int]`, `slug_of(canonical_id: str) -> str`, `is_canonical_id(s: str) -> bool` (regex `^mr:(\d{4})-([a-z0-9-]+)$`)

- [ ] **Step 1: Write the failing tests**

`tests/test_registry.py`:

```python
from martyrology_api.registry import Registry, anchor_day, is_canonical_id, slug_of


def test_load_entries_and_deprecated(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    assert reg.entries["mr:0101-basilius"].asterisk is True
    dep = reg.entries["mr:0101-circumcisio-domini"]
    assert dep.deprecated is True
    assert dep.attested_in == "martyrologium_romanum_1749"


def test_editions_filtered_to_martyrology(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    assert "missale_romanum_1570" not in reg.editions
    e = reg.editions["martyrologium_romanum_2004_it_IT"]
    assert (e.nature, e.scope, e.language, e.promulgated_year) == ("editio_vernacula", "IT", "it-IT", 2004)
    assert reg.editions["martyrologium_romanum_1584"].promulgated_year == 1584


def test_subjects_locale_fallback(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    assert reg.subjects("la")["mr:0101-circumcisio-domini"] == "Circumcisio Domini"
    assert reg.subjects("en")["mr:0102-concordius"] == "Saint Concordius"
    assert reg.subjects("de") == {}


def test_ids_for_day_ordering(crmedr_path, clbdr_path):
    reg = Registry.load(crmedr_path, clbdr_path)
    ids = [e.id for e in reg.ids_for_day(1, 1)]
    assert ids == ["mr:0101-maria-dei-genetrix", "mr:0101-basilius"]  # unnumbered first
    assert all(not e.deprecated for e in reg.ids_for_day(1, 1))


def test_id_helpers():
    assert anchor_day("mr:0102-concordius") == (1, 2)
    assert slug_of("mr:0102-argeus-et-socii") == "argeus-et-socii"
    assert is_canonical_id("mr:0102-argeus-et-socii")
    assert not is_canonical_id("mr:102-x")
    assert not is_canonical_id("foo:0102-x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `registry.py`**

```python
import json
import re
from dataclasses import dataclass
from pathlib import Path

ID_RE = re.compile(r"^mr:(\d{4})-([a-z0-9-]+)$")
MARTYROLOGY_BOOK = "book:martyrologium-romanum"


def is_canonical_id(s: str) -> bool:
    return bool(ID_RE.match(s))


def anchor_day(canonical_id: str) -> tuple[int, int]:
    m = ID_RE.match(canonical_id)
    if not m:
        raise ValueError(f"not a canonical id: {canonical_id}")
    mmdd = m.group(1)
    return int(mmdd[:2]), int(mmdd[2:])


def slug_of(canonical_id: str) -> str:
    m = ID_RE.match(canonical_id)
    if not m:
        raise ValueError(f"not a canonical id: {canonical_id}")
    return m.group(2)


@dataclass(frozen=True)
class IdEntry:
    id: str
    month: int
    day: int
    entry: int
    asterisk: bool = False
    country: str | None = None
    unnumbered: bool = False
    deprecated: bool = False
    attested_in: str | None = None


@dataclass(frozen=True)
class EditionMeta:
    id: str
    nature: str
    language: str
    scope: str
    promulgated: str
    promulgated_year: int
    decree: str | None = None
    predecessor: str | None = None
    successor: str | None = None
    translation_of: str | None = None
    note: str | None = None


class Registry:
    def __init__(self, entries: dict[str, IdEntry],
                 editions: dict[str, EditionMeta],
                 i18n: dict[str, dict[str, str]]):
        self.entries = entries
        self.editions = editions
        self._i18n = i18n

    def subjects(self, locale: str) -> dict[str, str]:
        return self._i18n.get(locale.split("-")[0], {})

    def ids_for_day(self, month: int, day: int) -> list[IdEntry]:
        found = [e for e in self.entries.values()
                 if not e.deprecated and e.month == month and e.day == day]
        return sorted(found, key=lambda e: (not e.unnumbered, e.entry))

    @classmethod
    def load(cls, crmedr_path: Path, clbdr_path: Path) -> "Registry":
        raw = json.loads((crmedr_path / "data/martyrology_ids.json").read_text())
        entries: dict[str, IdEntry] = {}
        for e in raw["entries"]:
            entries[e["id"]] = IdEntry(
                id=e["id"], month=e["month"], day=e["day"], entry=e["entry"],
                asterisk=e.get("asterisk", False), country=e.get("country"),
                unnumbered=e.get("unnumbered", False))
        dep_raw = json.loads((crmedr_path / "data/deprecated_ids.json").read_text())
        dep_subjects_la: dict[str, str] = {}
        for e in dep_raw:
            entries[e["id"]] = IdEntry(
                id=e["id"], month=e["month"], day=e["day"], entry=e["entry"],
                deprecated=True, attested_in=e.get("attested_in"))
            if e.get("subject_la"):
                dep_subjects_la[e["id"]] = e["subject_la"]

        i18n: dict[str, dict[str, str]] = {}
        for f in sorted((crmedr_path / "i18n").glob("*.json")):
            i18n[f.stem] = json.loads(f.read_text())
        i18n.setdefault("la", {})
        for cid, subj in dep_subjects_la.items():
            i18n["la"].setdefault(cid, subj)

        ed_raw = json.loads((clbdr_path / "data/editions.json").read_text())
        editions: dict[str, EditionMeta] = {}
        for e in ed_raw["entries"]:
            if e.get("book") != MARTYROLOGY_BOOK:
                continue
            editions[e["id"]] = EditionMeta(
                id=e["id"], nature=e["nature"], language=e["language"],
                scope=e["scope"], promulgated=str(e["promulgated"]),
                promulgated_year=int(str(e["promulgated"])[:4]),
                decree=e.get("decree"), predecessor=e.get("predecessor"),
                successor=e.get("successor"), translation_of=e.get("translation_of"),
                note=e.get("note"))
        return cls(entries, editions, i18n)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/martyrology_api/registry.py tests/test_registry.py
git commit -m "feat: CRMEDR/CLBDR registry loader with deprecated-id merge and id helpers"
```

---

### Task 4: Store (both on-disk shapes, placement index)

**Files:**
- Create: `src/martyrology_api/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Registry`, `IdEntry`, `anchor_day`, `slug_of`, `is_canonical_id` from Task 3.
- Produces:
  - `Elogium` dataclass: `id: str, text: str | None, entry: int, asterisk: bool, unnumbered: bool, anchor_month: int, anchor_day: int`
  - `DayData` dataclass: `month: int, day: int, titulus: str | None, elogia: list[Elogium], conclusio: str | None`
  - `Placement` dataclass: `edition_id: str, day_printed: str` (format `"MM-DD"`)`, entry: int, asterisk: bool, unnumbered: bool, text: str | None`
  - Module function `parse_month_file(raw: dict, month: int, shape: str, registry: Registry) -> dict[int, DayData]` — `shape` ∈ `"day-structured" | "flat"`; also `detect_shape(raw: dict) -> str` (keys starting `mr:` → flat, else day-structured; empty dict → day-structured)
  - `class Store(data_paths: list[Path], registry: Registry)`: `available() -> set[str]` (edition dirs found on disk), `shape(edition_id) -> str`, `month(edition_id, month) -> dict[int, DayData]` (empty dict when file missing), `day(edition_id, month, day) -> DayData | None`, `find_by_slug(edition_id, month, day, slug) -> Elogium | None` (searches the *printed* day), `placements(canonical_id) -> list[Placement]` (all available editions containing the id), `dir_for(edition_id) -> Path | None`
  - Entry-number rule: day-structured shape → `entry` = 1-based printed position within the day, `asterisk`/`unnumbered` looked up from registry (defaults `False` when the id is deprecated-only); flat shape → day membership, order, `entry`, `asterisk`, `unnumbered` all from `registry.ids_for_day`, texts from the flat map (ids in registry but absent from the flat map are **omitted**).

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:

```python
from martyrology_api.registry import Registry
from martyrology_api.store import Store, detect_shape


def make_store(crmedr_path, clbdr_path, data_paths) -> Store:
    return Store(data_paths, Registry.load(crmedr_path, clbdr_path))


def test_available_and_shape(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.available() == {"martyrologium_romanum_1749",
                             "martyrologium_romanum_2004",
                             "martyrologium_romanum_2004_it_IT"}
    assert s.shape("martyrologium_romanum_1749") == "day-structured"
    assert s.shape("martyrologium_romanum_2004") == "flat"


def test_day_structured_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_1749", 1, 1)
    assert d.titulus.startswith("1 Januarii")
    ids = [e.id for e in d.elogia]
    assert ids == ["mr:0101-circumcisio-domini", "mr:0102-concordius"]
    conc = d.elogia[1]
    assert (conc.entry, conc.anchor_month, conc.anchor_day) == (2, 1, 2)  # printed position 2, anchored 01-02
    assert d.conclusio.startswith("Et alibi")


def test_flat_day_uses_registry_placement(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_2004", 1, 1)
    assert d.titulus is None and d.conclusio is None
    ids = [e.id for e in d.elogia]
    assert ids == ["mr:0101-maria-dei-genetrix", "mr:0101-basilius"]
    assert d.elogia[0].unnumbered is True
    assert d.elogia[1].asterisk is True


def test_flat_day_omits_ids_without_text(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    d = s.day("martyrologium_romanum_2004_it_IT", 1, 1)
    assert [e.id for e in d.elogia] == ["mr:0101-maria-dei-genetrix"]  # basilius has no it text


def test_leap_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert [e.id for e in s.day("martyrologium_romanum_2004", 2, 29).elogia] == ["mr:0229-oswaldus"]
    assert [e.id for e in s.day("martyrologium_romanum_1749", 2, 29).elogia] == ["mr:0229-oswaldus"]


def test_missing_day_and_edition(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    assert s.day("martyrologium_romanum_1749", 3, 1) is None
    assert s.day("martyrologium_romanum_1584", 1, 1) is None
    assert s.month("martyrologium_romanum_1584", 1) == {}


def test_find_by_slug_on_printed_day(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    hit = s.find_by_slug("martyrologium_romanum_1749", 1, 1, "concordius")
    assert hit is not None and hit.id == "mr:0102-concordius"
    assert s.find_by_slug("martyrologium_romanum_1749", 1, 2, "concordius") is None
    assert s.find_by_slug("martyrologium_romanum_2004", 1, 2, "concordius").id == "mr:0102-concordius"


def test_placements_cross_edition(crmedr_path, clbdr_path, data_paths):
    s = make_store(crmedr_path, clbdr_path, data_paths)
    p = {pl.edition_id: pl for pl in s.placements("mr:0102-concordius")}
    assert p["martyrologium_romanum_1749"].day_printed == "01-01"
    assert p["martyrologium_romanum_2004"].day_printed == "01-02"
    assert p["martyrologium_romanum_2004_it_IT"].day_printed == "01-02"
    assert p["martyrologium_romanum_2004"].text.startswith("Spoleti")


def test_detect_shape():
    assert detect_shape({"mr:0101-basilius": "t"}) == "flat"
    assert detect_shape({"1": {"elogia": {}}}) == "day-structured"
    assert detect_shape({}) == "day-structured"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `store.py`**

```python
import json
from dataclasses import dataclass
from pathlib import Path

from .registry import Registry, anchor_day, slug_of


@dataclass
class Elogium:
    id: str
    text: str | None
    entry: int
    asterisk: bool
    unnumbered: bool
    anchor_month: int
    anchor_day: int


@dataclass
class DayData:
    month: int
    day: int
    titulus: str | None
    elogia: list[Elogium]
    conclusio: str | None


@dataclass
class Placement:
    edition_id: str
    day_printed: str
    entry: int
    asterisk: bool
    unnumbered: bool
    text: str | None


def detect_shape(raw: dict) -> str:
    for k in raw:
        return "flat" if k.startswith("mr:") else "day-structured"
    return "day-structured"


def _elogium(cid: str, text: str | None, position: int, registry: Registry) -> Elogium:
    am, ad = anchor_day(cid)
    reg = registry.entries.get(cid)
    return Elogium(
        id=cid, text=text, entry=position,
        asterisk=reg.asterisk if reg else False,
        unnumbered=reg.unnumbered if reg else False,
        anchor_month=am, anchor_day=ad)


def parse_month_file(raw: dict, month: int, shape: str, registry: Registry) -> dict[int, DayData]:
    days: dict[int, DayData] = {}
    if shape == "day-structured":
        for day_key, obj in raw.items():
            day = int(day_key)
            elogia = [_elogium(cid, text, i + 1, registry)
                      for i, (cid, text) in enumerate(obj.get("elogia", {}).items())]
            days[day] = DayData(month=month, day=day, titulus=obj.get("titulus"),
                                elogia=elogia, conclusio=obj.get("conclusio"))
    else:  # flat: membership/order/metadata from the registry, texts from the map
        by_day: dict[int, list] = {}
        for e in registry.entries.values():
            if e.deprecated or e.month != month or e.id not in raw:
                continue
            by_day.setdefault(e.day, []).append(e)
        for day, entries in by_day.items():
            entries.sort(key=lambda e: (not e.unnumbered, e.entry))
            elogia = [Elogium(id=e.id, text=raw[e.id], entry=e.entry,
                              asterisk=e.asterisk, unnumbered=e.unnumbered,
                              anchor_month=e.month, anchor_day=e.day)
                      for e in entries]
            days[day] = DayData(month=month, day=day, titulus=None,
                                elogia=elogia, conclusio=None)
    return days


class Store:
    def __init__(self, data_paths: list[Path], registry: Registry):
        self.registry = registry
        self._dirs: dict[str, Path] = {}
        for base in data_paths:
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if d.is_dir() and any(d.glob("[0-1][0-9].json")):
                    self._dirs.setdefault(d.name, d)
        self._months: dict[tuple[str, int], dict[int, DayData]] = {}
        self._shapes: dict[str, str] = {}

    def available(self) -> set[str]:
        return set(self._dirs)

    def dir_for(self, edition_id: str) -> Path | None:
        return self._dirs.get(edition_id)

    def _load_month(self, edition_id: str, month: int) -> dict[int, DayData]:
        key = (edition_id, month)
        if key in self._months:
            return self._months[key]
        d = self._dirs.get(edition_id)
        result: dict[int, DayData] = {}
        if d is not None:
            f = d / f"{month:02d}.json"
            if f.exists():
                raw = json.loads(f.read_text())
                self._shapes.setdefault(edition_id, detect_shape(raw))
                result = parse_month_file(raw, month, self._shapes[edition_id], self.registry)
        self._months[key] = result
        return result

    def shape(self, edition_id: str) -> str:
        if edition_id not in self._shapes:
            for m in range(1, 13):
                if self._load_month(edition_id, m):
                    break
        return self._shapes.get(edition_id, "day-structured")

    def month(self, edition_id: str, month: int) -> dict[int, DayData]:
        return self._load_month(edition_id, month)

    def day(self, edition_id: str, month: int, day: int) -> DayData | None:
        return self._load_month(edition_id, month).get(day)

    def find_by_slug(self, edition_id: str, month: int, day: int, slug: str):
        d = self.day(edition_id, month, day)
        if d is None:
            return None
        for e in d.elogia:
            if slug_of(e.id) == slug:
                return e
        return None

    def placements(self, canonical_id: str) -> list[Placement]:
        am, _ = anchor_day(canonical_id)
        out: list[Placement] = []
        for edition_id in sorted(self._dirs):
            months = [am] + [m for m in range(1, 13) if m != am]
            for m in months:
                found = next((("%02d-%02d" % (m, dd.day), e)
                              for dd in self._load_month(edition_id, m).values()
                              for e in dd.elogia if e.id == canonical_id), None)
                if found:
                    printed, e = found
                    out.append(Placement(edition_id=edition_id, day_printed=printed,
                                         entry=e.entry, asterisk=e.asterisk,
                                         unnumbered=e.unnumbered, text=e.text))
                    break
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/martyrology_api/store.py tests/test_store.py
git commit -m "feat: edition store parsing both on-disk shapes with cross-edition placement index"
```

---

### Task 5: Resolver

**Files:**
- Create: `src/martyrology_api/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Registry`, `EditionMeta` from Task 3.
- Produces:
  - `Resolution` dataclass: `edition_id: str, resolved_from: dict | None` (dict holds only the non-`None` of `nation`/`year`/`locale`; `None` when nothing was resolved, i.e. explicit edition)
  - `ResolutionError(ApiProblem)` subclasses raised directly as problems: `PreFirstEditionError` (404, type_slug `pre-first-edition`, extension `editions_url="/api/v1/editions"`), `EditionUnavailableError` (404, type_slug `edition-unavailable`, extension `edition=<id>`)
  - `resolve(registry: Registry, available: set[str], nation: str | None = None, year: int | None = None, locale: str | None = None) -> Resolution`
- Resolution algorithm (spec §1): candidates = `registry.editions`; if `nation` given, restrict to `scope == nation` — if that yields nothing, fall back to `scope == "universal"`; if `locale` given, restrict to language primary subtag == locale primary subtag — if that yields nothing, fall back to `language` primary subtag `la`; if `year` given, keep `promulgated_year <= year`, raising `PreFirstEditionError` when empty; pick max by `promulgated_year` (tie-break: non-`translatio` nature preferred, then edition id for determinism); if the winner is not in `available`, raise `EditionUnavailableError` — do **not** silently fall back to an older edition.

- [ ] **Step 1: Write the failing tests**

`tests/test_resolver.py`:

```python
import pytest

from martyrology_api.registry import Registry
from martyrology_api.resolver import (EditionUnavailableError,
                                      PreFirstEditionError, resolve)


@pytest.fixture
def reg(crmedr_path, clbdr_path):
    return Registry.load(crmedr_path, clbdr_path)


AVAILABLE = {"martyrologium_romanum_1749", "martyrologium_romanum_2004",
             "martyrologium_romanum_2004_it_IT"}


def test_universal_default_is_2004(reg):
    r = resolve(reg, AVAILABLE)
    assert r.edition_id == "martyrologium_romanum_2004"
    assert r.resolved_from == {}


def test_nation_resolves_vernacular(reg):
    r = resolve(reg, AVAILABLE, nation="IT")
    assert r.edition_id == "martyrologium_romanum_2004_it_IT"
    assert r.resolved_from == {"nation": "IT"}


def test_nation_with_latin_locale_overrides(reg):
    r = resolve(reg, AVAILABLE, nation="IT", locale="la")
    assert r.edition_id == "martyrologium_romanum_2004"


def test_year_resolver(reg):
    assert resolve(reg, AVAILABLE, year=1970).edition_id == "martyrologium_romanum_1749"
    assert resolve(reg, AVAILABLE, year=2004).edition_id == "martyrologium_romanum_2004"


def test_year_resolving_to_textless_edition_is_404(reg):
    with pytest.raises(EditionUnavailableError) as ei:
        resolve(reg, AVAILABLE, year=1600)  # -> 1584, registered but no texts
    assert ei.value.extensions["edition"] == "martyrologium_romanum_1584"


def test_pre_first_edition(reg):
    with pytest.raises(PreFirstEditionError):
        resolve(reg, AVAILABLE, year=1500)


def test_unknown_nation_falls_back_universal(reg):
    r = resolve(reg, AVAILABLE, nation="FR")
    assert r.edition_id == "martyrologium_romanum_2004"
    assert r.resolved_from == {"nation": "FR"}


def test_locale_en_prefers_translation_when_available(reg):
    avail = AVAILABLE | {"martyrologium_romanum_2004_en_unofficial"}
    r = resolve(reg, avail, locale="en")
    assert r.edition_id == "martyrologium_romanum_2004_en_unofficial"


def test_tie_break_prefers_non_translation(reg):
    # locale la, year 2004: 2004 (la) wins over en translation (filtered by locale anyway)
    r = resolve(reg, AVAILABLE, year=2004, locale="la")
    assert r.edition_id == "martyrologium_romanum_2004"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `resolver.py`**

```python
from dataclasses import dataclass

from .problems import ApiProblem
from .registry import EditionMeta, Registry


class PreFirstEditionError(ApiProblem):
    def __init__(self, year: int):
        super().__init__(
            404, "No edition in force",
            detail=f"No Roman Martyrology edition was promulgated on or before {year}; "
                   "the first typical edition is 1584.",
            type_slug="pre-first-edition", editions_url="/api/v1/editions")


class EditionUnavailableError(ApiProblem):
    def __init__(self, edition_id: str):
        super().__init__(
            404, "Edition texts unavailable",
            detail=f"Edition '{edition_id}' is registered but its texts are not "
                   "attached in this deployment.",
            type_slug="edition-unavailable", edition=edition_id,
            editions_url="/api/v1/editions")


@dataclass
class Resolution:
    edition_id: str
    resolved_from: dict | None


def _primary(lang: str) -> str:
    return lang.split("-")[0].lower()


def resolve(registry: Registry, available: set[str],
            nation: str | None = None, year: int | None = None,
            locale: str | None = None) -> Resolution:
    candidates = list(registry.editions.values())

    if nation:
        national = [e for e in candidates if e.scope == nation]
        candidates = national or [e for e in candidates if e.scope == "universal"]
    else:
        candidates = [e for e in candidates if e.scope == "universal"]

    if locale:
        wanted = [e for e in candidates if _primary(e.language) == _primary(locale)]
        candidates = wanted or [e for e in candidates if _primary(e.language) == "la"]

    if year is not None:
        candidates = [e for e in candidates if e.promulgated_year <= year]
        if not candidates:
            raise PreFirstEditionError(year)

    def rank(e: EditionMeta):
        return (e.promulgated_year, e.nature != "translatio", e.id)

    winner = max(candidates, key=rank)
    if winner.id not in available:
        raise EditionUnavailableError(winner.id)

    resolved_from = {k: v for k, v in
                     (("nation", nation), ("year", year), ("locale", locale))
                     if v is not None}
    return Resolution(edition_id=winner.id, resolved_from=resolved_from)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_resolver.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/martyrology_api/resolver.py tests/test_resolver.py
git commit -m "feat: edition resolver (nation/year/locale) with honest edition-unavailable errors"
```

---

### Task 6: Path grammar parser

**Files:**
- Create: `src/martyrology_api/grammar.py`
- Test: `tests/test_grammar.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `ElogiaRequest` dataclass: `nation: str | None = None, edition: str | None = None, year: int | None = None, month: int | None = None, day: int | None = None, slug: str | None = None`
  - `parse_elogia_path(path: str) -> ElogiaRequest` — `path` is everything after `/api/v1/elogia/` (no leading slash). Raises `ApiProblem(400, …, type_slug="malformed-path")` on bad grammar. Grammar: `[nation/{A-Z}{2} | edition/{id}] [YYYY] MM [DD [slug]]`; year forbidden after `edition/`; month 01–12 (2 digits required); day valid for month (Feb allows 29); slug matches `[a-z0-9-]+`.

- [ ] **Step 1: Write the failing tests**

`tests/test_grammar.py`:

```python
import pytest

from martyrology_api.grammar import ElogiaRequest, parse_elogia_path
from martyrology_api.problems import ApiProblem


def test_month_only():
    assert parse_elogia_path("01") == ElogiaRequest(month=1)


def test_month_day_slug():
    r = parse_elogia_path("01/02/argeus-et-socii")
    assert (r.month, r.day, r.slug) == (1, 2, "argeus-et-socii")


def test_universal_year():
    r = parse_elogia_path("1970/01/02")
    assert (r.year, r.month, r.day) == (1970, 1, 2)


def test_nation_forms():
    assert parse_elogia_path("nation/IT/01") == ElogiaRequest(nation="IT", month=1)
    r = parse_elogia_path("nation/IT/1970/01/02")
    assert (r.nation, r.year, r.month, r.day) == ("IT", 1970, 1, 2)


def test_edition_form():
    r = parse_elogia_path("edition/martyrologium_romanum_1749/01/01/concordius")
    assert (r.edition, r.month, r.day, r.slug) == ("martyrologium_romanum_1749", 1, 1, "concordius")


def test_leap_day_ok():
    assert parse_elogia_path("02/29").day == 29


@pytest.mark.parametrize("bad", [
    "", "13", "1", "001", "01/32", "02/30", "04/31", "01/02/UPPER",
    "nation/it/01", "nation/ITA/01", "edition/x/1970/01",  # year after edition forbidden
    "1970", "nation/IT", "edition/martyrologium_romanum_1749",  # month required
    "01/02/argeus-et-socii/extra", "0170/01",
])
def test_bad_paths(bad):
    with pytest.raises(ApiProblem) as ei:
        parse_elogia_path(bad)
    assert ei.value.status == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_grammar.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `grammar.py`**

```python
import re
from dataclasses import dataclass

from .problems import ApiProblem

DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
                 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
NATION_RE = re.compile(r"^[A-Z]{2}$")


@dataclass
class ElogiaRequest:
    nation: str | None = None
    edition: str | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    slug: str | None = None


def _bad(detail: str) -> ApiProblem:
    return ApiProblem(400, "Malformed elogia path", detail=detail,
                      type_slug="malformed-path")


def parse_elogia_path(path: str) -> ElogiaRequest:
    segs = [s for s in path.split("/") if s]
    req = ElogiaRequest()

    if segs and segs[0] == "nation":
        if len(segs) < 2 or not NATION_RE.match(segs[1]):
            raise _bad("nation/ must be followed by an ISO 3166-1 alpha-2 code")
        req.nation, segs = segs[1], segs[2:]
    elif segs and segs[0] == "edition":
        if len(segs) < 2:
            raise _bad("edition/ must be followed by a CLBDR edition id")
        req.edition, segs = segs[1], segs[2:]

    if segs and re.fullmatch(r"\d{4}", segs[0]):
        if req.edition:
            raise _bad("a year segment cannot follow an explicit edition")
        req.year, segs = int(segs[0]), segs[1:]

    if not segs:
        raise _bad("a two-digit month segment is required")
    if not re.fullmatch(r"\d{2}", segs[0]) or not 1 <= int(segs[0]) <= 12:
        raise _bad(f"invalid month segment '{segs[0]}' (expected 01-12)")
    req.month, segs = int(segs[0]), segs[1:]

    if segs:
        if not re.fullmatch(r"\d{2}", segs[0]) or \
                not 1 <= int(segs[0]) <= DAYS_IN_MONTH[req.month]:
            raise _bad(f"invalid day segment '{segs[0]}' for month {req.month:02d}")
        req.day, segs = int(segs[0]), segs[1:]

    if segs:
        if req.day is None or not SLUG_RE.match(segs[0]):
            raise _bad(f"invalid slug segment '{segs[0]}'")
        req.slug, segs = segs[0], segs[1:]

    if segs:
        raise _bad(f"unexpected trailing segments: {'/'.join(segs)}")
    return req
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_grammar.py -v`
Expected: all passed (6 named + 15 parametrized).

- [ ] **Step 5: Commit**

```bash
git add src/martyrology_api/grammar.py tests/test_grammar.py
git commit -m "feat: elogia path grammar parser with 4-vs-2-digit disambiguation"
```

---

### Task 7: Response models, read router, app assembly

**Files:**
- Create: `src/martyrology_api/models.py`, `src/martyrology_api/routers/__init__.py`, `src/martyrology_api/routers/read.py`, `src/martyrology_api/app.py`
- Test: `tests/test_read_api.py`

**Interfaces:**
- Consumes: `parse_elogia_path`/`ElogiaRequest` (Task 6), `resolve`/`Resolution` (Task 5), `Store`/`DayData`/`Elogium`/`Placement` (Task 4), `Registry` (Task 3), `ApiProblem` (Task 1).
- Produces:
  - pydantic models in `models.py`: `EditionMetadataOut` (`nature, language, scope, promulgated, decree: str | None, predecessor: str | None, successor: str | None, translation_of: str | None`), `MetadataOut` (`edition: str, edition_metadata: EditionMetadataOut, resolved_from: dict | None, month: int, day: int | None, access: str = "public", access_info: str | None = None`), `ElogiumOut` (`id, entry: int, asterisk: bool, unnumbered: bool, anchor_day: str, text: str | None`), `DayContentOut` (`titulus: str | None, elogia: list[ElogiumOut], conclusio: str | None`), `DayOut` (`metadata: MetadataOut` + the three `DayContentOut` fields), `MonthOut` (`metadata: MetadataOut, days: dict[str, DayContentOut]` — keys zero-padded `"01"`…), `EditionPlacementOut` (`day_printed, entry, asterisk, unnumbered, text`), `EulogyOut` (`id, subject: dict[str, str], anchor_day: str, deprecated: bool, editions: dict[str, EditionPlacementOut]`)
  - `read.py`: `router` (APIRouter) with `GET /elogia/{rest:path}` and `GET /elogium/{canonical_id}`; helper `resolve_request(request, req: ElogiaRequest, locale_q, edition_q) -> Resolution` (edition path/query → validate registered + available, `resolved_from=None`; else `resolve()` with locale defaulted from the first `Accept-Language` tag); helper `elogium_out(e: Elogium) -> ElogiumOut`
  - `app.py`: `create_app(settings: Settings | None = None) -> FastAPI` — loads `Registry` and `Store` eagerly, stores `settings/registry/store` on `app.state`, installs problem handlers, mounts routers under `/api/v1`
  - 404 problem slugs produced here: `unknown-edition`, `unknown-day`, `unknown-eulogy`, `unknown-id`
  - `metadata.access` is always `"public"` in this task; licensing lands in Task 10.

- [ ] **Step 1: Write the failing tests**

`tests/test_read_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.config import Settings


@pytest.fixture
def client(crmedr_path, clbdr_path, data_paths):
    import os
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path)
    return TestClient(create_app(settings))


def test_day_universal_default(client):
    r = client.get("/api/v1/elogia/01/01")
    assert r.status_code == 200
    b = r.json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004"
    assert b["metadata"]["resolved_from"] == {}
    assert [e["id"] for e in b["elogia"]] == ["mr:0101-maria-dei-genetrix", "mr:0101-basilius"]
    assert b["elogia"][0]["text"].startswith("In octava")
    assert b["titulus"] is None


def test_month_universal(client):
    r = client.get("/api/v1/elogia/01")
    assert r.status_code == 200
    b = r.json()
    assert set(b["days"]) == {"01", "02"}
    assert b["metadata"]["day"] is None


def test_nation_resolution(client):
    b = client.get("/api/v1/elogia/nation/IT/01/01").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004_it_IT"
    assert b["metadata"]["resolved_from"] == {"nation": "IT"}


def test_year_resolution(client):
    b = client.get("/api/v1/elogia/nation/IT/1970/01/01").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_1749"
    assert b["metadata"]["resolved_from"] == {"nation": "IT", "year": 1970}
    assert b["titulus"].startswith("1 Januarii")


def test_explicit_edition_path(client):
    b = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_1749"
    assert b["metadata"]["resolved_from"] is None


def test_edition_query_overrides(client):
    b = client.get("/api/v1/elogia/01/01?edition=martyrologium_romanum_1749").json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_1749"


def test_cross_day_slug_on_printed_day(client):
    r = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01/concordius")
    assert r.status_code == 200
    b = r.json()
    assert b["elogia"][0]["id"] == "mr:0102-concordius"
    assert b["elogia"][0]["anchor_day"] == "01-02"
    r2 = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/02/concordius")
    assert r2.status_code == 404
    assert r2.json()["type"].endswith("unknown-eulogy")


def test_accept_language_influences_resolution(client):
    b = client.get("/api/v1/elogia/nation/IT/01/01",
                   headers={"Accept-Language": "la"}).json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004"


def test_elogium_cross_edition(client):
    r = client.get("/api/v1/elogium/mr:0102-concordius")
    assert r.status_code == 200
    b = r.json()
    assert b["subject"]["la"] == "Sanctus Concordius"
    assert b["editions"]["martyrologium_romanum_1749"]["day_printed"] == "01-01"
    assert b["editions"]["martyrologium_romanum_2004"]["day_printed"] == "01-02"


def test_elogium_editions_filter(client):
    b = client.get("/api/v1/elogium/mr:0102-concordius"
                   "?editions=martyrologium_romanum_1749").json()
    assert list(b["editions"]) == ["martyrologium_romanum_1749"]


def test_errors(client):
    assert client.get("/api/v1/elogia/13/01").status_code == 400
    assert client.get("/api/v1/elogia/edition/nope_1000/01/01").status_code == 404
    assert client.get("/api/v1/elogia/03/05").status_code == 404          # no data
    assert client.get("/api/v1/elogia/1500/01/01").status_code == 404     # pre-1584
    assert client.get("/api/v1/elogium/mr:9999-nobody").status_code == 404
    r = client.get("/api/v1/elogia/nation/IT/1600/01/01")                 # -> 1584, textless
    assert r.status_code == 404 and r.json()["edition"] == "martyrologium_romanum_1584"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_read_api.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `models.py`**

```python
from pydantic import BaseModel


class EditionMetadataOut(BaseModel):
    nature: str
    language: str
    scope: str
    promulgated: str
    decree: str | None = None
    predecessor: str | None = None
    successor: str | None = None
    translation_of: str | None = None


class MetadataOut(BaseModel):
    edition: str
    edition_metadata: EditionMetadataOut
    resolved_from: dict | None = None
    month: int
    day: int | None = None
    access: str = "public"
    access_info: str | None = None


class ElogiumOut(BaseModel):
    id: str
    entry: int
    asterisk: bool
    unnumbered: bool
    anchor_day: str
    text: str | None


class DayContentOut(BaseModel):
    titulus: str | None
    elogia: list[ElogiumOut]
    conclusio: str | None


class DayOut(DayContentOut):
    metadata: MetadataOut


class MonthOut(BaseModel):
    metadata: MetadataOut
    days: dict[str, DayContentOut]


class EditionPlacementOut(BaseModel):
    day_printed: str
    entry: int
    asterisk: bool
    unnumbered: bool
    text: str | None


class EulogyOut(BaseModel):
    id: str
    subject: dict[str, str]
    anchor_day: str
    deprecated: bool
    editions: dict[str, EditionPlacementOut]
```

- [ ] **Step 4: Implement `routers/read.py`** (and empty `routers/__init__.py`)

```python
from fastapi import APIRouter, Request

from ..grammar import ElogiaRequest, parse_elogia_path
from ..models import (DayContentOut, DayOut, EditionMetadataOut,
                      EditionPlacementOut, ElogiumOut, EulogyOut, MetadataOut,
                      MonthOut)
from ..problems import ApiProblem
from ..registry import is_canonical_id
from ..resolver import EditionUnavailableError, Resolution, resolve
from ..store import DayData, Elogium

router = APIRouter()


def _edition_meta_out(request: Request, edition_id: str) -> EditionMetadataOut:
    e = request.app.state.registry.editions[edition_id]
    return EditionMetadataOut(nature=e.nature, language=e.language, scope=e.scope,
                              promulgated=e.promulgated, decree=e.decree,
                              predecessor=e.predecessor, successor=e.successor,
                              translation_of=e.translation_of)


def elogium_out(e: Elogium) -> ElogiumOut:
    return ElogiumOut(id=e.id, entry=e.entry, asterisk=e.asterisk,
                      unnumbered=e.unnumbered,
                      anchor_day=f"{e.anchor_month:02d}-{e.anchor_day:02d}",
                      text=e.text)


def _day_content(d: DayData) -> DayContentOut:
    return DayContentOut(titulus=d.titulus,
                         elogia=[elogium_out(e) for e in d.elogia],
                         conclusio=d.conclusio)


def _explicit_edition(request: Request, edition_id: str) -> Resolution:
    registry = request.app.state.registry
    store = request.app.state.store
    if edition_id not in registry.editions:
        raise ApiProblem(404, "Unknown edition",
                         detail=f"'{edition_id}' is not a registered martyrology edition.",
                         type_slug="unknown-edition")
    if edition_id not in store.available():
        raise EditionUnavailableError(edition_id)
    return Resolution(edition_id=edition_id, resolved_from=None)


def resolve_request(request: Request, req: ElogiaRequest,
                    locale_q: str | None, edition_q: str | None) -> Resolution:
    edition = req.edition or edition_q
    if edition:
        return _explicit_edition(request, edition)
    locale = locale_q
    if locale is None:
        al = request.headers.get("accept-language")
        if al:
            locale = al.split(",")[0].split(";")[0].strip() or None
    return resolve(request.app.state.registry, request.app.state.store.available(),
                   nation=req.nation, year=req.year, locale=locale)


@router.get("/elogia/{rest:path}")
def get_elogia(rest: str, request: Request,
               locale: str | None = None, edition: str | None = None):
    req = parse_elogia_path(rest)
    resolution = resolve_request(request, req, locale, edition)
    store = request.app.state.store
    metadata = MetadataOut(edition=resolution.edition_id,
                           edition_metadata=_edition_meta_out(request, resolution.edition_id),
                           resolved_from=resolution.resolved_from,
                           month=req.month, day=req.day)

    if req.day is None:
        days = store.month(resolution.edition_id, req.month)
        return MonthOut(metadata=metadata,
                        days={f"{d:02d}": _day_content(v)
                              for d, v in sorted(days.items())})

    day_data = store.day(resolution.edition_id, req.month, req.day)
    if day_data is None:
        raise ApiProblem(404, "No entries for this day",
                         detail=f"Edition '{resolution.edition_id}' has no entries "
                                f"for {req.month:02d}-{req.day:02d}.",
                         type_slug="unknown-day")

    if req.slug is None:
        c = _day_content(day_data)
        return DayOut(metadata=metadata, titulus=c.titulus,
                      elogia=c.elogia, conclusio=c.conclusio)

    hit = store.find_by_slug(resolution.edition_id, req.month, req.day, req.slug)
    if hit is None:
        raise ApiProblem(404, "Eulogy not on this day",
                         detail=f"No eulogy '{req.slug}' printed under "
                                f"{req.month:02d}-{req.day:02d} in "
                                f"'{resolution.edition_id}'.",
                         type_slug="unknown-eulogy")
    return DayOut(metadata=metadata, titulus=day_data.titulus,
                  elogia=[elogium_out(hit)], conclusio=day_data.conclusio)


@router.get("/elogium/{canonical_id}")
def get_elogium(canonical_id: str, request: Request, editions: str | None = None):
    registry = request.app.state.registry
    store = request.app.state.store
    if not is_canonical_id(canonical_id) or canonical_id not in registry.entries:
        raise ApiProblem(404, "Unknown canonical id",
                         detail=f"'{canonical_id}' is not in the CRMEDR registry.",
                         type_slug="unknown-id")
    entry = registry.entries[canonical_id]
    wanted = set(editions.split(",")) if editions else None
    placements = {p.edition_id: EditionPlacementOut(day_printed=p.day_printed,
                                                    entry=p.entry, asterisk=p.asterisk,
                                                    unnumbered=p.unnumbered, text=p.text)
                  for p in store.placements(canonical_id)
                  if wanted is None or p.edition_id in wanted}
    subject = {loc: registry.subjects(loc)[canonical_id]
               for loc in ("la", "en", "it")
               if canonical_id in registry.subjects(loc)}
    return EulogyOut(id=canonical_id, subject=subject,
                     anchor_day=f"{entry.month:02d}-{entry.day:02d}",
                     deprecated=entry.deprecated, editions=placements)
```

- [ ] **Step 5: Implement `app.py`**

```python
from fastapi import FastAPI

from . import __version__
from .config import Settings
from .problems import install_problem_handlers
from .registry import Registry
from .routers import read
from .store import Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Roman Martyrology API", version=__version__)
    install_problem_handlers(app)
    registry = Registry.load(settings.crmedr_path, settings.clbdr_path)
    app.state.settings = settings
    app.state.registry = registry
    app.state.store = Store(settings.data_path_list, registry)
    app.include_router(read.router, prefix="/api/v1")
    return app
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_read_api.py -v`
Expected: 12 passed. Also run the full suite: `pytest -q` — all green.

- [ ] **Step 7: Commit**

```bash
git add src/martyrology_api/models.py src/martyrology_api/routers src/martyrology_api/app.py tests/test_read_api.py
git commit -m "feat: read endpoints — day/month/slug paths, cross-edition elogium, app assembly"
```

---

### Task 8: Discovery (`/editions`) and catalog (`/elogia`)

**Files:**
- Create: `src/martyrology_api/routers/discovery.py`
- Modify: `src/martyrology_api/models.py` (append models), `src/martyrology_api/app.py` (mount router)
- Test: `tests/test_discovery_api.py`

**Interfaces:**
- Consumes: `Registry`/`EditionMeta` (Task 3), `Store` (Task 4), `Settings.restricted_set`/`access_info_url` (Task 1), models (Task 7).
- Produces:
  - Models appended to `models.py`: `GovernanceOut` (`governing_body: str, type: str, nation: str | None = None`), `AvailabilityOut` (`status: str, note: str | None = None`), `EditionOut` (`edition_id: str, book: str = "martyrologium_romanum", year: int, nature: str, scope: dict, locale: str, promulgation: dict, predecessor: str | None, successor: str | None, governance: GovernanceOut, availability: AvailabilityOut`), `EditionsOut` (`editions: list[EditionOut]`), `CatalogEntryOut` (`id: str, subject: str | None, anchor_day: str, deprecated: bool, present: bool | None = None, day_printed: str | None = None, entry: int | None = None`), `CatalogOut` (`elogia: list[CatalogEntryOut]`)
  - `discovery.py`: `router` with `GET /editions` and `GET /elogia` (exact — **must be mounted before** the read router so it wins over `/elogia/{rest:path}`); `availability_status(edition_id, available, settings) -> str` (`"restricted-texts"` if in `restricted_set`, else `"public"` if in `available`, else `"unavailable"`); `GOVERNANCE` map: scope `"universal"` → `GovernanceOut(governing_body="Dicastery for Divine Worship and the Discipline of the Sacraments", type="dicastery")`, scope `"IT"` → `GovernanceOut(governing_body="Conferenza Episcopale Italiana", type="bishops_conference", nation="IT")`, any other nation scope → `GovernanceOut(governing_body=f"Bishops' Conference ({scope})", type="bishops_conference", nation=scope)`
  - Catalog params: `locale` (subject language, default `la`), `edition` (adds `present`/`day_printed`/`entry` against that edition; unknown edition → 404 `unknown-edition`)

- [ ] **Step 1: Write the failing tests**

`tests/test_discovery_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.config import Settings


@pytest.fixture
def client(crmedr_path, clbdr_path, data_paths):
    import os
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path)
    return TestClient(create_app(settings))


def test_editions_discovery(client):
    r = client.get("/api/v1/editions")
    assert r.status_code == 200
    eds = {e["edition_id"]: e for e in r.json()["editions"]}
    assert "missale_romanum_1570" not in eds
    e2004 = eds["martyrologium_romanum_2004"]
    assert e2004["availability"]["status"] == "restricted-texts"
    assert e2004["governance"]["type"] == "dicastery"
    assert e2004["promulgation"]["decree"].startswith("Congregatio")
    it = eds["martyrologium_romanum_2004_it_IT"]
    assert it["governance"] == {"governing_body": "Conferenza Episcopale Italiana",
                                "type": "bishops_conference", "nation": "IT"}
    assert it["scope"] == {"type": "nation", "nation": "IT"}
    assert eds["martyrologium_romanum_1584"]["availability"]["status"] == "unavailable"
    assert eds["martyrologium_romanum_1749"]["availability"]["status"] == "public"


def test_catalog_default(client):
    r = client.get("/api/v1/elogia")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["elogia"]}
    assert items["mr:0102-concordius"]["subject"] == "Sanctus Concordius"
    assert items["mr:0102-concordius"]["anchor_day"] == "01-02"
    assert items["mr:0101-circumcisio-domini"]["deprecated"] is True
    assert items["mr:0101-basilius"]["present"] is None


def test_catalog_locale(client):
    items = {i["id"]: i for i in client.get("/api/v1/elogia?locale=en").json()["elogia"]}
    assert items["mr:0102-concordius"]["subject"] == "Saint Concordius"
    assert items["mr:0101-basilius"]["subject"] is None  # no en subject in fixture


def test_catalog_with_edition(client):
    r = client.get("/api/v1/elogia?edition=martyrologium_romanum_1749")
    items = {i["id"]: i for i in r.json()["elogia"]}
    assert items["mr:0102-concordius"]["present"] is True
    assert items["mr:0102-concordius"]["day_printed"] == "01-01"
    assert items["mr:0101-basilius"]["present"] is False
    assert client.get("/api/v1/elogia?edition=nope").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discovery_api.py -v`
Expected: FAIL (`/editions` 404, `/elogia` parsed as malformed path → 400).

- [ ] **Step 3: Append the models to `models.py`**

```python
class GovernanceOut(BaseModel):
    governing_body: str
    type: str
    nation: str | None = None


class AvailabilityOut(BaseModel):
    status: str
    note: str | None = None


class EditionOut(BaseModel):
    edition_id: str
    book: str = "martyrologium_romanum"
    year: int
    nature: str
    scope: dict
    locale: str
    promulgation: dict
    predecessor: str | None = None
    successor: str | None = None
    governance: GovernanceOut
    availability: AvailabilityOut


class EditionsOut(BaseModel):
    editions: list[EditionOut]


class CatalogEntryOut(BaseModel):
    id: str
    subject: str | None
    anchor_day: str
    deprecated: bool
    present: bool | None = None
    day_printed: str | None = None
    entry: int | None = None


class CatalogOut(BaseModel):
    elogia: list[CatalogEntryOut]
```

- [ ] **Step 4: Implement `routers/discovery.py`**

```python
from fastapi import APIRouter, Request

from ..config import Settings
from ..models import (AvailabilityOut, CatalogEntryOut, CatalogOut, EditionOut,
                      EditionsOut, GovernanceOut)
from ..problems import ApiProblem
from ..registry import EditionMeta

router = APIRouter()


def governance_for(scope: str) -> GovernanceOut:
    if scope == "universal":
        return GovernanceOut(
            governing_body="Dicastery for Divine Worship and the Discipline of the Sacraments",
            type="dicastery")
    if scope == "IT":
        return GovernanceOut(governing_body="Conferenza Episcopale Italiana",
                             type="bishops_conference", nation="IT")
    return GovernanceOut(governing_body=f"Bishops' Conference ({scope})",
                         type="bishops_conference", nation=scope)


def availability_status(edition_id: str, available: set[str], settings: Settings) -> str:
    if edition_id in settings.restricted_set:
        return "restricted-texts"
    return "public" if edition_id in available else "unavailable"


def _edition_out(e: EditionMeta, available: set[str], settings: Settings) -> EditionOut:
    status = availability_status(e.id, available, settings)
    note = None
    if status == "restricted-texts":
        note = f"Copyrighted texts; an approved API key is required. See {settings.access_info_url}"
    elif status == "unavailable":
        note = "Registered in the CLBDR but no texts are attached in this deployment."
    scope = ({"type": "universal"} if e.scope == "universal"
             else {"type": "nation", "nation": e.scope})
    return EditionOut(
        edition_id=e.id, year=e.promulgated_year, nature=e.nature, scope=scope,
        locale=e.language,
        promulgation={"decree": e.decree, "date": e.promulgated},
        predecessor=e.predecessor, successor=e.successor,
        governance=governance_for(e.scope),
        availability=AvailabilityOut(status=status, note=note))


@router.get("/editions")
def get_editions(request: Request) -> EditionsOut:
    registry = request.app.state.registry
    available = request.app.state.store.available()
    settings = request.app.state.settings
    eds = sorted(registry.editions.values(), key=lambda e: (e.promulgated_year, e.id))
    return EditionsOut(editions=[_edition_out(e, available, settings) for e in eds])


@router.get("/elogia")
def get_catalog(request: Request, locale: str = "la",
                edition: str | None = None) -> CatalogOut:
    registry = request.app.state.registry
    store = request.app.state.store
    if edition is not None and edition not in registry.editions:
        raise ApiProblem(404, "Unknown edition",
                         detail=f"'{edition}' is not a registered martyrology edition.",
                         type_slug="unknown-edition")
    subjects = registry.subjects(locale)
    items = []
    for e in sorted(registry.entries.values(), key=lambda x: (x.month, x.day, x.entry)):
        item = CatalogEntryOut(id=e.id, subject=subjects.get(e.id),
                               anchor_day=f"{e.month:02d}-{e.day:02d}",
                               deprecated=e.deprecated)
        if edition is not None:
            hit = next((p for p in store.placements(e.id)
                        if p.edition_id == edition), None)
            item.present = hit is not None
            if hit:
                item.day_printed = hit.day_printed
                item.entry = hit.entry
        items.append(item)
    return CatalogOut(elogia=items)
```

- [ ] **Step 5: Mount the router in `app.py`** — discovery **before** read so exact `/elogia` wins:

```python
from .routers import discovery, read
# in create_app, replace the single include with:
    app.include_router(discovery.router, prefix="/api/v1")
    app.include_router(read.router, prefix="/api/v1")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_discovery_api.py tests/test_read_api.py -v`
Expected: all passed (read tests still green — route ordering did not break `/elogia/{rest}`).

- [ ] **Step 7: Commit**

```bash
git add src/martyrology_api/routers/discovery.py src/martyrology_api/models.py src/martyrology_api/app.py tests/test_discovery_api.py
git commit -m "feat: /editions discovery with governance+availability, /elogia catalog"
```

---

### Task 9: Zitadel authentication

**Files:**
- Create: `src/martyrology_api/auth.py`
- Modify: `src/martyrology_api/app.py` (attach `app.state.authenticator`)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `ApiProblem` (Task 1).
- Produces:
  - `Identity` dataclass: `subject: str, username: str, email: str | None = None, name: str | None = None`
  - `class Authenticator(issuer: str, client_id: str, client_secret: str, cache_ttl: int = 300, transport: httpx.AsyncBaseTransport | None = None)` with `async identity(token: str) -> Identity | None` — POSTs `{issuer}/oauth/v2/introspect` (form field `token`, HTTP basic auth `client_id:client_secret`); `active: false` or empty issuer → `None`; results (including `None`) cached per token for `cache_ttl` seconds (`time.monotonic`)
  - `async get_identity(request: Request) -> Identity | None` FastAPI dependency: no `Authorization` header → `None`; malformed header or invalid token → raises `ApiProblem(401, …, type_slug="invalid-token")`; uses `request.app.state.authenticator`
- Later tasks stub auth by assigning `request.app.state.authenticator` a test double exposing the same `async identity(token)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_auth.py`:

```python
import json

import httpx
import pytest

from martyrology_api.auth import Authenticator, Identity

CALLS = {"n": 0}


def mock_transport(active: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        assert request.url.path.endswith("/oauth/v2/introspect")
        body = {"active": active, "sub": "u123", "username": "jdoe",
                "email": "j@example.org", "name": "J. Doe"}
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_active_token_yields_identity():
    a = Authenticator("https://zitadel.example", "cid", "sec",
                      transport=mock_transport(True))
    ident = await a.identity("tok1")
    assert ident == Identity(subject="u123", username="jdoe",
                             email="j@example.org", name="J. Doe")


@pytest.mark.asyncio
async def test_inactive_token_is_none():
    a = Authenticator("https://zitadel.example", "cid", "sec",
                      transport=mock_transport(False))
    assert await a.identity("tok2") is None


@pytest.mark.asyncio
async def test_cache_avoids_second_call():
    CALLS["n"] = 0
    a = Authenticator("https://zitadel.example", "cid", "sec",
                      transport=mock_transport(True))
    await a.identity("tok3")
    await a.identity("tok3")
    assert CALLS["n"] == 1


@pytest.mark.asyncio
async def test_disabled_when_no_issuer():
    a = Authenticator("", "", "")
    assert await a.identity("anything") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `auth.py`**

```python
import time
from dataclasses import dataclass

import httpx
from fastapi import Request

from .problems import ApiProblem


@dataclass(frozen=True)
class Identity:
    subject: str
    username: str
    email: str | None = None
    name: str | None = None


class Authenticator:
    def __init__(self, issuer: str, client_id: str, client_secret: str,
                 cache_ttl: int = 300,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.cache_ttl = cache_ttl
        self._transport = transport
        self._cache: dict[str, tuple[Identity | None, float]] = {}

    async def identity(self, token: str) -> Identity | None:
        if not self.issuer:
            return None
        hit = self._cache.get(token)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                f"{self.issuer}/oauth/v2/introspect",
                data={"token": token},
                auth=(self.client_id, self.client_secret))
        ident: Identity | None = None
        if resp.status_code == 200:
            body = resp.json()
            if body.get("active"):
                ident = Identity(
                    subject=body["sub"],
                    username=body.get("username") or body.get("preferred_username")
                             or body["sub"],
                    email=body.get("email"), name=body.get("name"))
        self._cache[token] = (ident, time.monotonic() + self.cache_ttl)
        return ident


async def get_identity(request: Request) -> Identity | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiProblem(401, "Invalid Authorization header",
                         detail="Expected 'Authorization: Bearer <token>'.",
                         type_slug="invalid-token")
    ident = await request.app.state.authenticator.identity(token.strip())
    if ident is None:
        raise ApiProblem(401, "Invalid or expired token",
                         type_slug="invalid-token")
    return ident
```

- [ ] **Step 4: Attach the authenticator in `app.py`** (inside `create_app`, after `app.state.store = …`):

```python
from .auth import Authenticator
# ...
    app.state.authenticator = Authenticator(
        settings.zitadel_issuer, settings.zitadel_client_id,
        settings.zitadel_client_secret)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/auth.py src/martyrology_api/app.py tests/test_auth.py
git commit -m "feat: Zitadel token introspection with per-token cache and Bearer dependency"
```

---

### Task 10: OpenFGA authorization

**Files:**
- Create: `src/martyrology_api/authz.py`
- Modify: `src/martyrology_api/app.py` (attach `app.state.authz`)
- Test: `tests/test_authz.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `Identity` (Task 9).
- Produces:
  - `class Authz(api_url: str, store_id: str, model_id: str, transport: httpx.AsyncBaseTransport | None = None)` with `async check(user: str, relation: str, edition_id: str) -> bool` — POSTs `{api_url}/stores/{store_id}/check` with body `{"tuple_key": {"user": user, "relation": relation, "object": "edition:{edition_id}"}, "authorization_model_id": model_id}` (model id omitted when empty); returns `allowed`; **fails closed**: unconfigured (`api_url` empty), non-200, or transport error → `False`
  - `def user_ref(identity: Identity) -> str` → `f"user:{identity.subject}"`
  - Relations used across the codebase (exact strings): `"can_read_texts"`, `"can_edit"`, `"can_admin"`, `"can_review"`
- Test doubles used by later tasks: any object with `async check(user, relation, edition_id) -> bool` assigned to `app.state.authz`.

- [ ] **Step 1: Write the failing tests**

`tests/test_authz.py`:

```python
import httpx
import pytest

from martyrology_api.auth import Identity
from martyrology_api.authz import Authz, user_ref


def transport(allowed: bool, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stores/store1/check"
        import json
        body = json.loads(request.content)
        assert body["tuple_key"]["object"].startswith("edition:")
        return httpx.Response(status, json={"allowed": allowed})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_allowed():
    a = Authz("https://fga.example", "store1", "model1", transport=transport(True))
    assert await a.check("user:u123", "can_read_texts",
                         "martyrologium_romanum_2004") is True


@pytest.mark.asyncio
async def test_denied():
    a = Authz("https://fga.example", "store1", "model1", transport=transport(False))
    assert await a.check("user:u123", "can_edit", "martyrologium_romanum_2004") is False


@pytest.mark.asyncio
async def test_fails_closed_on_error_and_unconfigured():
    a = Authz("https://fga.example", "store1", "model1",
              transport=transport(True, status=500))
    assert await a.check("user:u", "can_edit", "x") is False
    assert await Authz("", "", "").check("user:u", "can_edit", "x") is False


def test_user_ref():
    assert user_ref(Identity(subject="u123", username="jdoe")) == "user:u123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_authz.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `authz.py`**

```python
import httpx

from .auth import Identity


def user_ref(identity: Identity) -> str:
    return f"user:{identity.subject}"


class Authz:
    def __init__(self, api_url: str, store_id: str, model_id: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_url = api_url.rstrip("/")
        self.store_id = store_id
        self.model_id = model_id
        self._transport = transport

    async def check(self, user: str, relation: str, edition_id: str) -> bool:
        if not self.api_url or not self.store_id:
            return False
        body = {"tuple_key": {"user": user, "relation": relation,
                              "object": f"edition:{edition_id}"}}
        if self.model_id:
            body["authorization_model_id"] = self.model_id
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(
                    f"{self.api_url}/stores/{self.store_id}/check", json=body)
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        return bool(resp.json().get("allowed"))
```

- [ ] **Step 4: Attach in `app.py`** (inside `create_app`, after the authenticator):

```python
from .authz import Authz
# ...
    app.state.authz = Authz(settings.openfga_api_url,
                            settings.openfga_store_id,
                            settings.openfga_model_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_authz.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/authz.py src/martyrology_api/app.py tests/test_authz.py
git commit -m "feat: OpenFGA check wrapper, fail-closed, with user_ref helper"
```

---

### Task 11: Licensing redaction wired into reads

**Files:**
- Create: `src/martyrology_api/licensing.py`
- Modify: `src/martyrology_api/routers/read.py` (handlers become `async`, redaction applied)
- Test: `tests/test_licensing_api.py`

**Interfaces:**
- Consumes: `Settings.restricted_set`/`access_info_url` (Task 1), `get_identity`/`Identity` (Task 9), `Authz.check`/`user_ref` (Task 10), models (Task 7).
- Produces (`licensing.py`):
  - `def is_restricted(edition_id: str, settings) -> bool`
  - `async def texts_allowed(request: Request, identity: Identity | None, edition_id: str) -> bool` — `True` for unrestricted editions; otherwise requires `identity` and `await request.app.state.authz.check(user_ref(identity), "can_read_texts", edition_id)`
  - `def redact(elogia: list[ElogiumOut]) -> None` — sets every `.text = None` in place
- Behavior wired into `read.py` (spec §3): restricted edition + not allowed → **200**, all texts `None`, `metadata.access = "restricted-texts"`, `metadata.access_info = settings.access_info_url`; restricted + allowed → full texts, `metadata.access = "public"`, and `request.state.cache_private = True` (consumed by Task 12); `/elogium/{id}` redacts per-edition (each restricted edition in the map individually). Handlers gain `identity: Identity | None = Depends(get_identity)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_licensing_api.py`:

```python
import os

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.auth import Identity
from martyrology_api.config import Settings


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe") if token == "good" else None


class GrantReaders:
    def __init__(self, allowed_editions):
        self.allowed = allowed_editions

    async def check(self, user, relation, edition_id):
        return relation == "can_read_texts" and edition_id in self.allowed


@pytest.fixture
def client(crmedr_path, clbdr_path, data_paths):
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path)
    app = create_app(settings)
    app.state.authenticator = StaticAuth()
    app.state.authz = GrantReaders({"martyrologium_romanum_2004"})
    return TestClient(app)


def test_anonymous_restricted_is_redacted_200(client):
    r = client.get("/api/v1/elogia/01/01")
    assert r.status_code == 200
    b = r.json()
    assert b["metadata"]["access"] == "restricted-texts"
    assert b["metadata"]["access_info"]
    assert all(e["text"] is None for e in b["elogia"])
    assert [e["id"] for e in b["elogia"]] == ["mr:0101-maria-dei-genetrix",
                                              "mr:0101-basilius"]  # skeleton stays public


def test_public_edition_untouched(client):
    b = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    assert b["metadata"]["access"] == "public"
    assert b["elogia"][0]["text"] is not None


def test_authorized_gets_texts(client):
    r = client.get("/api/v1/elogia/01/01", headers={"Authorization": "Bearer good"})
    b = r.json()
    assert b["metadata"]["access"] == "public"
    assert b["elogia"][0]["text"].startswith("In octava")


def test_authorized_but_ungranted_edition_still_redacted(client):
    b = client.get("/api/v1/elogia/nation/IT/01/01",
                   headers={"Authorization": "Bearer good"}).json()
    assert b["metadata"]["edition"] == "martyrologium_romanum_2004_it_IT"
    assert b["metadata"]["access"] == "restricted-texts"


def test_bad_token_is_401(client):
    r = client.get("/api/v1/elogia/01/01", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_elogium_redacts_per_edition(client):
    b = client.get("/api/v1/elogium/mr:0102-concordius").json()
    assert b["editions"]["martyrologium_romanum_1749"]["text"] is not None
    assert b["editions"]["martyrologium_romanum_2004"]["text"] is None
    assert b["editions"]["martyrologium_romanum_2004_it_IT"]["text"] is None


def test_month_redaction(client):
    b = client.get("/api/v1/elogia/01").json()
    assert b["metadata"]["access"] == "restricted-texts"
    assert all(e["text"] is None
               for day in b["days"].values() for e in day["elogia"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_licensing_api.py -v`
Expected: FAIL — texts currently served in full, `access` always `"public"`.

- [ ] **Step 3: Implement `licensing.py`**

```python
from fastapi import Request

from .auth import Identity
from .authz import user_ref
from .models import ElogiumOut


def is_restricted(edition_id: str, settings) -> bool:
    return edition_id in settings.restricted_set


async def texts_allowed(request: Request, identity: Identity | None,
                        edition_id: str) -> bool:
    if not is_restricted(edition_id, request.app.state.settings):
        return True
    if identity is None:
        return False
    return await request.app.state.authz.check(
        user_ref(identity), "can_read_texts", edition_id)


def redact(elogia: list[ElogiumOut]) -> None:
    for e in elogia:
        e.text = None
```

- [ ] **Step 4: Wire into `routers/read.py`**

Make both handlers `async`, add the identity dependency, and apply licensing. The changed parts of `read.py`:

```python
from fastapi import APIRouter, Depends, Request

from ..auth import Identity, get_identity
from ..licensing import is_restricted, redact, texts_allowed
```

In `get_elogia`, change the signature and add the licensing block right after `metadata` is built:

```python
@router.get("/elogia/{rest:path}")
async def get_elogia(rest: str, request: Request,
                     locale: str | None = None, edition: str | None = None,
                     identity: Identity | None = Depends(get_identity)):
    ...
    allowed = await texts_allowed(request, identity, resolution.edition_id)
    settings = request.app.state.settings
    if is_restricted(resolution.edition_id, settings):
        if allowed:
            request.state.cache_private = True
        else:
            metadata.access = "restricted-texts"
            metadata.access_info = settings.access_info_url
```

Then, at each of the three return points, redact when not allowed — e.g. for the day case:

```python
    c = _day_content(day_data)
    if not allowed:
        redact(c.elogia)
    return DayOut(metadata=metadata, titulus=c.titulus,
                  elogia=c.elogia, conclusio=c.conclusio)
```

(same pattern for the month return — redact every `DayContentOut` in the dict — and the slug return, redacting the single-element list).

In `get_elogium`, change the signature the same way and redact per edition while building the placements map:

```python
@router.get("/elogium/{canonical_id}")
async def get_elogium(canonical_id: str, request: Request,
                      editions: str | None = None,
                      identity: Identity | None = Depends(get_identity)):
    ...
    placements = {}
    for p in store.placements(canonical_id):
        if wanted is not None and p.edition_id not in wanted:
            continue
        text = p.text
        if not await texts_allowed(request, identity, p.edition_id):
            text = None
        placements[p.edition_id] = EditionPlacementOut(
            day_printed=p.day_printed, entry=p.entry, asterisk=p.asterisk,
            unnumbered=p.unnumbered, text=text)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_licensing_api.py tests/test_read_api.py -v`
Expected: licensing tests pass; **`test_read_api.py` day/month tests for the 2004 edition now fail** because fixtures make 2004 restricted — update `tests/test_read_api.py`: in `test_day_universal_default` replace the text assertion with `assert b["elogia"][0]["text"] is None` and add `assert b["metadata"]["access"] == "restricted-texts"`; `test_cross_day_slug_on_printed_day` and `test_explicit_edition_path` target 1749 (public) and stay unchanged. Re-run until all green.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/licensing.py src/martyrology_api/routers/read.py tests/test_licensing_api.py tests/test_read_api.py
git commit -m "feat: licensed-text redaction (200 + text:null) with OpenFGA can_read_texts gate"
```

---

### Task 12: Cache-Control + ETag middleware

**Files:**
- Create: `src/martyrology_api/caching.py`
- Modify: `src/martyrology_api/app.py` (add middleware)
- Test: `tests/test_caching.py`

**Interfaces:**
- Consumes: `request.state.cache_private` flag set by Task 11.
- Produces: `CacheHeadersMiddleware` (Starlette `BaseHTTPMiddleware`) applying to `GET /api/v1/*` with status 200: ETag = quoted md5 of body; `If-None-Match` match → `304` with same `ETag`/`Cache-Control`; `Cache-Control` = `private, max-age=0` when `request.state.cache_private`, else `public, max-age=31536000, immutable` when the path contains `/edition/` or the query has `edition`, else `public, max-age=86400`; adds `Vary: Authorization, Accept-Language`.

- [ ] **Step 1: Write the failing tests**

`tests/test_caching.py`:

```python
import os

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.auth import Identity
from martyrology_api.config import Settings


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe") if token == "good" else None


class GrantAll:
    async def check(self, user, relation, edition_id):
        return True


@pytest.fixture
def client(crmedr_path, clbdr_path, data_paths):
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path)
    app = create_app(settings)
    app.state.authenticator = StaticAuth()
    app.state.authz = GrantAll()
    return TestClient(app)


def test_edition_path_is_immutable(client):
    r = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "etag" in r.headers
    assert "Authorization" in r.headers["vary"]


def test_resolver_path_is_daily(client):
    r = client.get("/api/v1/elogia/nation/IT/1970/01/01")
    assert r.headers["cache-control"] == "public, max-age=86400"


def test_authorized_restricted_is_private(client):
    r = client.get("/api/v1/elogia/01/01", headers={"Authorization": "Bearer good"})
    assert r.headers["cache-control"] == "private, max-age=0"


def test_304_on_if_none_match(client):
    r1 = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01")
    etag = r1.headers["etag"]
    r2 = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01",
                    headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers["etag"] == etag


def test_errors_not_cached(client):
    r = client.get("/api/v1/elogia/03/05")
    assert r.status_code == 404
    assert "cache-control" not in r.headers or "max-age=8" not in r.headers.get("cache-control", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caching.py -v`
Expected: FAIL — no `etag`/`cache-control` headers present.

- [ ] **Step 3: Implement `caching.py`**

```python
import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if (request.method != "GET"
                or not request.url.path.startswith("/api/v1")
                or response.status_code != 200):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        etag = '"' + hashlib.md5(body).hexdigest() + '"'

        if getattr(request.state, "cache_private", False):
            cc = "private, max-age=0"
        elif "/edition/" in request.url.path or "edition" in request.query_params:
            cc = "public, max-age=31536000, immutable"
        else:
            cc = "public, max-age=86400"

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.update({"etag": etag, "cache-control": cc,
                        "vary": "Authorization, Accept-Language"})
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304,
                            headers={"etag": etag, "cache-control": cc,
                                     "vary": "Authorization, Accept-Language"})
        return Response(content=body, status_code=200, headers=headers)
```

- [ ] **Step 4: Add to `app.py`** (inside `create_app`, right after `FastAPI(...)`):

```python
from .caching import CacheHeadersMiddleware
# ...
    app.add_middleware(CacheHeadersMiddleware)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_caching.py -v && pytest -q`
Expected: 5 passed; full suite green.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/caching.py src/martyrology_api/app.py tests/test_caching.py
git commit -m "feat: cache-control policy (immutable/daily/private) with ETag and 304"
```

---

### Task 13: VcsBackend protocol + LocalGitBackend

**Files:**
- Create: `src/martyrology_api/writer/__init__.py` (empty), `src/martyrology_api/writer/base.py`, `src/martyrology_api/writer/local.py`
- Test: `tests/test_writer_local.py`

**Interfaces:**
- Consumes: nothing internal (subprocess `git`).
- Produces (`base.py`):
  - `class ConflictError(Exception)`
  - `class VcsBackend(Protocol)`:
    - `ensure_branch(repo: str, branch: str) -> None` — create from the default branch if missing
    - `read_file(repo: str, branch: str, path: str) -> tuple[bytes, str] | None` — `(content, blob_sha)`, `None` if absent
    - `write_file(repo: str, branch: str, path: str, content: bytes, message: str, author_name: str, author_email: str, expected_sha: str | None = None) -> str` — returns commit sha; raises `ConflictError` when `expected_sha` doesn't match the current blob sha
    - `open_pr(repo: str, branch: str, title: str) -> str` — idempotent, returns PR URL
- Produces (`local.py`): `class LocalGitBackend(root: Path)` — repos are bare repos at `{root}/{repo}.git` (e.g. `{root}/CatholicOS/martyrology-api.git`); writes clone to a temp dir, commit, push; `open_pr` returns `f"local://{repo}/{branch}"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_writer_local.py`:

```python
import subprocess
from pathlib import Path

import pytest

from martyrology_api.writer.base import ConflictError
from martyrology_api.writer.local import LocalGitBackend

REPO = "CatholicOS/martyrology-api"


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_root(tmp_path) -> Path:
    root = tmp_path / "gitroot"
    bare = root / f"{REPO}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", "."], bare)
    seed = tmp_path / "seed"
    run(["git", "clone", str(bare), str(seed)], tmp_path)
    f = seed / "data/editions/martyrologium_romanum_1749/01.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"1": {"titulus": "t", "elogia": {}, "conclusio": "c"}}')
    run(["git", "add", "-A"], seed)
    run(["git", "-c", "user.name=seed", "-c", "user.email=s@x", "commit", "-m", "seed"], seed)
    run(["git", "push", "origin", "main"], seed)
    return root


def test_read_file(git_root):
    b = LocalGitBackend(git_root)
    got = b.read_file(REPO, "main", "data/editions/martyrologium_romanum_1749/01.json")
    assert got is not None
    content, sha = got
    assert b'"titulus": "t"' in content and len(sha) == 40
    assert b.read_file(REPO, "main", "nope.json") is None


def test_ensure_branch_and_write(git_root):
    b = LocalGitBackend(git_root)
    b.ensure_branch(REPO, "curation/jdoe/edits")
    commit = b.write_file(REPO, "curation/jdoe/edits",
                          "data/editions/martyrologium_romanum_1749/01.json",
                          b'{"1": {"titulus": "T2", "elogia": {}, "conclusio": "c"}}',
                          "curation: fix titulus", "J. Doe", "j@example.org")
    assert len(commit) == 40
    content, _ = b.read_file(REPO, "curation/jdoe/edits",
                             "data/editions/martyrologium_romanum_1749/01.json")
    assert b'"titulus": "T2"' in content
    # main untouched
    content_main, _ = b.read_file(REPO, "main",
                                  "data/editions/martyrologium_romanum_1749/01.json")
    assert b'"titulus": "t"' in content_main
    # author recorded
    log = subprocess.run(
        ["git", "-C", str(git_root / f"{REPO}.git"), "log", "-1",
         "--format=%an <%ae>", "curation/jdoe/edits"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert log == "J. Doe <j@example.org>"


def test_write_new_file_and_conflict(git_root):
    b = LocalGitBackend(git_root)
    b.write_file(REPO, "curation/jdoe/edits", "data/new.json", b"{}",
                 "curation: new file", "J", "j@x")
    _, sha = b.read_file(REPO, "curation/jdoe/edits", "data/new.json")
    b.write_file(REPO, "curation/jdoe/edits", "data/new.json", b'{"a": 1}',
                 "ok", "J", "j@x", expected_sha=sha)
    with pytest.raises(ConflictError):
        b.write_file(REPO, "curation/jdoe/edits", "data/new.json", b'{"b": 2}',
                     "stale", "J", "j@x", expected_sha=sha)  # sha now stale


def test_open_pr_local(git_root):
    b = LocalGitBackend(git_root)
    assert b.open_pr(REPO, "curation/jdoe/edits", "title") == \
        f"local://{REPO}/curation/jdoe/edits"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_writer_local.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `base.py`**

```python
from typing import Protocol


class ConflictError(Exception):
    pass


class VcsBackend(Protocol):
    def ensure_branch(self, repo: str, branch: str) -> None: ...

    def read_file(self, repo: str, branch: str, path: str) -> tuple[bytes, str] | None: ...

    def write_file(self, repo: str, branch: str, path: str, content: bytes,
                   message: str, author_name: str, author_email: str,
                   expected_sha: str | None = None) -> str: ...

    def open_pr(self, repo: str, branch: str, title: str) -> str: ...
```

- [ ] **Step 4: Implement `local.py`**

```python
import subprocess
import tempfile
from pathlib import Path

from .base import ConflictError


class LocalGitBackend:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _bare(self, repo: str) -> Path:
        p = self.root / f"{repo}.git"
        if not p.is_dir():
            raise FileNotFoundError(f"no local repo at {p}")
        return p

    def _git(self, cwd: Path, *args: str, check: bool = True):
        return subprocess.run(["git", *args], cwd=cwd, check=check,
                              capture_output=True, text=False)

    def _default_branch(self, repo: str) -> str:
        out = self._git(self._bare(repo), "symbolic-ref", "HEAD").stdout
        return out.decode().strip().removeprefix("refs/heads/")

    def ensure_branch(self, repo: str, branch: str) -> None:
        bare = self._bare(repo)
        exists = self._git(bare, "show-ref", "--verify", "--quiet",
                           f"refs/heads/{branch}", check=False)
        if exists.returncode != 0:
            self._git(bare, "branch", branch, self._default_branch(repo))

    def read_file(self, repo: str, branch: str, path: str) -> tuple[bytes, str] | None:
        bare = self._bare(repo)
        show = self._git(bare, "show", f"{branch}:{path}", check=False)
        if show.returncode != 0:
            return None
        sha = self._git(bare, "rev-parse", f"{branch}:{path}").stdout.decode().strip()
        return show.stdout, sha

    def write_file(self, repo: str, branch: str, path: str, content: bytes,
                   message: str, author_name: str, author_email: str,
                   expected_sha: str | None = None) -> str:
        self.ensure_branch(repo, branch)
        if expected_sha is not None:
            current = self.read_file(repo, branch, path)
            if current is None or current[1] != expected_sha:
                raise ConflictError(f"{path} on {branch} has changed")
        bare = self._bare(repo)
        with tempfile.TemporaryDirectory() as tmp:
            wc = Path(tmp) / "wc"
            self._git(Path(tmp), "clone", "--branch", branch, "--single-branch",
                      str(bare), str(wc))
            target = wc / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            self._git(wc, "add", path)
            self._git(wc, "-c", f"user.name={author_name}",
                      "-c", f"user.email={author_email}",
                      "commit", "-m", message,
                      "--author", f"{author_name} <{author_email}>")
            self._git(wc, "push", "origin", branch)
            return self._git(wc, "rev-parse", "HEAD").stdout.decode().strip()

    def open_pr(self, repo: str, branch: str, title: str) -> str:
        return f"local://{repo}/{branch}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_writer_local.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/writer tests/test_writer_local.py
git commit -m "feat: VcsBackend protocol and LocalGitBackend over bare repos"
```

---

### Task 14: Month-payload validation

**Files:**
- Create: `src/martyrology_api/writer/validation.py`
- Test: `tests/test_validation.py`

**Interfaces:**
- Consumes: `Registry`, `is_canonical_id`, `anchor_day` (Task 3), `ApiProblem` (Task 1), `DAYS_IN_MONTH` (Task 6).
- Produces:
  - `validate_month_payload(raw: dict, month: int, shape: str, registry: Registry) -> list[str]` — returns human-readable error strings (empty = valid)
  - `validate_or_raise(raw, month, shape, registry) -> None` — raises `ApiProblem(422, "Invalid month payload", type_slug="invalid-payload", errors=[…])`
- Rules — **day-structured**: top-level keys must be unpadded int strings valid for the month (Feb allows 29); each day value a dict with keys ⊆ `{titulus, elogia, conclusio}`; `titulus`/`conclusio` str or `None`; `elogia` a dict of canonical-id → non-empty str; every id must be canonical **and** known to the registry (current or deprecated); no id may appear under two days of the same file. **flat**: every key canonical + known; anchor month of each id must equal the file's month; every value a non-empty str.

- [ ] **Step 1: Write the failing tests**

`tests/test_validation.py`:

```python
import pytest

from martyrology_api.problems import ApiProblem
from martyrology_api.registry import Registry
from martyrology_api.writer.validation import (validate_month_payload,
                                               validate_or_raise)


@pytest.fixture
def reg(crmedr_path, clbdr_path):
    return Registry.load(crmedr_path, clbdr_path)


GOOD_DAY = {"1": {"titulus": "t", "elogia": {"mr:0101-basilius": "x"}, "conclusio": None}}


def test_day_structured_valid(reg):
    assert validate_month_payload(GOOD_DAY, 1, "day-structured", reg) == []


def test_day_structured_errors(reg):
    bad = {
        "0": {"elogia": {}},                                   # invalid day
        "32": {"elogia": {}},                                  # invalid day
        "1": {"elogia": {"mr:0101-basilius": "x"}, "extra": 1},  # unknown key
        "2": {"elogia": {"mr:9999-nobody": "x"}},              # unknown id
        "3": {"elogia": {"mr:0101-basilius": ""}},             # empty text + dup with day 1
    }
    errs = validate_month_payload(bad, 1, "day-structured", reg)
    assert len(errs) >= 5
    assert any("appears more than once" in e for e in errs)


def test_deprecated_ids_are_accepted(reg):
    raw = {"1": {"elogia": {"mr:0101-circumcisio-domini": "x"}}}
    assert validate_month_payload(raw, 1, "day-structured", reg) == []


def test_flat_valid_and_errors(reg):
    assert validate_month_payload({"mr:0101-basilius": "x"}, 1, "flat", reg) == []
    errs = validate_month_payload(
        {"mr:0228-romanus": "x",      # anchor month 2 != file month 1
         "mr:9999-nobody": "x",      # unknown
         "mr:0101-basilius": ""},    # empty text
        1, "flat", reg)
    assert len(errs) == 3


def test_validate_or_raise(reg):
    with pytest.raises(ApiProblem) as ei:
        validate_or_raise({"mr:9999-nobody": "x"}, 1, "flat", reg)
    assert ei.value.status == 422
    assert ei.value.extensions["errors"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validation.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `validation.py`**

```python
from ..grammar import DAYS_IN_MONTH
from ..problems import ApiProblem
from ..registry import Registry, anchor_day, is_canonical_id

ALLOWED_DAY_KEYS = {"titulus", "elogia", "conclusio"}


def _check_id(cid: str, registry: Registry, errors: list[str]) -> bool:
    if not is_canonical_id(cid):
        errors.append(f"'{cid}' is not a canonical id (mr:MMDD-slug)")
        return False
    if cid not in registry.entries:
        errors.append(f"'{cid}' is not in the CRMEDR registry; coin ids registry-side first")
        return False
    return True


def validate_month_payload(raw: dict, month: int, shape: str,
                           registry: Registry) -> list[str]:
    errors: list[str] = []
    if shape == "flat":
        for cid, text in raw.items():
            if _check_id(cid, registry, errors) and anchor_day(cid)[0] != month:
                errors.append(f"'{cid}' is anchored to month {anchor_day(cid)[0]:02d}, "
                              f"not {month:02d} (flat editions follow registry placement)")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"text for '{cid}' must be a non-empty string")
        return errors

    seen: dict[str, str] = {}
    for day_key, obj in raw.items():
        if not day_key.isdigit() or not 1 <= int(day_key) <= DAYS_IN_MONTH.get(month, 0):
            errors.append(f"invalid day key '{day_key}' for month {month:02d}")
            continue
        if not isinstance(obj, dict):
            errors.append(f"day '{day_key}' must be an object")
            continue
        unknown = set(obj) - ALLOWED_DAY_KEYS
        if unknown:
            errors.append(f"day '{day_key}' has unknown keys: {sorted(unknown)}")
        for field in ("titulus", "conclusio"):
            if field in obj and obj[field] is not None and not isinstance(obj[field], str):
                errors.append(f"day '{day_key}' {field} must be a string or null")
        elogia = obj.get("elogia", {})
        if not isinstance(elogia, dict):
            errors.append(f"day '{day_key}' elogia must be an object")
            continue
        for cid, text in elogia.items():
            _check_id(cid, registry, errors)
            if not isinstance(text, str) or not text.strip():
                errors.append(f"text for '{cid}' (day {day_key}) must be a non-empty string")
            if cid in seen:
                errors.append(f"'{cid}' appears more than once (days {seen[cid]} and {day_key})")
            seen[cid] = day_key
    return errors


def validate_or_raise(raw: dict, month: int, shape: str, registry: Registry) -> None:
    errors = validate_month_payload(raw, month, shape, registry)
    if errors:
        raise ApiProblem(422, "Invalid month payload",
                         detail=f"{len(errors)} validation error(s)",
                         type_slug="invalid-payload", errors=errors)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_validation.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/martyrology_api/writer/validation.py tests/test_validation.py
git commit -m "feat: month-payload validation for both shapes with registry id checks"
```

---

### Task 15: CurationService

**Files:**
- Create: `src/martyrology_api/writer/service.py`
- Test: `tests/test_curation_service.py`

**Interfaces:**
- Consumes: `VcsBackend`/`ConflictError` (Task 13), `validate_or_raise` (Task 14), `Registry`/`anchor_day` (Task 3), `parse_month_file`/`detect_shape`/`DayData` (Task 4), `Identity` (Task 9), `Settings` (Task 1), `ApiProblem` (Task 1).
- Produces (`service.py`):
  - `WriteReceipt` dataclass: `branch: str, commit_sha: str, pr_url: str`
  - `class CurationService(backend: VcsBackend, registry: Registry, settings: Settings)`:
    - `repo_for(edition_id) -> str` — `settings.private_repo` if `edition_id in settings.restricted_set` else `settings.public_repo`
    - `month_path(edition_id, month) -> str` — `f"{settings.repo_data_prefix}/{edition_id}/{month:02d}.json"`; `edition_meta_path(edition_id) -> …/edition.json`
    - `branch_for(identity, topic) -> str` — `f"curation/{identity.username}/{topic or 'edits'}"`
    - `create_edition(identity, edition_id, shape, note, topic) -> WriteReceipt` — 422 `unknown-edition` if not in `registry.editions`; 409 `already-exists` if `edition.json` already on the default branch; writes `edition.json` (`{"shape": …, "note": …}`) and twelve `{MM}.json` files containing `{}` (one commit per file is fine; the receipt carries the last commit)
    - `patch_edition(identity, edition_id, fields, topic)` — merge into `edition.json` (404 `unknown-edition-data` if absent from the branch)
    - `put_month(identity, edition_id, month, raw, topic, if_match) -> WriteReceipt` — validate then write
    - `patch_day(identity, edition_id, month, day, payload, topic, if_match)` — day-structured only (422 `not-day-structured` for flat); 404 `unknown-day` if the day key is absent; `payload` keys: `titulus`/`conclusio` (set verbatim, `None` allowed), `order` (list that must be a permutation of the day's current ids → 422 `bad-order` otherwise)
    - `put_elogium(identity, edition_id, cid, text, day, position, topic, if_match)` — day-structured requires `day` (422 `day-required`); inserts at `position` (1-based, append when `None`); flat ignores `day`/`position`
    - `patch_elogium(identity, edition_id, cid, text, topic, if_match)` — 404 `unknown-eulogy` if the id is in no month file of the edition on that branch
    - `delete_elogium(identity, edition_id, cid, topic, if_match)` — removes the key wherever found (anchor month searched first, then all months); 404 `unknown-eulogy` when absent
    - `read_month_draft(edition_id, month, branch) -> dict[int, DayData]` — branch content parsed through `parse_month_file` (for Task 16's `X-Curation-Branch` reads)
    - shape derivation: read `edition.json` from the branch; when absent, `detect_shape` on the anchor-month file; empty edition defaults `day-structured`
    - every write: `ensure_branch`, mutate JSON (`json.dumps(..., ensure_ascii=False, indent=2)` + trailing newline), commit message `f"curation({edition_id}): {action}"`, author from identity (`email or f"{username}@users.noreply.local"`), then `open_pr(repo, branch, f"Curation: {edition_id}")` — receipt carries its URL
    - `ConflictError` from the backend is translated to `ApiProblem(409, …, type_slug="write-conflict")`; `if_match` (blob sha from a prior read) is passed as `expected_sha`

- [ ] **Step 1: Write the failing tests**

`tests/test_curation_service.py`:

```python
import json
import subprocess
from pathlib import Path

import pytest

from martyrology_api.auth import Identity
from martyrology_api.config import Settings
from martyrology_api.problems import ApiProblem
from martyrology_api.registry import Registry
from martyrology_api.writer.local import LocalGitBackend
from martyrology_api.writer.service import CurationService

PUB = "CatholicOS/martyrology-api"
PRIV = "CatholicOS/martyrology-texts"
IDENT = Identity(subject="u123", username="jdoe", email="j@example.org")


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def seed_repo(root: Path, repo: str, files: dict[str, str]):
    bare = root / f"{repo}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", "."], bare)
    seed = root / f"seed-{repo.replace('/', '-')}"
    run(["git", "clone", str(bare), str(seed)], root)
    for rel, content in files.items():
        f = seed / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    run(["git", "add", "-A"], seed)
    run(["git", "-c", "user.name=s", "-c", "user.email=s@x", "commit", "-m", "seed"], seed)
    run(["git", "push", "origin", "main"], seed)


@pytest.fixture
def service(tmp_path, crmedr_path, clbdr_path):
    root = tmp_path / "gitroot"
    seed_repo(root, PUB, {
        "data/editions/martyrologium_romanum_1749/edition.json":
            '{"shape": "day-structured"}',
        "data/editions/martyrologium_romanum_1749/01.json": json.dumps({
            "1": {"titulus": "t1", "elogia": {
                "mr:0101-circumcisio-domini": "Circumcisio.",
                "mr:0102-concordius": "Spoleti Concordii."}, "conclusio": "c"},
            "2": {"titulus": "t2", "elogia": {
                "mr:0102-argeus-et-socii": "Tomis Argei."}, "conclusio": "c"},
        })})
    seed_repo(root, PRIV, {
        "data/editions/martyrologium_romanum_2004/edition.json": '{"shape": "flat"}',
        "data/editions/martyrologium_romanum_2004/01.json":
            '{"mr:0101-basilius": "Basilii."}'})
    settings = Settings(_env_file=None, crmedr_path=crmedr_path,
                        clbdr_path=clbdr_path, local_git_root=str(root))
    registry = Registry.load(crmedr_path, clbdr_path)
    return CurationService(LocalGitBackend(root), registry, settings)


def read_branch_json(service, repo, branch, path):
    content, _ = service.backend.read_file(repo, branch, path)
    return json.loads(content)


def test_routing_and_branch_naming(service):
    assert service.repo_for("martyrologium_romanum_1749") == PUB
    assert service.repo_for("martyrologium_romanum_2004") == PRIV
    assert service.branch_for(IDENT, None) == "curation/jdoe/edits"
    assert service.branch_for(IDENT, "ocr-fixes") == "curation/jdoe/ocr-fixes"


def test_patch_elogium_day_structured(service):
    r = service.patch_elogium(IDENT, "martyrologium_romanum_1749",
                              "mr:0102-concordius", "Spoleti sancti Concordii, emend.",
                              topic=None, if_match=None)
    assert r.branch == "curation/jdoe/edits" and r.pr_url.startswith("local://")
    raw = read_branch_json(service, PUB, r.branch,
                           "data/editions/martyrologium_romanum_1749/01.json")
    assert raw["1"]["elogia"]["mr:0102-concordius"].endswith("emend.")


def test_put_elogium_with_position(service):
    service.put_elogium(IDENT, "martyrologium_romanum_1749", "mr:0101-basilius",
                        "Basilii text.", day=1, position=1, topic=None, if_match=None)
    raw = read_branch_json(service, PUB, "curation/jdoe/edits",
                           "data/editions/martyrologium_romanum_1749/01.json")
    assert list(raw["1"]["elogia"])[0] == "mr:0101-basilius"


def test_put_elogium_day_required(service):
    with pytest.raises(ApiProblem) as ei:
        service.put_elogium(IDENT, "martyrologium_romanum_1749", "mr:0101-basilius",
                            "x", day=None, position=None, topic=None, if_match=None)
    assert ei.value.status == 422


def test_delete_elogium_cross_day(service):
    service.delete_elogium(IDENT, "martyrologium_romanum_1749",
                           "mr:0102-concordius", topic=None, if_match=None)
    raw = read_branch_json(service, PUB, "curation/jdoe/edits",
                           "data/editions/martyrologium_romanum_1749/01.json")
    assert "mr:0102-concordius" not in raw["1"]["elogia"]  # was printed under day 1
    with pytest.raises(ApiProblem) as ei:
        service.delete_elogium(IDENT, "martyrologium_romanum_1749",
                               "mr:0102-concordius", topic=None, if_match=None)
    assert ei.value.status == 404


def test_patch_day_order_and_fields(service):
    service.patch_day(IDENT, "martyrologium_romanum_1749", 1, 1,
                      {"titulus": "Nova", "order": [
                          "mr:0102-concordius", "mr:0101-circumcisio-domini"]},
                      topic=None, if_match=None)
    raw = read_branch_json(service, PUB, "curation/jdoe/edits",
                           "data/editions/martyrologium_romanum_1749/01.json")
    assert raw["1"]["titulus"] == "Nova"
    assert list(raw["1"]["elogia"])[0] == "mr:0102-concordius"
    with pytest.raises(ApiProblem) as ei:
        service.patch_day(IDENT, "martyrologium_romanum_1749", 1, 1,
                          {"order": ["mr:0101-basilius"]}, topic=None, if_match=None)
    assert ei.value.status == 422  # not a permutation


def test_flat_edition_write(service):
    service.patch_elogium(IDENT, "martyrologium_romanum_2004",
                          "mr:0101-basilius", "Basilii Magni, emend.",
                          topic=None, if_match=None)
    raw = read_branch_json(service, PRIV, "curation/jdoe/edits",
                           "data/editions/martyrologium_romanum_2004/01.json")
    assert raw["mr:0101-basilius"].endswith("emend.")


def test_put_month_validates(service):
    with pytest.raises(ApiProblem) as ei:
        service.put_month(IDENT, "martyrologium_romanum_1749", 1,
                          {"1": {"elogia": {"mr:9999-nobody": "x"}}},
                          topic=None, if_match=None)
    assert ei.value.status == 422


def test_create_edition(service):
    r = service.create_edition(IDENT, "martyrologium_romanum_1914_en_unofficial",
                               shape="day-structured", note=None, topic="digitize")
    raw = read_branch_json(
        service, PUB, "curation/jdoe/digitize",
        "data/editions/martyrologium_romanum_1914_en_unofficial/edition.json")
    assert raw["shape"] == "day-structured"
    with pytest.raises(ApiProblem) as ei:
        service.create_edition(IDENT, "not_registered_2050", "flat", None, None)
    assert ei.value.status == 422


def test_read_month_draft(service):
    service.patch_elogium(IDENT, "martyrologium_romanum_1749",
                          "mr:0102-argeus-et-socii", "Draft text.",
                          topic=None, if_match=None)
    days = service.read_month_draft("martyrologium_romanum_1749", 1,
                                    "curation/jdoe/edits")
    assert days[2].elogia[0].text == "Draft text."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_curation_service.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `service.py`**

```python
import json
from dataclasses import dataclass

from ..auth import Identity
from ..config import Settings
from ..problems import ApiProblem
from ..registry import Registry, anchor_day
from ..store import DayData, detect_shape, parse_month_file
from .base import ConflictError, VcsBackend
from .validation import validate_or_raise


@dataclass
class WriteReceipt:
    branch: str
    commit_sha: str
    pr_url: str


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


class CurationService:
    def __init__(self, backend: VcsBackend, registry: Registry, settings: Settings):
        self.backend = backend
        self.registry = registry
        self.settings = settings

    def repo_for(self, edition_id: str) -> str:
        return (self.settings.private_repo
                if edition_id in self.settings.restricted_set
                else self.settings.public_repo)

    def month_path(self, edition_id: str, month: int) -> str:
        return f"{self.settings.repo_data_prefix}/{edition_id}/{month:02d}.json"

    def edition_meta_path(self, edition_id: str) -> str:
        return f"{self.settings.repo_data_prefix}/{edition_id}/edition.json"

    def branch_for(self, identity: Identity, topic: str | None) -> str:
        return f"curation/{identity.username}/{topic or 'edits'}"

    def _require_registered(self, edition_id: str) -> None:
        if edition_id not in self.registry.editions:
            raise ApiProblem(422, "Unknown edition",
                             detail=f"'{edition_id}' must be registered in the CLBDR "
                                    "before texts can be curated.",
                             type_slug="unknown-edition")

    def _shape(self, edition_id: str, branch: str) -> str:
        meta = self.backend.read_file(self.repo_for(edition_id), branch,
                                      self.edition_meta_path(edition_id))
        if meta is not None:
            shape = json.loads(meta[0]).get("shape")
            if shape in ("day-structured", "flat"):
                return shape
        probe = self.backend.read_file(self.repo_for(edition_id), branch,
                                       self.month_path(edition_id, 1))
        if probe is not None:
            return detect_shape(json.loads(probe[0]))
        return "day-structured"

    def _read_month_raw(self, edition_id: str, month: int,
                        branch: str) -> tuple[dict, str | None]:
        got = self.backend.read_file(self.repo_for(edition_id), branch,
                                     self.month_path(edition_id, month))
        return (json.loads(got[0]), got[1]) if got is not None else ({}, None)

    def _commit(self, identity: Identity, edition_id: str, path: str,
                data: dict, action: str, topic: str | None,
                if_match: str | None) -> WriteReceipt:
        repo = self.repo_for(edition_id)
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(repo, branch)
        try:
            sha = self.backend.write_file(
                repo, branch, path, _dump(data),
                f"curation({edition_id}): {action}",
                identity.name or identity.username,
                identity.email or f"{identity.username}@users.noreply.local",
                expected_sha=if_match)
        except ConflictError as exc:
            raise ApiProblem(409, "Write conflict", detail=str(exc),
                             type_slug="write-conflict")
        pr = self.backend.open_pr(repo, branch, f"Curation: {edition_id}")
        return WriteReceipt(branch=branch, commit_sha=sha, pr_url=pr)

    # -- edition-level -----------------------------------------------------

    def create_edition(self, identity: Identity, edition_id: str, shape: str,
                       note: str | None, topic: str | None) -> WriteReceipt:
        self._require_registered(edition_id)
        default_meta = self.backend.read_file(
            self.repo_for(edition_id), "main", self.edition_meta_path(edition_id))
        if default_meta is not None:
            raise ApiProblem(409, "Edition already exists",
                             type_slug="already-exists")
        meta: dict = {"shape": shape}
        if note:
            meta["note"] = note
        receipt = self._commit(identity, edition_id,
                               self.edition_meta_path(edition_id), meta,
                               "create edition", topic, None)
        for m in range(1, 13):
            receipt = self._commit(identity, edition_id,
                                   self.month_path(edition_id, m), {},
                                   f"scaffold month {m:02d}", topic, None)
        return receipt

    def patch_edition(self, identity: Identity, edition_id: str,
                      fields: dict, topic: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        got = self.backend.read_file(self.repo_for(edition_id), branch,
                                     self.edition_meta_path(edition_id))
        if got is None:
            raise ApiProblem(404, "Edition has no data here",
                             type_slug="unknown-edition-data")
        meta = json.loads(got[0])
        meta.update({k: v for k, v in fields.items() if v is not None})
        return self._commit(identity, edition_id,
                            self.edition_meta_path(edition_id), meta,
                            "update edition metadata", topic, got[1])

    # -- month / day / eulogy ---------------------------------------------

    def put_month(self, identity: Identity, edition_id: str, month: int,
                  raw: dict, topic: str | None,
                  if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        validate_or_raise(raw, month, self._shape(edition_id, branch), self.registry)
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"replace month {month:02d}", topic, if_match)

    def patch_day(self, identity: Identity, edition_id: str, month: int, day: int,
                  payload: dict, topic: str | None,
                  if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        if self._shape(edition_id, branch) != "day-structured":
            raise ApiProblem(422, "Day edits need a day-structured edition",
                             type_slug="not-day-structured")
        raw, sha = self._read_month_raw(edition_id, month, branch)
        key = str(day)
        if key not in raw:
            raise ApiProblem(404, "No such day in this edition",
                             type_slug="unknown-day")
        for field in ("titulus", "conclusio"):
            if field in payload:
                raw[key][field] = payload[field]
        if "order" in payload:
            current = raw[key].get("elogia", {})
            if sorted(payload["order"]) != sorted(current):
                raise ApiProblem(422, "Order must be a permutation of the day's ids",
                                 type_slug="bad-order")
            raw[key]["elogia"] = {cid: current[cid] for cid in payload["order"]}
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"edit day {month:02d}-{day:02d}", topic,
                            if_match or sha)

    def _locate(self, edition_id: str, cid: str, branch: str) -> tuple[int, dict, str | None, str | None]:
        """Return (month, raw, blob_sha, day_key|None) for the month containing cid."""
        am, _ = anchor_day(cid)
        months = [am] + [m for m in range(1, 13) if m != am]
        for m in months:
            raw, sha = self._read_month_raw(edition_id, m, branch)
            if not raw:
                continue
            if detect_shape(raw) == "flat":
                if cid in raw:
                    return m, raw, sha, None
            else:
                for day_key, obj in raw.items():
                    if cid in obj.get("elogia", {}):
                        return m, raw, sha, day_key
        raise ApiProblem(404, "Eulogy not present in this edition",
                         type_slug="unknown-eulogy")

    def put_elogium(self, identity: Identity, edition_id: str, cid: str, text: str,
                    day: int | None, position: int | None, topic: str | None,
                    if_match: str | None) -> WriteReceipt:
        self._require_registered(edition_id)
        if cid not in self.registry.entries:
            raise ApiProblem(422, "Unknown canonical id", type_slug="invalid-payload",
                             errors=[f"'{cid}' is not in the CRMEDR registry"])
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        shape = self._shape(edition_id, branch)
        am, _ = anchor_day(cid)
        if shape == "flat":
            raw, sha = self._read_month_raw(edition_id, am, branch)
            raw[cid] = text
            return self._commit(identity, edition_id, self.month_path(edition_id, am),
                                raw, f"set text for {cid}", topic, if_match or sha)
        if day is None:
            raise ApiProblem(422, "A 'day' is required for day-structured editions",
                             type_slug="day-required")
        raw, sha = self._read_month_raw(edition_id, am, branch)
        obj = raw.setdefault(str(day), {"titulus": None, "elogia": {}, "conclusio": None})
        elogia = {k: v for k, v in obj.get("elogia", {}).items() if k != cid}
        items = list(elogia.items())
        idx = len(items) if position is None else max(0, position - 1)
        items.insert(idx, (cid, text))
        obj["elogia"] = dict(items)
        return self._commit(identity, edition_id, self.month_path(edition_id, am),
                            raw, f"place {cid} under day {day}", topic,
                            if_match or sha)

    def patch_elogium(self, identity: Identity, edition_id: str, cid: str,
                      text: str, topic: str | None,
                      if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        month, raw, sha, day_key = self._locate(edition_id, cid, branch)
        if day_key is None:
            raw[cid] = text
        else:
            raw[day_key]["elogia"][cid] = text
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"correct text of {cid}", topic, if_match or sha)

    def delete_elogium(self, identity: Identity, edition_id: str, cid: str,
                       topic: str | None, if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        month, raw, sha, day_key = self._locate(edition_id, cid, branch)
        if day_key is None:
            del raw[cid]
        else:
            del raw[day_key]["elogia"][cid]
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"remove {cid}", topic, if_match or sha)

    # -- draft reads -------------------------------------------------------

    def read_month_draft(self, edition_id: str, month: int,
                         branch: str) -> dict[int, DayData]:
        raw, _ = self._read_month_raw(edition_id, month, branch)
        return parse_month_file(raw, month, detect_shape(raw), self.registry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_curation_service.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/martyrology_api/writer/service.py tests/test_curation_service.py
git commit -m "feat: CurationService — git-backed edition/month/day/eulogy writes with 409s"
```

---

### Task 16: Curation router + draft reads (`X-Curation-Branch`)

**Files:**
- Create: `src/martyrology_api/routers/curation.py`
- Modify: `src/martyrology_api/models.py` (append write models), `src/martyrology_api/routers/read.py` (draft-read support), `src/martyrology_api/app.py` (attach `app.state.curation`, mount router)
- Test: `tests/test_curation_api.py`

**Interfaces:**
- Consumes: `CurationService`/`WriteReceipt` (Task 15), `LocalGitBackend` (Task 13), `get_identity`/`Identity` (Task 9), `Authz.check`/`user_ref` (Task 10), `ApiProblem` (Task 1).
- Produces:
  - Models appended to `models.py`: `WriteReceiptOut` (`branch: str, commit_sha: str, pr_url: str`), `EditionCreateIn` (`shape: Literal["day-structured", "flat"] = "day-structured", note: str | None = None`), `EditionPatchIn` (`note: str | None = None`), `DayPatchIn` (`titulus: str | None = None, conclusio: str | None = None, order: list[str] | None = None` — pydantic `model_fields_set` distinguishes "absent" from "explicit null"), `ElogiumPutIn` (`text: str, day: int | None = None, position: int | None = None`), `ElogiumPatchIn` (`text: str`)
  - `curation.py` `router` — all under `/editions`, all take `topic: str | None` query and `If-Match` header:
    - `PUT /editions/{edition_id}` (201) — relation `can_admin`
    - `PATCH /editions/{edition_id}` — `can_admin`
    - `PUT /editions/{edition_id}/{month}` (month path regex `^\d{2}$`; body = raw month dict) — `can_edit`
    - `PATCH /editions/{edition_id}/{month}/{day}` — `can_edit`
    - `PUT|PATCH|DELETE /editions/{edition_id}/elogia/{canonical_id}` — `can_edit`
  - Shared dependency `require_relation(relation)`: 401 when anonymous (`ApiProblem(401, …, "authentication-required")`), 403 when `authz.check` denies (`type_slug="forbidden"`); FGA checks awaited, service calls run via `starlette.concurrency.run_in_threadpool` (the backend is synchronous)
  - Draft reads in `read.py`: header `X-Curation-Branch` + authenticated identity with `can_edit` on the resolved edition → month data comes from `request.app.state.curation.read_month_draft(edition_id, month, branch)` instead of the store; anonymous or unauthorized callers get the header **ignored** (spec §4); slug lookups on draft data search `day.elogia` via `slug_of`
  - `app.py`: `app.state.curation = CurationService(backend, registry, settings)` where backend = `LocalGitBackend(Path(settings.local_git_root))` when `settings.local_git_root` else `GitHubBackend(settings.github_token)` (Task 17 — until then, import-guard: fall back to `LocalGitBackend` only when `local_git_root` is set, else `None` and curation endpoints answer 503 `ApiProblem(503, "Curation not configured", type_slug="curation-unconfigured")`)

- [ ] **Step 1: Write the failing tests**

`tests/test_curation_api.py`:

```python
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.auth import Identity
from martyrology_api.config import Settings

PUB = "CatholicOS/martyrology-api"


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def seed_repo(root: Path, repo: str, files: dict[str, str]):
    bare = root / f"{repo}.git"
    bare.mkdir(parents=True)
    run(["git", "init", "--bare", "-b", "main", "."], bare)
    seed = root / f"seed-{repo.replace('/', '-')}"
    run(["git", "clone", str(bare), str(seed)], root)
    for rel, content in files.items():
        f = seed / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    run(["git", "add", "-A"], seed)
    run(["git", "-c", "user.name=s", "-c", "user.email=s@x", "commit", "-m", "s"], seed)
    run(["git", "push", "origin", "main"], seed)


class StaticAuth:
    async def identity(self, token):
        return Identity(subject="u123", username="jdoe",
                        email="j@example.org") if token == "good" else None


class Grants:
    def __init__(self, relations):  # set of (relation, edition_id)
        self.relations = relations

    async def check(self, user, relation, edition_id):
        return (relation, edition_id) in self.relations


@pytest.fixture
def client(tmp_path, crmedr_path, clbdr_path, data_paths):
    root = tmp_path / "gitroot"
    seed_repo(root, PUB, {
        "data/editions/martyrologium_romanum_1749/edition.json":
            '{"shape": "day-structured"}',
        "data/editions/martyrologium_romanum_1749/01.json": json.dumps({
            "1": {"titulus": "t1", "elogia": {"mr:0102-concordius": "Spoleti."},
                  "conclusio": "c"}})})
    seed_repo(root, "CatholicOS/martyrology-texts", {})
    settings = Settings(
        _env_file=None,
        data_path=os.pathsep.join(str(p) for p in data_paths),
        crmedr_path=crmedr_path, clbdr_path=clbdr_path,
        local_git_root=str(root))
    app = create_app(settings)
    app.state.authenticator = StaticAuth()
    app.state.authz = Grants({("can_edit", "martyrologium_romanum_1749"),
                              ("can_admin", "martyrologium_romanum_1914_en_unofficial")})
    return TestClient(app)


AUTH = {"Authorization": "Bearer good"}


def test_write_requires_auth(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                     json={"text": "x"})
    assert r.status_code == 401
    r = client.patch("/api/v1/editions/martyrologium_romanum_2004/elogia/mr:0101-basilius",
                     json={"text": "x"}, headers=AUTH)
    assert r.status_code == 403  # no can_edit grant on 2004


def test_patch_elogium_roundtrip(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                     json={"text": "Spoleti, emendatum."}, headers=AUTH)
    assert r.status_code == 200
    b = r.json()
    assert b["branch"] == "curation/jdoe/edits"
    assert b["pr_url"].startswith("local://")
    assert len(b["commit_sha"]) == 40


def test_draft_read_via_header(client):
    client.patch("/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                 json={"text": "Draft only."}, headers=AUTH)
    # published read unchanged (store serves the fixture files on disk)
    pub = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/01").json()
    assert pub["elogia"][0]["text"] != "Draft only."
    # draft read sees the branch
    draft = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/01/01",
        headers=AUTH | {"X-Curation-Branch": "curation/jdoe/edits"}).json()
    assert draft["elogia"][0]["text"] == "Draft only."
    # anonymous caller: header ignored
    anon = client.get(
        "/api/v1/elogia/edition/martyrologium_romanum_1749/01/01",
        headers={"X-Curation-Branch": "curation/jdoe/edits"}).json()
    assert anon["elogia"][0]["text"] != "Draft only."


def test_put_month_validation_error(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1749/01",
                   json={"1": {"elogia": {"mr:9999-nobody": "x"}}}, headers=AUTH)
    assert r.status_code == 422
    assert r.json()["errors"]


def test_patch_day(client):
    r = client.patch("/api/v1/editions/martyrologium_romanum_1749/01/01",
                     json={"titulus": "Novus titulus"}, headers=AUTH)
    assert r.status_code == 200


def test_delete_elogium(client):
    r = client.request("DELETE",
                       "/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                       headers=AUTH)
    assert r.status_code == 200
    r2 = client.request("DELETE",
                        "/api/v1/editions/martyrologium_romanum_1749/elogia/mr:0102-concordius",
                        headers=AUTH)
    assert r2.status_code == 404


def test_create_edition_needs_admin(client):
    r = client.put("/api/v1/editions/martyrologium_romanum_1914_en_unofficial",
                   json={"shape": "day-structured"}, headers=AUTH)
    assert r.status_code == 201
    r2 = client.put("/api/v1/editions/martyrologium_romanum_1749",
                    json={"shape": "day-structured"}, headers=AUTH)
    assert r2.status_code == 403  # only can_edit, not can_admin, on 1749
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_curation_api.py -v`
Expected: FAIL — 404s (routes don't exist yet).

- [ ] **Step 3: Append the write models to `models.py`**

```python
from typing import Literal


class WriteReceiptOut(BaseModel):
    branch: str
    commit_sha: str
    pr_url: str


class EditionCreateIn(BaseModel):
    shape: Literal["day-structured", "flat"] = "day-structured"
    note: str | None = None


class EditionPatchIn(BaseModel):
    note: str | None = None


class DayPatchIn(BaseModel):
    titulus: str | None = None
    conclusio: str | None = None
    order: list[str] | None = None


class ElogiumPutIn(BaseModel):
    text: str
    day: int | None = None
    position: int | None = None


class ElogiumPatchIn(BaseModel):
    text: str
```

- [ ] **Step 4: Implement `routers/curation.py`**

```python
from fastapi import APIRouter, Body, Depends, Header, Path, Request
from starlette.concurrency import run_in_threadpool

from ..auth import Identity, get_identity
from ..authz import user_ref
from ..models import (DayPatchIn, EditionCreateIn, EditionPatchIn,
                      ElogiumPatchIn, ElogiumPutIn, WriteReceiptOut)
from ..problems import ApiProblem
from ..writer.service import WriteReceipt

router = APIRouter(prefix="/editions")

MONTH = Path(pattern=r"^\d{2}$")
DAY = Path(pattern=r"^\d{2}$")


def _service(request: Request):
    svc = request.app.state.curation
    if svc is None:
        raise ApiProblem(503, "Curation not configured",
                         detail="No VCS backend is configured in this deployment.",
                         type_slug="curation-unconfigured")
    return svc


def require_relation(relation: str):
    async def dep(request: Request, edition_id: str,
                  identity: Identity | None = Depends(get_identity)) -> Identity:
        if identity is None:
            raise ApiProblem(401, "Authentication required",
                             type_slug="authentication-required")
        allowed = await request.app.state.authz.check(
            user_ref(identity), relation, edition_id)
        if not allowed:
            raise ApiProblem(403, "Forbidden",
                             detail=f"'{relation}' on '{edition_id}' denied.",
                             type_slug="forbidden")
        return identity
    return dep


def _out(receipt: WriteReceipt) -> WriteReceiptOut:
    return WriteReceiptOut(branch=receipt.branch, commit_sha=receipt.commit_sha,
                           pr_url=receipt.pr_url)


@router.put("/{edition_id}", status_code=201)
async def put_edition(request: Request, edition_id: str, body: EditionCreateIn,
                      topic: str | None = None,
                      identity: Identity = Depends(require_relation("can_admin"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.create_edition, identity, edition_id, body.shape, body.note, topic)
    return _out(receipt)


@router.patch("/{edition_id}")
async def patch_edition(request: Request, edition_id: str, body: EditionPatchIn,
                        topic: str | None = None,
                        identity: Identity = Depends(require_relation("can_admin"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.patch_edition, identity, edition_id, body.model_dump(), topic)
    return _out(receipt)


@router.put("/{edition_id}/{month}")
async def put_month(request: Request, edition_id: str,
                    month: str = MONTH, body: dict = Body(...),
                    topic: str | None = None,
                    if_match: str | None = Header(default=None),
                    identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.put_month, identity, edition_id, int(month), body, topic, if_match)
    return _out(receipt)


@router.patch("/{edition_id}/{month}/{day}")
async def patch_day(request: Request, edition_id: str,
                    month: str = MONTH, day: str = DAY,
                    body: DayPatchIn = Body(...),
                    topic: str | None = None,
                    if_match: str | None = Header(default=None),
                    identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    payload = {k: getattr(body, k) for k in body.model_fields_set}
    receipt = await run_in_threadpool(
        svc.patch_day, identity, edition_id, int(month), int(day),
        payload, topic, if_match)
    return _out(receipt)


@router.put("/{edition_id}/elogia/{canonical_id}")
async def put_elogium(request: Request, edition_id: str, canonical_id: str,
                      body: ElogiumPutIn,
                      topic: str | None = None,
                      if_match: str | None = Header(default=None),
                      identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.put_elogium, identity, edition_id, canonical_id, body.text,
        body.day, body.position, topic, if_match)
    return _out(receipt)


@router.patch("/{edition_id}/elogia/{canonical_id}")
async def patch_elogium(request: Request, edition_id: str, canonical_id: str,
                        body: ElogiumPatchIn,
                        topic: str | None = None,
                        if_match: str | None = Header(default=None),
                        identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.patch_elogium, identity, edition_id, canonical_id, body.text,
        topic, if_match)
    return _out(receipt)


@router.delete("/{edition_id}/elogia/{canonical_id}")
async def delete_elogium(request: Request, edition_id: str, canonical_id: str,
                         topic: str | None = None,
                         if_match: str | None = Header(default=None),
                         identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.delete_elogium, identity, edition_id, canonical_id, topic, if_match)
    return _out(receipt)
```

Route-ordering caution: `PUT /{edition_id}/elogia/{canonical_id}` must be declared **before** `PUT /{edition_id}/{month}` in the file (Starlette matches in order, and `elogia` would otherwise be captured as a month and rejected by the regex). Rearrange the handlers so both `/elogia/…` routes precede the `/{month}` routes, or keep the regex-`^\d{2}$` constraint (which rejects `elogia`) and rely on FastAPI falling through to the next route — the constraint alone is sufficient in current Starlette, but declaring the more specific routes first is the robust choice; the test suite catches either mistake.

- [ ] **Step 5: Wire draft reads into `routers/read.py`**

Add a helper and use it in `get_elogia` wherever month/day data is fetched:

```python
from ..authz import user_ref
from ..registry import slug_of


async def _draft_months(request: Request, identity, edition_id: str,
                        month: int) -> dict | None:
    branch = request.headers.get("x-curation-branch")
    if not branch or identity is None:
        return None
    svc = getattr(request.app.state, "curation", None)
    if svc is None:
        return None
    if not await request.app.state.authz.check(
            user_ref(identity), "can_edit", edition_id):
        return None
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(svc.read_month_draft, edition_id, month, branch)
```

In `get_elogia`, after `resolution` is computed replace the data-fetch lines:

```python
    months = await _draft_months(request, identity, resolution.edition_id, req.month)
    if months is None:
        months = store.month(resolution.edition_id, req.month)
    # month case:
    if req.day is None:
        ...build from `months` as before...
    day_data = months.get(req.day)
```

and replace the `find_by_slug` call with an inline search over the (possibly draft) day:

```python
    hit = next((e for e in day_data.elogia if slug_of(e.id) == req.slug), None)
```

(`store.find_by_slug` remains for direct users but the router path now works for both sources.)

- [ ] **Step 6: Wire the service in `app.py`**

```python
from pathlib import Path as _Path

from .routers import curation, discovery, read
from .writer.local import LocalGitBackend
from .writer.service import CurationService
# in create_app, after app.state.authz:
    if settings.local_git_root:
        backend = LocalGitBackend(_Path(settings.local_git_root))
    else:
        backend = None  # GitHubBackend arrives in Task 17
    app.state.curation = (CurationService(backend, registry, settings)
                          if backend is not None else None)
# and mount, keeping discovery before read:
    app.include_router(discovery.router, prefix="/api/v1")
    app.include_router(curation.router, prefix="/api/v1")
    app.include_router(read.router, prefix="/api/v1")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_curation_api.py -v && pytest -q`
Expected: 7 passed; full suite green (read/licensing/caching unaffected).

- [ ] **Step 8: Commit**

```bash
git add src/martyrology_api/routers/curation.py src/martyrology_api/routers/read.py src/martyrology_api/models.py src/martyrology_api/app.py tests/test_curation_api.py
git commit -m "feat: curation endpoints with FGA gates, and X-Curation-Branch draft reads"
```

---

### Task 17: GitHubBackend

**Files:**
- Create: `src/martyrology_api/writer/github.py`
- Modify: `src/martyrology_api/app.py` (use it when `github_token` set and no `local_git_root`)
- Test: `tests/test_github_backend.py`

**Interfaces:**
- Consumes: `ConflictError` (Task 13).
- Produces: `class GitHubBackend(token: str, api_url: str = "https://api.github.com", transport: httpx.BaseTransport | None = None)` implementing `VcsBackend` (synchronous, `httpx.Client`):
  - `read_file`: `GET /repos/{repo}/contents/{path}?ref={branch}` → decode base64 `content`, return `(bytes, sha)`; 404 → `None`
  - `ensure_branch`: `GET /repos/{repo}/git/ref/heads/{branch}`; on 404 → `GET /repos/{repo}` for `default_branch`, `GET /repos/{repo}/git/ref/heads/{default}` for its sha, `POST /repos/{repo}/git/refs` with `{"ref": "refs/heads/{branch}", "sha": …}`
  - `write_file`: `PUT /repos/{repo}/contents/{path}` with `{"message", "content": base64, "branch", "committer": {"name", "email"}, "author": {"name", "email"}}` + `"sha"` when updating (current blob sha — fetched via `read_file` unless `expected_sha` given, in which case `expected_sha` is sent verbatim); 409/422 with sha-mismatch message → raise `ConflictError`; returns `commit.sha`
  - `open_pr`: `GET /repos/{repo}/pulls?head={owner}:{branch}&state=open` → first `html_url` if any; else `POST /repos/{repo}/pulls` `{"title", "head": branch, "base": default_branch}` → `html_url`
  - All requests carry `Authorization: Bearer {token}` and `Accept: application/vnd.github+json`.

- [ ] **Step 1: Write the failing tests**

`tests/test_github_backend.py`:

```python
import base64
import json

import httpx
import pytest

from martyrology_api.writer.base import ConflictError
from martyrology_api.writer.github import GitHubBackend

REPO = "CatholicOS/martyrology-texts"


class FakeGitHub:
    def __init__(self):
        self.files = {("main", "data/x.json"): b'{"a": 1}'}
        self.branches = {"main": "c0ffee"}
        self.prs = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        assert request.headers["authorization"] == "Bearer tok"
        if m == "GET" and p == f"/repos/{REPO}":
            return httpx.Response(200, json={"default_branch": "main"})
        if m == "GET" and p.startswith(f"/repos/{REPO}/git/ref/heads/"):
            br = p.rsplit("/", 1)[-1]
            if br in self.branches:
                return httpx.Response(200, json={"object": {"sha": self.branches[br]}})
            return httpx.Response(404, json={})
        if m == "POST" and p == f"/repos/{REPO}/git/refs":
            body = json.loads(request.content)
            self.branches[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return httpx.Response(201, json={})
        if m == "GET" and p.startswith(f"/repos/{REPO}/contents/"):
            path = p.removeprefix(f"/repos/{REPO}/contents/")
            ref = request.url.params.get("ref", "main")
            content = self.files.get((ref, path))
            if content is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={
                "content": base64.b64encode(content).decode(),
                "sha": f"blob-{ref}-{path}"})
        if m == "PUT" and p.startswith(f"/repos/{REPO}/contents/"):
            path = p.removeprefix(f"/repos/{REPO}/contents/")
            body = json.loads(request.content)
            branch = body["branch"]
            existing = self.files.get((branch, path))
            if existing is not None and body.get("sha") != f"blob-{branch}-{path}":
                return httpx.Response(409, json={"message": "sha mismatch"})
            self.files[(branch, path)] = base64.b64decode(body["content"])
            return httpx.Response(200, json={"commit": {"sha": "abc123"},
                                             "content": {"sha": "newblob"}})
        if m == "GET" and p == f"/repos/{REPO}/pulls":
            return httpx.Response(200, json=self.prs)
        if m == "POST" and p == f"/repos/{REPO}/pulls":
            pr = {"html_url": f"https://github.com/{REPO}/pull/1"}
            self.prs.append(pr)
            return httpx.Response(201, json=pr)
        return httpx.Response(500, json={"unhandled": p})


@pytest.fixture
def gh():
    fake = FakeGitHub()
    backend = GitHubBackend("tok", transport=httpx.MockTransport(fake.handler))
    return fake, backend


def test_read_file(gh):
    _, b = gh
    content, sha = b.read_file(REPO, "main", "data/x.json")
    assert content == b'{"a": 1}' and sha == "blob-main-data/x.json"
    assert b.read_file(REPO, "main", "nope.json") is None


def test_ensure_branch_creates_from_default(gh):
    fake, b = gh
    b.ensure_branch(REPO, "curation/jdoe/edits")
    assert fake.branches["curation/jdoe/edits"] == "c0ffee"
    b.ensure_branch(REPO, "curation/jdoe/edits")  # idempotent


def test_write_new_and_update_and_conflict(gh):
    fake, b = gh
    b.ensure_branch(REPO, "curation/jdoe/edits")
    sha = b.write_file(REPO, "curation/jdoe/edits", "data/y.json", b"{}",
                       "msg", "J", "j@x")
    assert sha == "abc123"
    # update with correct current sha (auto-fetched)
    b.write_file(REPO, "curation/jdoe/edits", "data/y.json", b'{"b": 2}',
                 "msg2", "J", "j@x")
    with pytest.raises(ConflictError):
        b.write_file(REPO, "curation/jdoe/edits", "data/y.json", b'{"c": 3}',
                     "msg3", "J", "j@x", expected_sha="stale-sha")


def test_open_pr_idempotent(gh):
    fake, b = gh
    url = b.open_pr(REPO, "curation/jdoe/edits", "Curation")
    assert url == f"https://github.com/{REPO}/pull/1"
    assert b.open_pr(REPO, "curation/jdoe/edits", "Curation") == url
    assert len(fake.prs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_github_backend.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `github.py`**

```python
import base64

import httpx

from .base import ConflictError


class GitHubBackend:
    def __init__(self, token: str, api_url: str = "https://api.github.com",
                 transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=api_url, transport=transport,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"})

    def _default_branch(self, repo: str) -> str:
        r = self._client.get(f"/repos/{repo}")
        r.raise_for_status()
        return r.json()["default_branch"]

    def ensure_branch(self, repo: str, branch: str) -> None:
        r = self._client.get(f"/repos/{repo}/git/ref/heads/{branch}")
        if r.status_code == 200:
            return
        default = self._default_branch(repo)
        base = self._client.get(f"/repos/{repo}/git/ref/heads/{default}")
        base.raise_for_status()
        created = self._client.post(
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}",
                  "sha": base.json()["object"]["sha"]})
        created.raise_for_status()

    def read_file(self, repo: str, branch: str, path: str) -> tuple[bytes, str] | None:
        r = self._client.get(f"/repos/{repo}/contents/{path}",
                             params={"ref": branch})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
        return base64.b64decode(body["content"]), body["sha"]

    def write_file(self, repo: str, branch: str, path: str, content: bytes,
                   message: str, author_name: str, author_email: str,
                   expected_sha: str | None = None) -> str:
        payload = {"message": message, "branch": branch,
                   "content": base64.b64encode(content).decode(),
                   "committer": {"name": author_name, "email": author_email},
                   "author": {"name": author_name, "email": author_email}}
        sha = expected_sha
        if sha is None:
            current = self.read_file(repo, branch, path)
            if current is not None:
                sha = current[1]
        if sha is not None:
            payload["sha"] = sha
        r = self._client.put(f"/repos/{repo}/contents/{path}", json=payload)
        if r.status_code in (409, 422):
            raise ConflictError(f"{path} on {branch}: {r.json().get('message')}")
        r.raise_for_status()
        return r.json()["commit"]["sha"]

    def open_pr(self, repo: str, branch: str, title: str) -> str:
        owner = repo.split("/")[0]
        existing = self._client.get(f"/repos/{repo}/pulls",
                                    params={"head": f"{owner}:{branch}",
                                            "state": "open"})
        existing.raise_for_status()
        prs = existing.json()
        if prs:
            return prs[0]["html_url"]
        created = self._client.post(
            f"/repos/{repo}/pulls",
            json={"title": title, "head": branch,
                  "base": self._default_branch(repo)})
        created.raise_for_status()
        return created.json()["html_url"]
```

- [ ] **Step 4: Use it in `app.py`** (replace the `backend = None` fallback from Task 16):

```python
from .writer.github import GitHubBackend
# ...
    if settings.local_git_root:
        backend = LocalGitBackend(_Path(settings.local_git_root))
    elif settings.github_token:
        backend = GitHubBackend(settings.github_token)
    else:
        backend = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_github_backend.py -v && pytest -q`
Expected: 4 passed; full suite green.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/writer/github.py src/martyrology_api/app.py tests/test_github_backend.py
git commit -m "feat: GitHubBackend via contents/refs/pulls REST with conflict mapping"
```

---

### Task 18: Real-data smoke tests, dev entrypoint, docs

**Files:**
- Create: `tests/test_smoke_realdata.py`, `.env.example`
- Modify: `README.md` (running-the-API section), `docs/architecture.md` (point the API-surface sketch at the spec)
- Test: `tests/test_smoke_realdata.py`

**Interfaces:**
- Consumes: everything.
- Produces: confidence that the app runs against the real repo data (`data/editions` + sibling `../crmedr`, `../clbdr` clones), and a documented dev entrypoint.

- [ ] **Step 1: Write the smoke tests** (skipped automatically when sibling registries are absent, e.g. in CI without checkouts)

`tests/test_smoke_realdata.py`:

```python
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from martyrology_api.app import create_app
from martyrology_api.config import Settings

ROOT = Path(__file__).parent.parent
CRMEDR = ROOT.parent / "crmedr"
CLBDR = ROOT.parent / "clbdr"

pytestmark = pytest.mark.skipif(
    not (CRMEDR.is_dir() and CLBDR.is_dir() and (ROOT / "data/editions").is_dir()),
    reason="real registries/data not present")


@pytest.fixture(scope="module")
def client():
    settings = Settings(_env_file=None,
                        data_path=str(ROOT / "data/editions"),
                        crmedr_path=CRMEDR, clbdr_path=CLBDR)
    return TestClient(create_app(settings))


def test_editions_lists_the_clbdr_line(client):
    eds = {e["edition_id"] for e in client.get("/api/v1/editions").json()["editions"]}
    assert "martyrologium_romanum_1749" in eds
    assert "martyrologium_romanum_2004" in eds


def test_1749_january_second(client):
    r = client.get("/api/v1/elogia/edition/martyrologium_romanum_1749/01/02")
    assert r.status_code == 200
    b = r.json()
    assert b["titulus"] and b["conclusio"]
    assert len(b["elogia"]) > 0
    assert all(e["id"].startswith("mr:") for e in b["elogia"])


def test_year_resolver_hits_1749(client):
    r = client.get("/api/v1/elogia/1800/01/02")
    assert r.status_code == 200
    assert r.json()["metadata"]["edition"] == "martyrologium_romanum_1749"


def test_full_year_no_crashes(client):
    from martyrology_api.grammar import DAYS_IN_MONTH
    for month, ndays in DAYS_IN_MONTH.items():
        r = client.get(f"/api/v1/elogia/edition/martyrologium_romanum_1749/{month:02d}")
        assert r.status_code == 200, f"month {month}"
        assert len(r.json()["days"]) > 25


def test_catalog_size_matches_registry(client):
    items = client.get("/api/v1/elogia").json()["elogia"]
    assert len(items) > 3000  # current + deprecated CRMEDR ids
```

- [ ] **Step 2: Run the smoke tests**

Run: `pytest tests/test_smoke_realdata.py -v`
Expected: 5 passed locally (sibling repos exist here). If any fail, the failure is real — fix the code, not the test (these are the golden acceptance checks against the digitized editions).

- [ ] **Step 3: Write `.env.example`**

```bash
# Read data: os.pathsep-separated base dirs, each holding one dir per edition
MARTYROLOGY_DATA_PATH=data/editions
MARTYROLOGY_CRMEDR_PATH=../crmedr
MARTYROLOGY_CLBDR_PATH=../clbdr

# Zitadel (empty = auth disabled: public editions only, no writes)
MARTYROLOGY_ZITADEL_ISSUER=
MARTYROLOGY_ZITADEL_CLIENT_ID=
MARTYROLOGY_ZITADEL_CLIENT_SECRET=

# OpenFGA (empty = authz disabled: fail closed)
MARTYROLOGY_OPENFGA_API_URL=
MARTYROLOGY_OPENFGA_STORE_ID=
MARTYROLOGY_OPENFGA_MODEL_ID=

# Curation backend: set ONE of these (github wins in prod)
MARTYROLOGY_GITHUB_TOKEN=
MARTYROLOGY_LOCAL_GIT_ROOT=
```

- [ ] **Step 4: Update `README.md`** — append after the "Status" section:

````markdown
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

The API surface, response model, auth and curation design are specified in
[docs/superpowers/specs/2026-07-22-martyrology-api-v1-design.md](docs/superpowers/specs/2026-07-22-martyrology-api-v1-design.md).
````

And in `docs/architecture.md`, replace the "## API surface (sketch)" section body with a pointer:

```markdown
## API surface

Superseded by the approved design spec:
[superpowers/specs/2026-07-22-martyrology-api-v1-design.md](superpowers/specs/2026-07-22-martyrology-api-v1-design.md)
(implemented in `src/martyrology_api/`).
```

- [ ] **Step 5: Full suite + commit**

Run: `pytest -q`
Expected: all green.

```bash
git add tests/test_smoke_realdata.py .env.example README.md docs/architecture.md
git commit -m "feat: real-data smoke tests, dev entrypoint docs, .env.example"
```

---

## Plan Self-Review Notes (already applied)

1. **Spec coverage**: read grammar incl. leap day (T6/T7), resolution + pre-1584 + edition-unavailable (T5), response models (T7), discovery governance/availability (T8), catalog (T8), redacted-200 licensing (T11), Zitadel PATs via introspection (T9), OpenFGA relations exactly as specced (T10), caching tiers + ETag (T12), all seven write endpoints + If-Match 409 + branch/PR mechanics + X-Curation-Branch (T13–T16), GitHub backend (T17), golden real-data tests (T18). The OpenFGA **model** itself (type definitions) is deployed to the FGA store out-of-band via cdcf-infra — this API only *checks* relations; noted deliberately out of implementation scope.
2. **Deviation from spec, recorded**: OpenFGA called via plain HTTP (httpx) instead of `openfga-sdk` — same wire protocol, fewer deps (see Tech Stack note).
3. **Type consistency spot-checks**: `Identity(subject, username, email, name)` used identically in T9/T11/T15/T16; `WriteReceipt(branch, commit_sha, pr_url)` T15/T16; `read_file -> tuple[bytes, str] | None` T13/T15/T17; `parse_month_file(raw, month, shape, registry)` T4/T15; relation strings `can_read_texts`/`can_edit`/`can_admin` T10/T11/T16.







