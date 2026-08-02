# Continuous Deployment Design

**Date:** 2026-08-01
**Status:** Approved, pending implementation
**Supersedes:** the three-option deployment list in `docs/architecture.md`

## Problem

`martyrology-api` is a public repository that must serve texts it cannot contain.
The copyrighted 2004-family eulogies live in the private `CatholicOS/martyrology-texts`
repository; the canonical-ID registries (`crmedr`, `clbdr`) live in separate public
repositories. The API resolves all three from filesystem paths at startup
(`create_app()` → `Registry.load(crmedr_path, clbdr_path)` and
`Store(data_path_list, registry)` in `src/martyrology_api/app.py:24-27`).

Deployment must therefore assemble code and three external data trees into one
running service, on a VPS where:

- most services are managed by Plesk, which has no ASGI support;
- the existing GitHub Actions deploy identity is a Plesk-chrooted user with
  scp access but severely limited command execution;
- the async capabilities of ASGI must be preserved (no WSGI shim, no Passenger).

## Constraints and decisions taken as given

These were settled during design and are not revisited here:

1. **The served corpus is frozen to the release artifact.** A curation PR merged
   on `martyrology-texts` does not reach the live API until a release is cut.
   This is a deliberate trade of immediacy for reproducibility and auditability.
   The mitigation is to make releases cheap and automatic, not to loosen the freeze.
2. **The host's Python is a stable, pinnable 3.12+.** Ubuntu 24.04 LTS ships
   Python 3.12 as the system interpreter. A venv on the host is therefore safe,
   which removes the principal argument for shipping a container runtime.
3. **The runtime substrate is a plain systemd unit**, not Docker. Given (2), a
   container would buy isolation that is not needed while adding a registry
   credential on the VPS — a second path by which the private corpus could be
   pulled.

## Verified environment facts

| Fact | Value |
|---|---|
| VPS OS | Ubuntu 24.04.4 LTS (noble) |
| VPS glibc | 2.39-0ubuntu8.8 |
| Matching runner | `ubuntu-24.04` (pinned explicitly, **not** `ubuntu-latest`) |
| Web front end | Plesk-managed nginx, reverse proxy via "Additional nginx directives" |
| TLS | Owned by Plesk (Let's Encrypt renewal stays automatic) |

`ubuntu-latest` will eventually roll to 26.04 and silently break the ABI match
between the CI-built wheelhouse and the host. The pin is load-bearing.

---

## 1. Data pinning: git submodules

Three submodules are added to this repository:

| Path | Repository | Visibility |
|---|---|---|
| `vendor/crmedr` | `CatholicOS/crmedr` | public |
| `vendor/clbdr` | `CatholicOS/clbdr` | public |
| `vendor/texts` | `CatholicOS/martyrology-texts` | **private** |

**All three `.gitmodules` URLs must be HTTPS, never SSH.** `actions/checkout`
authenticates submodules by injecting a token as an HTTP extraheader; an
`git@github.com:` URL breaks both the release workflow and Dependabot.

### Why submodules rather than a pins file

The pin is a reviewable commit SHA tracked by git, and Dependabot has a
first-class `gitsubmodule` ecosystem. A data update therefore becomes an
automatic pull request on the existing schedule, auto-merged by the existing
`.github/workflows/dependabot-automerge.yml`. No pin-parsing code and no
`repository_dispatch` plumbing are required in the baseline.

This is deployment option 2 already sanctioned in `docs/architecture.md`,
repurposed for *pinning* rather than for attachment.

### Dependabot access to the private submodule

Primary: the organisation-level **Grant Dependabot access to private
repositories** allowlist (Organization Settings → Code security), with
`martyrology-texts` added.

Fallback, if the org setting is unavailable: a Dependabot secret plus a `git`
registry in `.github/dependabot.yml`:

```yaml
registries:
  martyrology-texts:
    type: git
    url: https://github.com/CatholicOS/martyrology-texts.git
    username: x-access-token
    password: ${{ secrets.SUBMODULE_PAT }}

updates:
  - package-ecosystem: "gitsubmodule"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
    labels: ["dependencies", "data"]
    registries:
      - martyrology-texts
```

Two known behaviours to accept: the submodule updater tracks the default-branch
tip rather than tags, and it does not group — so expect up to three separate
pull requests per cycle.

### Effect on local development

None. `.env` keeps `MARTYROLOGY_CRMEDR_PATH=../crmedr` and
`MARTYROLOGY_CLBDR_PATH=../clbdr` pointing at sibling checkouts. The submodules
are what CI reads. A clone without access to `martyrology-texts` fails to
initialise `vendor/texts` only; `git submodule update --init vendor/crmedr
vendor/clbdr` succeeds for everyone, and the graceful-degradation guarantee
(architecture.md principle 1) already covers the absent corpus.

## 2. The release bundle

Artifact name: `martyrology-<version>-linux-x86_64-cp312.tar.gz`

```
manifest.json
wheels/            martyrology_api-<version>-py3-none-any.whl
                   + every resolved runtime dependency as a wheel
data/editions/     from this repo (public-domain editions)
data/texts/        from vendor/texts
data/crmedr/       from vendor/crmedr
data/clbdr/        from vendor/clbdr
```

`manifest.json` records:

```json
{
  "bundle_format": 1,
  "api_version": "0.1.0",
  "api_commit": "<sha>",
  "data": { "texts": "<sha>", "crmedr": "<sha>", "clbdr": "<sha>" },
  "python_requires": ">=3.12",
  "files": { "<relative path>": "<sha256>" }
}
```

This manifest is the auditable record of exactly which corpus is live — the
concrete artifact of decision (1) above.

**The bundle deliberately excludes `.env`.** Zitadel, OpenFGA and
`MARTYROLOGY_GITHUB_TOKEN` secrets live in root-owned `/etc/martyrology/api.env`,
loaded by the systemd unit's `EnvironmentFile=`. They are never shipped and
never overwritten by a deploy. A deploy changes code and data only, and a leaked
bundle contains no credentials.

## 3. Release workflow

`.github/workflows/deploy.yml`, modelled on `cdcf-website/.github/workflows/deploy.yml`.

```yaml
on:
  release:
    types: [published]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: deploy-martyrology
  cancel-in-progress: false
```

### Repository settings

| Kind | Name | Value |
|---|---|---|
| Secret | `VPS_HOST` | VPS hostname |
| Secret | `VPS_SSH_KEY` | private half of the deploy keypair |
| Secret | `VPS_USERNAME` | `martyrology-deploy` |
| Secret | `SUBMODULE_TOKEN` | fine-grained PAT, read-only on `martyrology-texts` |
| Variable | `VPS_HOST_KEY` | output of `ssh-keyscan -t ed25519,rsa <host>` |
| Variable | `APP_DIR` | `/opt/martyrology` |

`SUBMODULE_TOKEN` is unavoidable: the workflow's `GITHUB_TOKEN` cannot read a
different private repository.

### Steps

1. `actions/checkout` with `submodules: recursive` and `token: ${{ secrets.SUBMODULE_TOKEN }}`.
2. `uv build` → api wheel. `uv export --no-dev --format requirements-txt` → hash-pinned
   requirements → `pip wheel -r requirements.txt -w wheels/`, on `ubuntu-24.04` / cp312.
3. Assemble the tree, write `manifest.json`, `tar czf`, `sha256sum`.
4. Set up SSH exactly as the existing workflows do: fail fast if any secret or
   variable is empty; write `VPS_SSH_KEY` to `~/.ssh/deploy_key` mode 0600; write
   the pinned `VPS_HOST_KEY` to `~/.ssh/known_hosts`; run the DNS SSHFP drift check.
5. `scp` the bundle and its `.sha256` to `${APP_DIR}/incoming/`.
6. `ssh … "APP_DIR=${APP_DIR} bash ${APP_DIR}/bin/deploy.sh <version>"` — `APP_DIR`
   is exported explicitly, because `deploy.sh` otherwise falls back to its own
   `/opt/martyrology` default and would look for the bundle somewhere other than
   where step 5 put it.

Each network step is wrapped in the established 3-attempt / 15-second retry loop.

Step 6 is synchronous: the deploy script's stdout lands in the Actions log and
its exit code decides the build. There are no blind deploys and no status-file
handshake.

## 4. VPS provisioning and privilege boundaries

`scripts/setup-vps-deploy-user.sh`, modelled on
`cdcf-infra/scripts/setup-vps-sync-user.sh` — run once as root, idempotent.

### Two distinct identities

| User | Role | Rights |
|---|---|---|
| `martyrology-deploy` | GitHub Actions deploy identity | owns `/opt/martyrology`; supplementary member of group `martyrology`; no password; no sudo except the two rules below |
| `martyrology` | systemd service account | primary group `martyrology`; read-only on the release tree via that group; no login |

The two accounts share exactly one thing: the `martyrology` group. That is what
grants the service account read access to a tree owned by the deploy user, and
it is the reason the tree does not need — and must not have — world bits. The
release tree contains the licensed `martyrology-texts` corpus, and this is a
Plesk-managed host on which every other hosted subscription runs its own
non-chrooted uid; a world-readable `data/texts/` would publish the corpus to
every one of them, defeating the private-submodule architecture entirely.

The Plesk-chrooted subscription user is **not** used. A dedicated non-chrooted
user (the `cdcfinfra-deploy` pattern) can execute one command over ssh, which
removes the need for a trigger file, a systemd `.path` watcher, a root-run
oneshot parsing attacker-influenced filenames, and a status-file handshake back
to CI. It also keeps the application out of `/var/www/vhosts/<domain>/`, where
Plesk may rearrange things underneath it.

### Directory layout

Everything under `/opt/martyrology` is owned `martyrology-deploy:martyrology`,
and no path anywhere in it carries an "other" bit.

```
/opt/martyrology/                  martyrology-deploy:martyrology  0750
  bin/                             0750
  bin/deploy.sh                    0750, installed by the setup script
  config/                          0750
  config/runtime.env               0640, non-secret settings
  incoming/                        0700 — scp target; the bundle *contains*
                                   the corpus, and only the deploy user needs
                                   it, so the group is excluded here too
  releases/                        2750 — setgid, so release directories
                                   created by deploy.sh inherit the
                                   martyrology group
  releases/<version>/{venv,data,manifest.json}
                                   u=rwX, g=rX, o= (deploy.sh normalises the
                                   whole tree after extraction and then
                                   asserts it)
  current -> releases/<version>
/etc/martyrology/api.env           root:root 0600, secrets only
```

Both scripts assert this rather than assume it. `deploy.sh` walks the freshly
extracted release and fails if any entry is not group-readable, is not owned by
the `martyrology` group, or has any "other" bit set. `setup-vps-deploy-user.sh`
impersonates the service account to prove it can traverse `$APP_DIR` and
`releases/` and read `runtime.env`, then separately proves that nothing under
`$APP_DIR` is other-accessible and that the service account cannot read
`incoming/`. The two halves fail independently: a world-readable tree passes the
first and fails the second.

### Two environment files, split by secrecy

systemd accepts multiple `EnvironmentFile=` lines, so the service's configuration
is split by who is allowed to read it:

- **`/etc/martyrology/api.env`** — `root:root 0600`. Zitadel, OpenFGA and
  `MARTYROLOGY_GITHUB_TOKEN`. systemd reads `EnvironmentFile=` as root before
  dropping privileges, so the service gets its secrets while `martyrology-deploy`
  cannot read them. This mirrors step 4 of `setup-vps-sync-user.sh`, which
  restores `ubuntu` ownership and mode 0600 on `.env.production` after the
  recursive chown.
- **`/opt/martyrology/config/runtime.env`** — `martyrology-deploy:martyrology`,
  0640. `MARTYROLOGY_PORT`, `MARTYROLOGY_MANIFEST_PATH` and the three data paths. All
  point through the stable `current` symlink, so this file is written once at
  provisioning and never changes.

The split is load-bearing, not cosmetic: `deploy.sh` must know the live port to
poll `/healthz` after restarting (§5.8), and it must not be able to read secrets
to do so.

### Sudoers drop-in

`/etc/sudoers.d/martyrology-deploy`:

```
martyrology-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart martyrology-api.service, \
                                        /usr/bin/systemctl is-active martyrology-api.service
```

Two exact commands. No wildcards. If the deploy key leaks, the blast radius is
"replace the application code and bounce the service" — definitionally what a
deploy key can do. It cannot read secrets, touch other units, or escalate.

### The deploy script is not self-updating

`/opt/martyrology/bin/deploy.sh` is versioned in this repository and installed by
the setup script. It is deliberately **not** refreshed from the bundle: updating
it is an operator-initiated action, on the same reasoning as the `sync-to-vps.yml`
header comment about keeping script changes out of the automatic path.

## 5. Deploy script algorithm

`bash ${APP_DIR}/bin/deploy.sh <version>`, running as `martyrology-deploy`:

1. Reject a `<version>` that does not match `^v?[0-9]+(\.[0-9]+)*$`.
2. Verify the bundle against its `.sha256`; abort on mismatch.
3. Extract to a temporary directory, refusing any member with an absolute path
   or a `..` component.
4. Validate `manifest.json` against the expected schema and `bundle_format`.
5. `python3.12 -m venv releases/<version>/venv`, then
   `pip install --no-index --find-links wheels …`. Fully offline — a GitHub or
   PyPI outage cannot break a deploy.
   Then `chgrp -R martyrology` and `chmod -R u+rwX,g+rX,o-rwx` the release tree,
   and assert the result (see §4) — CI tar member modes and the host umask are
   not to be trusted in either direction.
6. **Smoke check before committing:** start the new release on a random free
   loopback port, assert `/healthz` returns 200 with the expected edition set,
   then kill it.
7. Flip `current` to the new release; `sudo systemctl restart martyrology-api.service`.
   systemd resolves the symlink at exec time, so a restart alone picks up the
   new release.
8. Poll the live port until `/healthz` is 200. If it is not healthy within the
   timeout, **flip `current` back, restart, and exit non-zero** — automatic
   rollback, with a red build.
9. Prune to the five most recent releases; remove the consumed bundle from
   `incoming/`.

Steps 1–4 are the reason nothing from the payload is ever executed except a
fixed, known entrypoint, and only after its hash matches.

## 6. systemd unit and Plesk

`/etc/systemd/system/martyrology-api.service`:

```ini
[Service]
User=martyrology
EnvironmentFile=/opt/martyrology/config/runtime.env
EnvironmentFile=/etc/martyrology/api.env
ExecStart=/opt/martyrology/current/venv/bin/uvicorn \
    martyrology_api.app:create_app --factory --host 127.0.0.1 --port ${MARTYROLOGY_PORT}
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true
```

### Port selection

The port must be free and stable. Plesk itself occupies 8443, 8880 and 8447;
Linux's default ephemeral range starts at 32768, so any stable port below that is
safe from ephemeral collision. **Default choice: 8412.** Verify before committing
to it:

```bash
ss -ltnp | sort -t: -k2 -n
```

The port appears in exactly two places — `MARTYROLOGY_PORT` in
`/opt/martyrology/config/runtime.env`, and the nginx directive below. That
coupling is manual and must be kept in sync by hand; it is recorded in the runbook.

### Plesk nginx directives

Domain → Apache & nginx Settings → Additional nginx directives:

```nginx
location / {
    proxy_pass http://127.0.0.1:8412;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Proxy-mode-to-Apache is disabled for the domain.

WebSocket upgrade headers are deliberately omitted. The usual
`proxy_set_header Connection $connection_upgrade` depends on a `map` block, which
is only legal at `http` level — Plesk injects these directives at `server` level,
so including it would make nginx reject the configuration. The API does not stream
today; if it ever does, the `map` goes in an http-level Plesk configuration
include (`/etc/nginx/conf.d/`), not in this field.

Plesk retains ownership of TLS and Let's Encrypt renewal, which is the actual
argument for staying inside Plesk rather than bypassing it. Nothing in Plesk
knows the application is Python, and uvicorn keeps its own event loop, so ASGI
async behaviour is fully preserved.

## 7. Changes to this repository

- **`GET /healthz`** (new, the only new endpoint) returning
  `{status, version, data: {crmedr, clbdr, texts}, editions: [...]}`, read from
  `manifest.json` via a new `MARTYROLOGY_MANIFEST_PATH` setting. The manifest is
  absent in development, where the data fields degrade to `null`. Required by the
  smoke check (§5.6), the rollback poll (§5.8), and by anyone asking which corpus
  is live. The service document at `app.py:47` is unchanged.
- **`scripts/setup-vps-deploy-user.sh`** (new), per §4.
- **`scripts/deploy/deploy.sh`** (new) — the source of truth for what the setup
  script installs at `/opt/martyrology/bin/deploy.sh`.
- **`.github/workflows/deploy.yml`** (new), per §3.
- **`.github/dependabot.yml`** — add the `gitsubmodule` ecosystem.
- **`.gitmodules`** (new) — three HTTPS submodule URLs.
- **`.env.example`** — a commented production block.
- **`docs/architecture.md`** — replace the three-option deployment list with a
  pointer to this spec.

## 8. Testing

- Unit tests for manifest parsing and for `/healthz` degradation when the
  manifest is absent.
- A CI job, triggered on pull requests touching `deploy.yml` or the bundle
  assembly, that builds the bundle and asserts its tree shape and manifest schema.
- `--dry-run` support in `deploy.sh`, plus a `shellcheck` job covering both shell
  scripts.

## 9. Out of scope

No blue/green or zero-downtime deployment — a restart behind nginx is a
sub-second gap. No database. No in-process data reload endpoint. No multi-host
deployment. No image registry. No staging environment (GitHub Actions
Environments can scope `APP_DIR` and the port per environment later, if wanted).

## 10. One-time operator runbook

1. `sudo apt install python3.12-venv` on the VPS.
2. Clone this repo somewhere and run `sudo bash scripts/setup-vps-deploy-user.sh`.
3. Generate the deploy keypair: `ssh-keygen -t ed25519 -C "martyrology-api deploy" -f ./deploy-key`.
4. Append the public half to `/home/martyrology-deploy/.ssh/authorized_keys`.
5. Capture the host key: `ssh-keyscan -t ed25519,rsa <host>`.
6. Populate `/etc/martyrology/api.env` with the Zitadel, OpenFGA and GitHub-token
   secrets. The setup script has already written `/opt/martyrology/config/runtime.env`
   with `MARTYROLOGY_PORT`, `MARTYROLOGY_MANIFEST_PATH` and the three data paths
   under `/opt/martyrology/current/data/`.
7. Choose and verify the port with `ss -ltnp`; add the nginx directives in Plesk.
8. Set the repository secrets and variables listed in §3.
9. Add `martyrology-texts` to the organisation's Dependabot private-repository
   allowlist.
10. Publish a release to trigger the first deploy.

## Appendix: questions resolved during design

| Question | Resolution |
|---|---|
| Can the deploy identity execute commands, or only scp? | Resolved by using a dedicated non-chrooted user instead of the Plesk one; commands run, so the watcher design was dropped entirely. |
| Can Dependabot read a private same-org submodule? | Yes — org-level allowlist, or a Dependabot `git` registry secret as fallback. Requires HTTPS submodule URLs. |
| Does the runner's ABI match the VPS? | Yes — both Ubuntu 24.04 / glibc 2.39, with `ubuntu-24.04` pinned explicitly. |
| Can Plesk proxy to a long-lived ASGI process? | Yes, via Additional nginx directives to a loopback port. |
