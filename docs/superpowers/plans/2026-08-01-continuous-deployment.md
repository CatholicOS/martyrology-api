# Continuous Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `martyrology-api` to the Plesk-managed VPS automatically on every published GitHub release, bundling the private text corpus and the two public registries into one verifiable, rollback-able artifact.

**Architecture:** Three data repositories are pinned as git submodules. On release, CI builds a wheel plus an offline wheelhouse, assembles them with the data trees and a `manifest.json` into a tarball, scp's it to the VPS, and runs a deploy script over ssh as a dedicated non-chrooted user. The script verifies the checksum, builds a venv offline, smoke-checks the new release on a scratch port, flips a `current` symlink, restarts systemd, and rolls back automatically if the live health check fails.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pydantic v2, hatchling, uv, pytest, GitHub Actions, systemd, bash, nginx (via Plesk).

**Spec:** `docs/superpowers/specs/2026-08-01-continuous-deployment-design.md`

## Global Constraints

- Python floor is `>=3.12`; ruff `target-version = "py312"`, `line-length = 100`, lint select `["E", "F", "W", "I", "UP", "B"]`.
- Coverage gate is `fail_under = 90` over `source = ["martyrology_api"]`. Code under `scripts/` is linted by ruff but not counted for coverage and not checked by pyright (`include = ["src", "tests"]`).
- CI runs `ruff check src tests scripts` and `ruff format --check src tests scripts` — every new Python file under those roots must be ruff-clean and ruff-formatted.
- All `.gitmodules` URLs must be **HTTPS**, never `git@github.com:`. `actions/checkout` authenticates submodules via an HTTP extraheader; an SSH URL breaks both the release workflow and Dependabot.
- The runner is pinned to `ubuntu-24.04`, never `ubuntu-latest`. The VPS is Ubuntu 24.04.4 / glibc 2.39, and the wheelhouse ABI must match.
- Bundle artifact name: `martyrology-<version>-linux-x86_64-cp312.tar.gz`.
- `bundle_format` is `1`.
- `APP_DIR` on the VPS is `/opt/martyrology`. Default service port is `8412`.
- Third-party GitHub Actions are pinned to full commit SHAs (established by commit `f253b38`).
- Commits are GPG-signed (`git commit -S`). Never bypass signing.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/martyrology_api/manifest.py` | Parse and validate a deployment manifest; return `None` for any unusable manifest. |
| `tests/test_manifest.py` | Unit tests for manifest parsing. |
| `tests/test_health_api.py` | Endpoint tests for `/healthz`. |
| `scripts/deploy/build_bundle.py` | Assemble the release bundle and write `manifest.json`. |
| `tests/test_build_bundle.py` | Unit tests for bundle assembly, including a writer/reader cross-check. |
| `scripts/deploy/deploy.sh` | On-VPS installer: verify, extract, venv, smoke-check, flip, restart, roll back. |
| `tests/test_deploy_script.py` | Subprocess tests for `deploy.sh` rejection paths and `--dry-run`. |
| `scripts/deploy/setup-vps-deploy-user.sh` | One-time root provisioning of users, dirs, sudoers, units, runtime.env. |
| `.github/workflows/deploy.yml` | Release → build → scp → ssh deploy. |
| `.gitmodules` | Three HTTPS submodule pins. |

**Modified:**

| Path | Change |
|---|---|
| `src/martyrology_api/config.py` | Add `manifest_path` setting and `manifest_file` property. |
| `src/martyrology_api/models.py` | Add `HealthOut`. |
| `src/martyrology_api/app.py` | Add the `/healthz` route beside the service document at `app.py:47`. |
| `.github/dependabot.yml` | Add the `gitsubmodule` ecosystem. |
| `.github/workflows/ci.yml` | Add a `shellcheck` job. |
| `.env.example` | Add a commented production block. |
| `docs/architecture.md` | Replace the three-option deployment list with a pointer to the spec. |

---

### Task 1: Manifest reader and the `/healthz` endpoint

The only runtime code change. `/healthz` is consumed by the deploy script's
pre-flip smoke check and post-restart rollback poll, so it must answer even when
the manifest is missing or corrupt — a health endpoint that 500s on a bad
manifest would turn a cosmetic problem into a failed deploy and a rollback.

**Files:**
- Create: `src/martyrology_api/manifest.py`
- Create: `tests/test_manifest.py`
- Create: `tests/test_health_api.py`
- Modify: `src/martyrology_api/config.py`
- Modify: `src/martyrology_api/models.py`
- Modify: `src/martyrology_api/app.py:47`

**Interfaces:**
- Consumes: `Settings` (`src/martyrology_api/config.py`), `Store.available()` returning `set[str]` (`src/martyrology_api/store.py:134`), `__version__` (`src/martyrology_api/__init__.py`).
- Produces:
  - `martyrology_api.manifest.BUNDLE_FORMAT: int` (value `1`)
  - `martyrology_api.manifest.Manifest` — pydantic model with fields `bundle_format: int`, `api_version: str`, `api_commit: str`, `data: dict[str, str]`, `python_requires: str`, `files: dict[str, str]`
  - `martyrology_api.manifest.load_manifest(path: Path | None) -> Manifest | None`
  - `Settings.manifest_path: str` and `Settings.manifest_file -> Path | None`
  - `GET /healthz` returning `{"status": "ok", "version": str, "data": {"crmedr": str|None, "clbdr": str|None, "texts": str|None}, "editions": list[str]}`
  - Task 3 validates its generated manifest against `Manifest`.

- [ ] **Step 1: Write the failing manifest tests**

Create `tests/test_manifest.py`:

```python
import json
from pathlib import Path

from martyrology_api.manifest import load_manifest

GOOD = {
    "bundle_format": 1,
    "api_version": "0.1.0",
    "api_commit": "a" * 40,
    "data": {"texts": "t" * 40, "crmedr": "c" * 40, "clbdr": "l" * 40},
    "python_requires": ">=3.12",
    "files": {"data/crmedr/x.json": "0" * 64},
}


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_none_path_yields_none():
    assert load_manifest(None) is None


def test_missing_file_yields_none(tmp_path: Path):
    assert load_manifest(tmp_path / "absent.json") is None


def test_malformed_json_yields_none(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_manifest(path) is None


def test_missing_required_field_yields_none(tmp_path: Path):
    payload = {k: v for k, v in GOOD.items() if k != "api_commit"}
    assert load_manifest(_write(tmp_path, payload)) is None


def test_unknown_bundle_format_yields_none(tmp_path: Path):
    assert load_manifest(_write(tmp_path, {**GOOD, "bundle_format": 99})) is None


def test_good_manifest_parses(tmp_path: Path):
    manifest = load_manifest(_write(tmp_path, GOOD))
    assert manifest is not None
    assert manifest.api_commit == "a" * 40
    assert manifest.data["texts"] == "t" * 40
    assert manifest.files["data/crmedr/x.json"] == "0" * 64
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'martyrology_api.manifest'`

- [ ] **Step 3: Implement the manifest module**

Create `src/martyrology_api/manifest.py`:

```python
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

BUNDLE_FORMAT = 1


class Manifest(BaseModel):
    """The deployment manifest written into every release bundle.

    `data` maps a data-repository nickname (texts, crmedr, clbdr) to the
    commit SHA that was bundled; `files` maps every bundled path to its
    sha256. Together they are the auditable record of which corpus is live.
    """

    bundle_format: int
    api_version: str
    api_commit: str
    data: dict[str, str]
    python_requires: str
    files: dict[str, str]


def load_manifest(path: Path | None) -> Manifest | None:
    """Read a deployment manifest, or None when it is absent or unusable.

    Absence is the ordinary development case: no bundle, no manifest. A
    malformed manifest, or one written by a future bundle format, is also
    reported as absent rather than raised. /healthz is what the deploy
    script polls to decide whether to roll back, so it must keep answering
    even when the manifest is the thing that is broken.
    """
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError:
        return None
    if manifest.bundle_format != BUNDLE_FORMAT:
        return None
    return manifest
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_manifest.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Write the failing `/healthz` tests**

Create `tests/test_health_api.py`:

```python
import json
from pathlib import Path

MANIFEST = {
    "bundle_format": 1,
    "api_version": "0.1.0",
    "api_commit": "a" * 40,
    "data": {"texts": "t" * 40, "crmedr": "c" * 40, "clbdr": "l" * 40},
    "python_requires": ">=3.12",
    "files": {},
}


def test_healthz_ok_without_a_manifest(make_client):
    body = make_client().get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["data"] == {"crmedr": None, "clbdr": None, "texts": None}


def test_healthz_lists_available_editions_sorted(make_client):
    body = make_client().get("/healthz").json()
    assert body["editions"], "fixtures should expose at least one edition"
    assert body["editions"] == sorted(body["editions"])


def test_healthz_reports_commits_from_the_manifest(make_client, tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    body = make_client(manifest_path=str(path)).get("/healthz").json()
    assert body["data"] == {"crmedr": "c" * 40, "clbdr": "l" * 40, "texts": "t" * 40}


def test_healthz_survives_a_corrupt_manifest(make_client, tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    response = make_client(manifest_path=str(path)).get("/healthz")
    assert response.status_code == 200
    assert response.json()["data"] == {"crmedr": None, "clbdr": None, "texts": None}
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `pytest tests/test_health_api.py -v`
Expected: FAIL — 404 on `/healthz`, and `Settings` rejects the `manifest_path` keyword.

- [ ] **Step 7: Add the setting**

In `src/martyrology_api/config.py`, add after the `access_info_url` field (line 18):

```python
    manifest_path: str = ""  # deployment manifest.json; empty outside a bundle
```

and add this property beside the other properties:

```python
    @property
    def manifest_file(self) -> Path | None:
        return Path(self.manifest_path) if self.manifest_path else None
```

`Path` is already imported at `config.py:2`.

- [ ] **Step 8: Add the response model**

In `src/martyrology_api/models.py`, add beside the other `*Out` models:

```python
class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    data: dict[str, str | None]
    editions: list[str]
```

`Literal` is already imported at `models.py:1`.

- [ ] **Step 9: Add the endpoint**

In `src/martyrology_api/app.py`, extend the imports:

```python
from .manifest import load_manifest
from .models import HealthOut
```

and add this route immediately after the `service_document` function (which ends at `app.py:61`), before `return app`:

```python
    @app.get("/healthz", tags=["service"], response_model=HealthOut)
    def healthz() -> HealthOut:
        manifest = load_manifest(settings.manifest_file)
        commits: dict[str, str | None] = {"crmedr": None, "clbdr": None, "texts": None}
        if manifest is not None:
            for key in commits:
                commits[key] = manifest.data.get(key)
        return HealthOut(
            status="ok",
            version=__version__,
            data=commits,
            editions=sorted(app.state.store.available()),
        )
```

- [ ] **Step 10: Run the full suite**

Run: `pytest -q --cov --cov-branch --cov-report=term-missing`
Expected: PASS, coverage still at or above 90. `tests/test_openapi.py` has no
schema snapshot, so the new route needs no fixture update; its
`test_openapi_every_route_declares_responses` loop covers `/healthz` automatically.

- [ ] **Step 11: Lint and typecheck**

Run: `ruff check src tests scripts && ruff format --check src tests scripts && pyright`
Expected: all clean.

- [ ] **Step 12: Commit**

```bash
git add src/martyrology_api/manifest.py src/martyrology_api/config.py \
        src/martyrology_api/models.py src/martyrology_api/app.py \
        tests/test_manifest.py tests/test_health_api.py
git commit -S -m "Add deployment manifest reader and /healthz endpoint"
```

---

### Task 2: Pin the data repositories as submodules

**Files:**
- Create: `.gitmodules`
- Modify: `.github/dependabot.yml`
- Modify: `.env.example`
- Modify: `docs/architecture.md`

**Interfaces:**
- Produces: `vendor/crmedr`, `vendor/clbdr`, `vendor/texts` — the paths Task 3's bundle builder reads and derives commit SHAs from.

- [ ] **Step 1: Add the three submodules**

```bash
git submodule add https://github.com/CatholicOS/crmedr.git vendor/crmedr
git submodule add https://github.com/CatholicOS/clbdr.git vendor/clbdr
git submodule add https://github.com/CatholicOS/martyrology-texts.git vendor/texts
```

- [ ] **Step 2: Verify the URLs are HTTPS**

Run: `grep url .gitmodules`
Expected: three `https://github.com/CatholicOS/...` lines and **no** `git@github.com:` line. An SSH URL here breaks both `actions/checkout` and Dependabot; fix it with `git config -f .gitmodules submodule.<name>.url https://...` and re-run `git submodule sync` if any appear.

- [ ] **Step 3: Verify all three initialise**

Run: `git submodule status`
Expected: three lines, each with a commit SHA and no leading `-` (which would mean uninitialised).

- [ ] **Step 4: Add the Dependabot ecosystem**

Append to the `updates:` list in `.github/dependabot.yml`:

```yaml
  - package-ecosystem: "gitsubmodule"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
    cooldown:
      default-days: 7
    labels:
      - "dependencies"
      - "data"
```

The private `vendor/texts` submodule additionally needs `martyrology-texts` added
to the organisation's *Grant Dependabot access to private repositories* allowlist
(Organization Settings → Code security). If that setting is unavailable, use the
`registries:` fallback documented in spec §1.

- [ ] **Step 5: Add the production block to `.env.example`**

Append:

```bash
# --- Production (VPS) ---------------------------------------------------
# Written to /opt/martyrology/config/runtime.env by setup-vps-deploy-user.sh.
# All paths route through the `current` symlink, so they never change between
# releases. Secrets live in /etc/martyrology/api.env (root:root 0600) instead.
# MARTYROLOGY_PORT=8412
# MARTYROLOGY_MANIFEST_PATH=/opt/martyrology/current/manifest.json
# MARTYROLOGY_DATA_PATH=/opt/martyrology/current/data/editions:/opt/martyrology/current/data/texts
# MARTYROLOGY_CRMEDR_PATH=/opt/martyrology/current/data/crmedr
# MARTYROLOGY_CLBDR_PATH=/opt/martyrology/current/data/clbdr
```

- [ ] **Step 6: Repoint the architecture doc**

In `docs/architecture.md`, replace the numbered three-option deployment list
(the "Deployment options, in order of preference" block) with:

```markdown
The deployment architecture is specified in
[docs/superpowers/specs/2026-08-01-continuous-deployment-design.md](superpowers/specs/2026-08-01-continuous-deployment-design.md):
the three data trees are pinned as git submodules under `vendor/`, assembled by
CI into a release bundle with a manifest, and installed on the VPS by a deploy
script that smoke-checks before activating and rolls back on failure.
```

- [ ] **Step 7: Confirm the test suite is unaffected**

Run: `pytest -q`
Expected: PASS. Submodules under `vendor/` are not on any configured data path
(`testpaths = ["tests"]`, fixtures under `tests/fixtures`), so nothing changes.

- [ ] **Step 8: Commit**

```bash
git add .gitmodules vendor .github/dependabot.yml .env.example docs/architecture.md
git commit -S -m "Pin crmedr, clbdr and martyrology-texts as submodules"
```

---

### Task 3: Bundle builder

**Files:**
- Create: `scripts/deploy/build_bundle.py`
- Create: `tests/test_build_bundle.py`

**Interfaces:**
- Consumes: `martyrology_api.manifest.Manifest` and `BUNDLE_FORMAT` from Task 1; the `vendor/*` submodules from Task 2.
- Produces:
  - `sha256_file(path: Path) -> str`
  - `hash_tree(root: Path) -> dict[str, str]`
  - `build_manifest(staging: Path, api_version: str, api_commit: str, data_commits: dict[str, str]) -> dict`
  - `git_commit(repo: Path) -> str`
  - `assemble(staging: Path, out_dir: Path, version: str) -> Path` returning the tarball path
  - CLI: `python scripts/deploy/build_bundle.py --version <v> --staging <dir> --out <dir>`, used by Task 6's workflow.

Spec §8 asks for a CI job, gated on pull requests touching the bundle assembly,
that builds a bundle and asserts its tree shape and manifest schema. These tests
satisfy that requirement more broadly: they live in `pytest`, which already runs
on every pull request, so the check cannot be skipped by a path filter that
someone forgets to update. No separate job is added.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_bundle.py`:

```python
import importlib.util
import json
import tarfile
from pathlib import Path

from martyrology_api import manifest as runtime_manifest
from martyrology_api.manifest import Manifest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "build_bundle.py"
_spec = importlib.util.spec_from_file_location("build_bundle", _PATH)
assert _spec is not None and _spec.loader is not None
build_bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_bundle)

COMMITS = {"texts": "t" * 40, "crmedr": "c" * 40, "clbdr": "l" * 40}


def _staging(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    (root / "data" / "crmedr").mkdir(parents=True)
    (root / "wheels").mkdir()
    (root / "data" / "crmedr" / "ids.json").write_text("{}", encoding="utf-8")
    (root / "wheels" / "fake.whl").write_bytes(b"PK\x03\x04")
    return root


def test_sha256_file_matches_known_digest(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"abc")
    assert build_bundle.sha256_file(target) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_tree_uses_sorted_posix_relative_keys(tmp_path: Path):
    root = _staging(tmp_path)
    tree = build_bundle.hash_tree(root)
    assert list(tree) == sorted(tree)
    assert "data/crmedr/ids.json" in tree
    assert "wheels/fake.whl" in tree
    assert not any(key.startswith("/") for key in tree)


def test_build_manifest_records_format_and_commits(tmp_path: Path):
    manifest = build_bundle.build_manifest(_staging(tmp_path), "0.1.0", "a" * 40, COMMITS)
    assert manifest["bundle_format"] == 1
    assert manifest["api_commit"] == "a" * 40
    assert manifest["data"] == COMMITS


def test_build_manifest_validates_against_the_runtime_model(tmp_path: Path):
    """The writer and the reader must agree; this is the contract between
    scripts/deploy/build_bundle.py and src/martyrology_api/manifest.py."""
    manifest = build_bundle.build_manifest(_staging(tmp_path), "0.1.0", "a" * 40, COMMITS)
    parsed = Manifest.model_validate(manifest)
    assert parsed.api_version == "0.1.0"


def test_bundle_format_constants_agree():
    """BUNDLE_FORMAT is declared in both modules. If they ever drift, the
    deploy script's manifest check rejects every bundle CI produces, so
    pin them together here rather than discovering it on the VPS."""
    assert build_bundle.BUNDLE_FORMAT == runtime_manifest.BUNDLE_FORMAT


def test_assemble_writes_a_tarball_with_a_manifest_at_the_root(tmp_path: Path):
    root = _staging(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    tarball = build_bundle.assemble(root, out, "1.2.3")
    assert tarball.name == "martyrology-1.2.3-linux-x86_64-cp312.tar.gz"
    with tarfile.open(tarball) as archive:
        names = archive.getnames()
    assert "manifest.json" in names
    assert "data/crmedr/ids.json" in names


def test_assemble_manifest_does_not_hash_itself(tmp_path: Path):
    root = _staging(tmp_path)
    build_bundle.write_manifest(root, "0.1.0", "a" * 40, COMMITS)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert "manifest.json" not in manifest["files"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_build_bundle.py -v`
Expected: FAIL — `FileNotFoundError` for `scripts/deploy/build_bundle.py`.

- [ ] **Step 3: Implement the builder**

Create `scripts/deploy/build_bundle.py`:

```python
#!/usr/bin/env python3
"""Assemble a martyrology-api release bundle.

Takes a staging directory already populated with `wheels/` and `data/`,
writes `manifest.json` into it, and tars the result. Run by
.github/workflows/deploy.yml; the manifest it writes is read at runtime by
src/martyrology_api/manifest.py, and tests/test_build_bundle.py asserts the
two agree.
"""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

BUNDLE_FORMAT = 1
PYTHON_REQUIRES = ">=3.12"
BUNDLE_NAME = "martyrology-{version}-linux-x86_64-cp312.tar.gz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    """sha256 of every regular file under root, keyed by POSIX-style relative
    path and sorted so the manifest is byte-stable across runs."""
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def git_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_manifest(
    staging: Path, api_version: str, api_commit: str, data_commits: dict[str, str]
) -> dict:
    return {
        "bundle_format": BUNDLE_FORMAT,
        "api_version": api_version,
        "api_commit": api_commit,
        "data": data_commits,
        "python_requires": PYTHON_REQUIRES,
        "files": hash_tree(staging),
    }


def write_manifest(
    staging: Path, api_version: str, api_commit: str, data_commits: dict[str, str]
) -> Path:
    """Hash the staged tree, then write the manifest into it. Order matters:
    the manifest cannot contain its own digest, so it is built first."""
    manifest = build_manifest(staging, api_version, api_commit, data_commits)
    path = staging / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def assemble(staging: Path, out_dir: Path, version: str) -> Path:
    tarball = out_dir / BUNDLE_NAME.format(version=version)
    with tarfile.open(tarball, "w:gz") as archive:
        for path in sorted(staging.rglob("*")):
            archive.add(path, arcname=path.relative_to(staging).as_posix())
    return tarball


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--api-version", required=True)
    parser.add_argument("--repo-root", default=Path("."), type=Path)
    args = parser.parse_args()

    data_commits = {
        "texts": git_commit(args.repo_root / "vendor" / "texts"),
        "crmedr": git_commit(args.repo_root / "vendor" / "crmedr"),
        "clbdr": git_commit(args.repo_root / "vendor" / "clbdr"),
    }
    write_manifest(args.staging, args.api_version, git_commit(args.repo_root), data_commits)
    tarball = assemble(args.staging, args.out, args.version)
    print(tarball)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_build_bundle.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint**

Run: `ruff check src tests scripts && ruff format --check src tests scripts`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy/build_bundle.py tests/test_build_bundle.py
git commit -S -m "Add release bundle builder"
```

---

### Task 4: On-VPS deploy script

**Files:**
- Create: `scripts/deploy/deploy.sh`
- Create: `tests/test_deploy_script.py`

**Interfaces:**
- Consumes: the bundle naming and layout produced by Task 3; `/healthz` from Task 1.
- Produces: `bash deploy.sh [--dry-run] <version>`, invoked over ssh by Task 6 and installed to `/opt/martyrology/bin/deploy.sh` by Task 5. Honours `APP_DIR` (default `/opt/martyrology`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deploy_script.py`:

```python
import hashlib
import subprocess
import tarfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy" / "deploy.sh"


def _app_dir(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    (app / "incoming").mkdir(parents=True)
    (app / "releases").mkdir()
    return app


def _bundle(app: Path, version: str, *, arcname: str = "manifest.json") -> Path:
    payload = app / "incoming" / f"martyrology-{version}-linux-x86_64-cp312.tar.gz"
    source = app / "manifest.json"
    source.write_text("{}", encoding="utf-8")
    with tarfile.open(payload, "w:gz") as archive:
        archive.add(source, arcname=arcname)
    source.unlink()
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    (payload.parent / f"{payload.name}.sha256").write_text(
        f"{digest}  {payload.name}\n", encoding="utf-8"
    )
    return payload


def _run(app: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={"PATH": "/usr/bin:/bin", "APP_DIR": str(app)},
        capture_output=True,
        text=True,
    )


def test_rejects_a_missing_version(tmp_path: Path):
    result = _run(_app_dir(tmp_path))
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_rejects_a_shell_metacharacter_version(tmp_path: Path):
    result = _run(_app_dir(tmp_path), "--dry-run", "1.0.0; rm -rf /")
    assert result.returncode != 0
    assert "suspicious version" in result.stderr


def test_rejects_a_missing_bundle(tmp_path: Path):
    result = _run(_app_dir(tmp_path), "--dry-run", "9.9.9")
    assert result.returncode != 0
    assert "bundle not found" in result.stderr


def test_rejects_a_checksum_mismatch(tmp_path: Path):
    app = _app_dir(tmp_path)
    bundle = _bundle(app, "1.0.0")
    bundle.write_bytes(bundle.read_bytes() + b"tampered")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr


def test_rejects_a_path_traversal_member(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0", arcname="../escape.json")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode != 0
    assert "absolute or parent-relative paths" in result.stderr


def test_dry_run_accepts_a_good_bundle(tmp_path: Path):
    app = _app_dir(tmp_path)
    _bundle(app, "1.0.0")
    result = _run(app, "--dry-run", "1.0.0")
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert not (app / "releases" / "1.0.0").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_deploy_script.py -v`
Expected: FAIL — the script does not exist, so bash exits 127.

- [ ] **Step 3: Implement the script**

Create `scripts/deploy/deploy.sh`:

```bash
#!/usr/bin/env bash
#
# deploy.sh — install and activate a martyrology-api release bundle.
#
# Runs on the VPS as the martyrology-deploy user, invoked over ssh by
# .github/workflows/deploy.yml. Installed at $APP_DIR/bin/deploy.sh by
# scripts/deploy/setup-vps-deploy-user.sh; deliberately NOT refreshed from
# the bundle, so updating it stays an operator action.
#
# Nothing from the payload is ever executed. The bundle is checksum-verified
# and screened for path traversal before a single byte is extracted.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/martyrology}"
SERVICE="martyrology-api.service"
RUNTIME_ENV="$APP_DIR/config/runtime.env"
KEEP_RELEASES=5
HEALTH_TIMEOUT=30

die() {
    echo "ERROR: $*" >&2
    exit 1
}

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
    shift
fi

VERSION="${1:-}"
[ -n "$VERSION" ] || die "usage: deploy.sh [--dry-run] <version>"

# Anchored, no metacharacters: the version becomes part of a path and of a
# filename, so anything outside this shape is refused outright.
[[ "$VERSION" =~ ^v?[0-9]+(\.[0-9]+)*$ ]] || die "refusing suspicious version string: $VERSION"

BUNDLE="$APP_DIR/incoming/martyrology-${VERSION}-linux-x86_64-cp312.tar.gz"
[ -f "$BUNDLE" ] || die "bundle not found: $BUNDLE"
[ -f "$BUNDLE.sha256" ] || die "checksum not found: $BUNDLE.sha256"

(cd "$(dirname "$BUNDLE")" && sha256sum -c "$(basename "$BUNDLE").sha256" >/dev/null 2>&1) \
    || die "checksum mismatch for $BUNDLE"

if tar -tzf "$BUNDLE" | grep -Eq '^/|(^|/)\.\.(/|$)'; then
    die "bundle contains absolute or parent-relative paths"
fi

RELEASE="$APP_DIR/releases/$VERSION"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: $BUNDLE verified; would install to $RELEASE"
    exit 0
fi

echo "Installing $VERSION to $RELEASE"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
tar -xzf "$BUNDLE" -C "$RELEASE"

echo "Building venv (offline)"
python3.12 -m venv "$RELEASE/venv"
"$RELEASE/venv/bin/pip" install --quiet --upgrade pip
"$RELEASE/venv/bin/pip" install --quiet --no-index \
    --find-links "$RELEASE/wheels" martyrology-api

# Validate the manifest with the reader the app itself uses, so a bundle whose
# manifest this release cannot parse is rejected before it is ever activated.
"$RELEASE/venv/bin/python" - "$RELEASE/manifest.json" <<'PY' || die "manifest validation failed"
import sys
from pathlib import Path

from martyrology_api.manifest import load_manifest

if load_manifest(Path(sys.argv[1])) is None:
    sys.exit("manifest.json is absent, malformed, or an unsupported bundle_format")
PY

wait_healthy() {
    local port="$1"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

echo "Smoke-checking the new release before activating it"
SMOKE_PORT="$("$RELEASE/venv/bin/python" -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

MARTYROLOGY_MANIFEST_PATH="$RELEASE/manifest.json" \
MARTYROLOGY_DATA_PATH="$RELEASE/data/editions:$RELEASE/data/texts" \
MARTYROLOGY_CRMEDR_PATH="$RELEASE/data/crmedr" \
MARTYROLOGY_CLBDR_PATH="$RELEASE/data/clbdr" \
    "$RELEASE/venv/bin/uvicorn" martyrology_api.app:create_app --factory \
    --host 127.0.0.1 --port "$SMOKE_PORT" >"$RELEASE/smoke.log" 2>&1 &
SMOKE_PID=$!
trap 'kill "$SMOKE_PID" 2>/dev/null || true' EXIT

if ! wait_healthy "$SMOKE_PORT"; then
    kill "$SMOKE_PID" 2>/dev/null || true
    cat "$RELEASE/smoke.log" >&2
    die "smoke check failed; $VERSION was not activated"
fi

EDITIONS="$(curl -fsS "http://127.0.0.1:${SMOKE_PORT}/healthz" \
    | "$RELEASE/venv/bin/python" -c 'import json,sys; print(len(json.load(sys.stdin)["editions"]))')"
[ "$EDITIONS" -gt 0 ] || die "smoke check served zero editions; $VERSION was not activated"
echo "Smoke check passed: $EDITIONS editions"

kill "$SMOKE_PID" 2>/dev/null || true
trap - EXIT

PREVIOUS=""
if [ -L "$APP_DIR/current" ]; then
    PREVIOUS="$(readlink "$APP_DIR/current")"
fi

echo "Activating $VERSION"
ln -sfn "$RELEASE" "$APP_DIR/current.new"
mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"
sudo /usr/bin/systemctl restart "$SERVICE"

# shellcheck source=/dev/null
LIVE_PORT="$(. "$RUNTIME_ENV" && echo "$MARTYROLOGY_PORT")"

if ! wait_healthy "$LIVE_PORT"; then
    echo "ERROR: $VERSION is unhealthy on port $LIVE_PORT; rolling back" >&2
    if [ -n "$PREVIOUS" ]; then
        ln -sfn "$PREVIOUS" "$APP_DIR/current.new"
        mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"
        sudo /usr/bin/systemctl restart "$SERVICE"
        echo "Rolled back to $PREVIOUS" >&2
    else
        echo "No previous release to roll back to" >&2
    fi
    exit 1
fi

echo "$VERSION is live and healthy on port $LIVE_PORT"

rm -f "$BUNDLE" "$BUNDLE.sha256"
CURRENT_TARGET="$(readlink "$APP_DIR/current")"
# shellcheck disable=SC2012
ls -1dt "$APP_DIR"/releases/*/ 2>/dev/null | tail -n "+$((KEEP_RELEASES + 1))" | while read -r old; do
    [ "${old%/}" = "$CURRENT_TARGET" ] && continue
    echo "Pruning ${old%/}"
    rm -rf "${old%/}"
done
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_deploy_script.py -v`
Expected: PASS (6 passed). Only the pre-extraction rejection paths and
`--dry-run` are exercised; venv creation, systemd and sudo are not reachable in CI.

- [ ] **Step 5: Shellcheck the script**

Run: `shellcheck scripts/deploy/deploy.sh`
Expected: clean. Install with `sudo apt install shellcheck` if absent.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy/deploy.sh tests/test_deploy_script.py
git commit -S -m "Add on-VPS deploy script with smoke check and rollback"
```

---

### Task 5: VPS provisioning script

**Files:**
- Create: `scripts/deploy/setup-vps-deploy-user.sh`

**Interfaces:**
- Consumes: `scripts/deploy/deploy.sh` from Task 4 (installs it to `$APP_DIR/bin/`).
- Produces: the `martyrology-deploy` and `martyrology` accounts, `/opt/martyrology` with `config/runtime.env`, `/etc/martyrology/api.env`, `/etc/sudoers.d/martyrology-deploy`, and `martyrology-api.service` — the environment Task 6's workflow deploys into.

- [ ] **Step 1: Write the script**

Create `scripts/deploy/setup-vps-deploy-user.sh`:

```bash
#!/usr/bin/env bash
#
# setup-vps-deploy-user.sh — provision the VPS for martyrology-api deploys.
#
# Run ONCE on the VPS as root. Idempotent: re-runs are safe.
#
# Creates two identities with different jobs:
#   martyrology-deploy — the GitHub Actions identity. Owns $APP_DIR, has no
#     password and no sudo beyond two exact systemctl commands.
#   martyrology — the service account the unit runs as. Read-only on the
#     release tree, no login.
#
# Secrets live in /etc/martyrology/api.env (root:root 0600), which the deploy
# identity cannot read; systemd loads it as root before dropping privileges.
# Non-secret settings live in $APP_DIR/config/runtime.env, which the deploy
# script reads to learn the live port.

set -euo pipefail

DEPLOY_USER="martyrology-deploy"
SERVICE_USER="martyrology"
APP_DIR="/opt/martyrology"
SECRET_ENV="/etc/martyrology/api.env"
RUNTIME_ENV="$APP_DIR/config/runtime.env"
PORT="${MARTYROLOGY_PORT:-8412}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root (try: sudo $0)" >&2; exit 1; }
[ -f "$SCRIPT_DIR/deploy.sh" ] || { echo "ERROR: deploy.sh not beside this script" >&2; exit 1; }

for user in "$DEPLOY_USER" "$SERVICE_USER"; do
    if ! id -u "$user" >/dev/null 2>&1; then
        echo "Creating user: $user"
        useradd --create-home --shell /bin/bash "$user"
        passwd --lock "$user" >/dev/null
    else
        echo "User already exists: $user"
    fi
done

SSH_DIR="/home/$DEPLOY_USER/.ssh"
mkdir -p "$SSH_DIR"
touch "$SSH_DIR/authorized_keys"
chmod 700 "$SSH_DIR"
chmod 600 "$SSH_DIR/authorized_keys"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR"

mkdir -p "$APP_DIR"/{bin,config,incoming,releases}
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
chmod 755 "$APP_DIR"

install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 "$SCRIPT_DIR/deploy.sh" "$APP_DIR/bin/deploy.sh"

if [ ! -f "$RUNTIME_ENV" ]; then
    echo "Writing $RUNTIME_ENV"
    cat >"$RUNTIME_ENV" <<EOF
MARTYROLOGY_PORT=$PORT
MARTYROLOGY_MANIFEST_PATH=$APP_DIR/current/manifest.json
MARTYROLOGY_DATA_PATH=$APP_DIR/current/data/editions:$APP_DIR/current/data/texts
MARTYROLOGY_CRMEDR_PATH=$APP_DIR/current/data/crmedr
MARTYROLOGY_CLBDR_PATH=$APP_DIR/current/data/clbdr
EOF
    chown "$DEPLOY_USER:$DEPLOY_USER" "$RUNTIME_ENV"
    chmod 644 "$RUNTIME_ENV"
else
    echo "Keeping existing $RUNTIME_ENV"
fi

mkdir -p "$(dirname "$SECRET_ENV")"
if [ ! -f "$SECRET_ENV" ]; then
    echo "Writing $SECRET_ENV skeleton — fill these in before the first deploy"
    cat >"$SECRET_ENV" <<'EOF'
MARTYROLOGY_ZITADEL_ISSUER=
MARTYROLOGY_ZITADEL_CLIENT_ID=
MARTYROLOGY_ZITADEL_CLIENT_SECRET=
MARTYROLOGY_OPENFGA_API_URL=
MARTYROLOGY_OPENFGA_STORE_ID=
MARTYROLOGY_OPENFGA_MODEL_ID=
MARTYROLOGY_GITHUB_TOKEN=
EOF
else
    echo "Keeping existing $SECRET_ENV"
fi
chown root:root "$SECRET_ENV"
chmod 600 "$SECRET_ENV"

# Validate before installing: a malformed sudoers file breaks sudo host-wide.
SUDOERS_TMP="$(mktemp)"
cat >"$SUDOERS_TMP" <<EOF
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart martyrology-api.service, /usr/bin/systemctl is-active martyrology-api.service
EOF
visudo -cf "$SUDOERS_TMP" >/dev/null || { rm -f "$SUDOERS_TMP"; echo "ERROR: generated sudoers is invalid" >&2; exit 1; }
install -o root -g root -m 440 "$SUDOERS_TMP" /etc/sudoers.d/martyrology-deploy
rm -f "$SUDOERS_TMP"

cat >/etc/systemd/system/martyrology-api.service <<EOF
[Unit]
Description=Roman Martyrology API
After=network.target

[Service]
User=$SERVICE_USER
EnvironmentFile=$RUNTIME_ENV
EnvironmentFile=$SECRET_ENV
ExecStart=$APP_DIR/current/venv/bin/uvicorn martyrology_api.app:create_app --factory --host 127.0.0.1 --port \${MARTYROLOGY_PORT}
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable martyrology-api.service

cat <<EOF

✓ Provisioned $DEPLOY_USER (deploys) and $SERVICE_USER (runtime).
✓ $APP_DIR ready; deploy.sh installed at $APP_DIR/bin/deploy.sh.
✓ Unit enabled but NOT started — it needs a first release.

NEXT STEPS
──────────
1. Fill in the secrets in $SECRET_ENV.
2. Generate the deploy keypair on a workstation:
       ssh-keygen -t ed25519 -C "martyrology-api deploy" -f ./deploy-key
3. Append the PUBLIC half to $SSH_DIR/authorized_keys.
4. Capture the host key for pinning:
       ssh-keyscan -t ed25519,rsa <vps-hostname>
5. Confirm port $PORT is free:
       ss -ltnp | sort -t: -k2 -n
6. Add the nginx proxy directives for the domain in Plesk (spec §6).
7. In the martyrology-api repo settings:
       Secrets:   VPS_HOST, VPS_SSH_KEY (private half), VPS_USERNAME=$DEPLOY_USER,
                  SUBMODULE_TOKEN
       Variables: VPS_HOST_KEY (ssh-keyscan output), APP_DIR=$APP_DIR
8. Publish a GitHub release to trigger the first deploy.
EOF
```

- [ ] **Step 2: Check the syntax parses**

Run: `bash -n scripts/deploy/setup-vps-deploy-user.sh`
Expected: no output, exit 0.

- [ ] **Step 3: Shellcheck it**

Run: `shellcheck scripts/deploy/setup-vps-deploy-user.sh`
Expected: clean.

- [ ] **Step 4: Verify the non-root guard**

Run: `bash scripts/deploy/setup-vps-deploy-user.sh; echo "exit=$?"`
Expected: `ERROR: run as root (try: sudo ...)` and `exit=1`. This is the only
part of the script that is safe to exercise on a workstation — everything past
the guard mutates system state and belongs on the VPS.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy/setup-vps-deploy-user.sh
git commit -S -m "Add VPS provisioning script for martyrology-api deploys"
```

---

### Task 6: Release workflow and shellcheck CI

**Files:**
- Create: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `scripts/deploy/build_bundle.py` (Task 3) and `$APP_DIR/bin/deploy.sh` (Tasks 4–5).
- Produces: the deployment pipeline itself. No downstream consumers.

- [ ] **Step 1: Track the lockfile**

`uv.lock` currently exists on disk but is untracked (and is *not* gitignored).
The workflow's `uv export` step needs it committed, or the release build resolves
dependencies afresh and the "frozen to the release" guarantee is only as good as
whatever PyPI served that minute.

```bash
git add uv.lock
git commit -S -m "Track uv.lock so release builds resolve deterministically"
```

Run: `git ls-files uv.lock`
Expected: `uv.lock`

- [ ] **Step 2: Add the shellcheck job to CI**

Append to `jobs:` in `.github/workflows/ci.yml`:

```yaml
  shellcheck:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false

      - name: shellcheck
        run: shellcheck scripts/deploy/*.sh
```

Submodules are deliberately not checked out here — the job only lints shell
scripts, and skipping them keeps CI runnable from forks with no access to
`martyrology-texts`.

- [ ] **Step 3: Verify the CI change is valid YAML**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output, exit 0. (Install PyYAML in the venv if missing:
`pip install pyyaml`.)

- [ ] **Step 4: Write the deploy workflow**

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

# Builds a release bundle (api wheel + offline wheelhouse + the three pinned
# data trees + manifest.json), ships it to the VPS, and activates it.
#
# All ${{ ... }} interpolations are repo secrets/vars (trusted). No untrusted
# github.event.* field is used.
#
# See docs/superpowers/specs/2026-08-01-continuous-deployment-design.md

on:
  release:
    types: [published]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: deploy-martyrology
  cancel-in-progress: false

jobs:
  deploy:
    # Pinned, never ubuntu-latest: the VPS is Ubuntu 24.04 / glibc 2.39 and the
    # wheelhouse ABI must match it.
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
        with:
          submodules: recursive
          token: ${{ secrets.SUBMODULE_TOKEN }}
          persist-credentials: false

      - uses: actions/setup-python@v6.3.0
        with:
          python-version: "3.12"

      - name: Resolve version
        id: version
        run: |
          VERSION="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
          echo "value=$VERSION" >> "$GITHUB_OUTPUT"
          echo "Building martyrology-api $VERSION"

      - name: Build wheel and offline wheelhouse
        run: |
          pip install uv
          mkdir -p staging/wheels
          uv build --wheel --out-dir dist
          uv export --no-dev --no-emit-project --format requirements-txt -o requirements.txt
          pip wheel -r requirements.txt -w staging/wheels
          cp dist/*.whl staging/wheels/

      - name: Stage data trees
        run: |
          mkdir -p staging/data
          cp -a data/editions staging/data/editions
          cp -a vendor/texts staging/data/texts
          cp -a vendor/crmedr staging/data/crmedr
          cp -a vendor/clbdr staging/data/clbdr
          rm -rf staging/data/*/.git

      - name: Assemble bundle
        id: bundle
        env:
          VERSION: ${{ steps.version.outputs.value }}
        run: |
          mkdir -p out
          BUNDLE="$(python scripts/deploy/build_bundle.py \
            --version "$VERSION" --api-version "$VERSION" \
            --staging staging --out out --repo-root .)"
          NAME="$(basename "$BUNDLE")"
          # Generated from inside out/ so the checksum file names the bundle
          # bare; deploy.sh runs `sha256sum -c` from the incoming/ directory.
          (cd out && sha256sum "$NAME" > "$NAME.sha256")
          echo "path=$BUNDLE" >> "$GITHUB_OUTPUT"
          ls -la out

      - name: Setup SSH
        env:
          VPS_SSH_KEY: ${{ secrets.VPS_SSH_KEY }}
          VPS_HOST_KEY: ${{ vars.VPS_HOST_KEY }}
        run: |
          if [ -z "$VPS_SSH_KEY" ] || [ -z "$VPS_HOST_KEY" ]; then
            echo "ERROR: secrets.VPS_SSH_KEY or vars.VPS_HOST_KEY is empty."
            exit 1
          fi
          mkdir -p ~/.ssh
          chmod 700 ~/.ssh
          echo "$VPS_SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          echo "$VPS_HOST_KEY" > ~/.ssh/known_hosts
          chmod 644 ~/.ssh/known_hosts

      - name: Verify the pinned host key covers the target
        env:
          VPS_HOST: ${{ secrets.VPS_HOST }}
        run: |
          if ! ssh-keygen -F "$VPS_HOST" -f ~/.ssh/known_hosts >/dev/null; then
            echo "ERROR: vars.VPS_HOST_KEY has no key for $VPS_HOST."
            exit 1
          fi

      - name: Sanity-check the pinned key against DNS SSHFP records
        continue-on-error: true
        env:
          VPS_HOST: ${{ secrets.VPS_HOST }}
          VPS_HOST_KEY: ${{ vars.VPS_HOST_KEY }}
        run: |
          # Non-fatal drift detector, mirroring cdcf-website's deploy workflow.
          # The pinned key is the trust anchor; DNS is only corroboration, so a
          # mismatch warns rather than blocks (DNS may simply lag a rotation).
          PIN_FPS=$(printf '%s\n' "$VPS_HOST_KEY" \
            | awk '$1 ~ /^(ssh-|ecdsa-)/ || $2 ~ /^(ssh-|ecdsa-)/' \
            | ssh-keygen -l -f - 2>/dev/null \
            | awk '{print $2}' | sed 's/^SHA256://' | sort -u)
          if [ -z "$PIN_FPS" ]; then
            echo "::warning::Could not derive fingerprints from VPS_HOST_KEY; skipping drift check."
            exit 0
          fi
          DNS_FPS=$(dig +short SSHFP "$VPS_HOST" 2>/dev/null | awk '{print toupper($3)}' | sort -u)
          if [ -z "$DNS_FPS" ]; then
            echo "::warning::No SSHFP records published for $VPS_HOST; skipping drift check."
            exit 0
          fi
          for fp in $PIN_FPS; do
            echo "$DNS_FPS" | grep -qi "$fp" \
              || echo "::warning::Pinned key $fp not advertised in SSHFP for $VPS_HOST. Either DNS lags reality or VPS_HOST_KEY is stale."
          done

      - name: Upload bundle
        env:
          VPS_USERNAME: ${{ secrets.VPS_USERNAME }}
          VPS_HOST: ${{ secrets.VPS_HOST }}
          APP_DIR: ${{ vars.APP_DIR }}
          BUNDLE: ${{ steps.bundle.outputs.path }}
        run: |
          if [ -z "$VPS_USERNAME" ] || [ -z "$VPS_HOST" ] || [ -z "$APP_DIR" ]; then
            echo "ERROR: VPS_USERNAME / VPS_HOST / APP_DIR is empty."
            exit 1
          fi
          for attempt in 1 2 3; do
            echo "Upload attempt $attempt..."
            if scp -i ~/.ssh/deploy_key \
              -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
              "$BUNDLE" "$BUNDLE.sha256" \
              "${VPS_USERNAME}@${VPS_HOST}:${APP_DIR}/incoming/"; then
              echo "Upload succeeded on attempt $attempt"
              exit 0
            fi
            [ "$attempt" -lt 3 ] && echo "Retrying in 15s..." && sleep 15
          done
          echo "All upload attempts failed"
          exit 1

      - name: Activate release
        env:
          VPS_USERNAME: ${{ secrets.VPS_USERNAME }}
          VPS_HOST: ${{ secrets.VPS_HOST }}
          APP_DIR: ${{ vars.APP_DIR }}
          VERSION: ${{ steps.version.outputs.value }}
        run: |
          ssh -i ~/.ssh/deploy_key \
            -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
            "${VPS_USERNAME}@${VPS_HOST}" \
            "bash ${APP_DIR}/bin/deploy.sh ${VERSION}"

      - name: Attach manifest to the release
        if: github.event_name == 'release'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAG: ${{ github.event.release.tag_name }}
        run: gh release upload "$TAG" staging/manifest.json --clobber
```

The activation step is intentionally not retried: `deploy.sh` rolls back on
failure, so a retry would reinstall a release already judged unhealthy.

- [ ] **Step 5: Validate the workflow YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))"`
Expected: no output, exit 0.

- [ ] **Step 6: Run the full suite one more time**

Run: `pytest -q --cov --cov-branch --cov-report=term-missing && ruff check src tests scripts && ruff format --check src tests scripts && pyright`
Expected: all pass, coverage at or above 90.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/deploy.yml .github/workflows/ci.yml
git commit -S -m "Add release deploy workflow and shellcheck CI job"
```

---

## Post-implementation: operator steps

These are not code and cannot be done by the implementing engineer. They belong
to whoever administers the VPS and the GitHub organisation, and are listed in
spec §10:

1. `sudo apt install python3.12-venv shellcheck` on the VPS.
2. Run `sudo bash scripts/deploy/setup-vps-deploy-user.sh` on the VPS and follow
   its printed next steps.
3. Add `martyrology-texts` to the organisation's Dependabot private-repository
   allowlist.
4. Add the nginx directives in Plesk (spec §6) after confirming port 8412 is free.
5. Publish a release to trigger the first deploy.
