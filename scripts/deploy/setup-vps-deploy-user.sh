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
# The two identities meet in one place only: the martyrology group. The deploy
# user is added to it, the tree is owned martyrology-deploy:martyrology, and
# every "other" bit is stripped. That is deliberate and load-bearing — the
# release tree contains the licensed martyrology-texts corpus, and this host is
# Plesk-managed, so every other hosted subscription runs its own non-chrooted
# uid on the same box. A world-readable release tree would hand that corpus to
# all of them. Group bits let the service account in; nothing else gets in.
#
# Secrets live in /etc/martyrology/api.env (root:root 0600), which the deploy
# identity cannot read; systemd loads it as root before dropping privileges.
# Non-secret settings live in $APP_DIR/config/runtime.env, which the deploy
# script reads to learn the live port.

set -euo pipefail

DEPLOY_USER="martyrology-deploy"
SERVICE_USER="martyrology"
# The service account's own group; both identities share it. Kept as its own
# variable because deploy.sh chgrp's each release tree to exactly this name.
SERVICE_GROUP="martyrology"
APP_DIR="/opt/martyrology"
SECRET_ENV="/etc/martyrology/api.env"
RUNTIME_ENV="$APP_DIR/config/runtime.env"
PORT="${MARTYROLOGY_PORT:-8412}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root (try: sudo $0)" >&2; exit 1; }
[ -f "$SCRIPT_DIR/deploy.sh" ] || { echo "ERROR: deploy.sh not beside this script" >&2; exit 1; }

# deploy.sh needs these at deploy time; fail loudly now rather than mid-deploy.
for cmd in python3.12 curl tar sha256sum; do
    command -v "$cmd" >/dev/null || { echo "ERROR: required command not found: $cmd" >&2; exit 1; }
done
python3.12 -m venv --help >/dev/null 2>&1 \
    || { echo "ERROR: python3.12-venv is not installed (apt install python3.12-venv)" >&2; exit 1; }

# The sudoers drop-in below is useless if the host's sudoers doesn't pull in
# /etc/sudoers.d — catch that now instead of at first deploy.
grep -qE '^[#@]includedir[[:space:]]+/etc/sudoers\.d' /etc/sudoers \
    || { echo "ERROR: /etc/sudoers has no includedir for /etc/sudoers.d" >&2; exit 1; }

if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
    echo "Creating user: $DEPLOY_USER"
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
    passwd --lock "$DEPLOY_USER" >/dev/null
else
    echo "User already exists: $DEPLOY_USER"
fi

# The shared group has to exist before the service account is created, so the
# account can be pinned to it explicitly rather than relying on useradd's
# distro-dependent USERGROUPS_ENAB default to conjure one of the same name.
if ! getent group "$SERVICE_GROUP" >/dev/null; then
    echo "Creating group: $SERVICE_GROUP"
    groupadd --system "$SERVICE_GROUP"
else
    echo "Group already exists: $SERVICE_GROUP"
fi

# No login: the service account only ever runs the unit, never a shell.
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "Creating user: $SERVICE_USER"
    useradd --system --gid "$SERVICE_GROUP" --shell /usr/sbin/nologin \
        --no-create-home "$SERVICE_USER"
    passwd --lock "$SERVICE_USER" >/dev/null
else
    echo "User already exists: $SERVICE_USER"
fi

# The single point of contact between the two identities. Supplementary group
# membership is read at session setup, so an ssh session the deploy user
# already holds will not see it — irrelevant here because deploys open a fresh
# session, but the reason a re-run is safe rather than merely idempotent.
if ! id -nG "$DEPLOY_USER" | tr ' ' '\n' | grep -qx "$SERVICE_GROUP"; then
    echo "Adding $DEPLOY_USER to group $SERVICE_GROUP"
    usermod -aG "$SERVICE_GROUP" "$DEPLOY_USER"
else
    echo "$DEPLOY_USER is already in group $SERVICE_GROUP"
fi

SSH_DIR="/home/$DEPLOY_USER/.ssh"
mkdir -p "$SSH_DIR"
touch "$SSH_DIR/authorized_keys"
chmod 700 "$SSH_DIR"
chmod 600 "$SSH_DIR/authorized_keys"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR"

mkdir -p "$APP_DIR"/{bin,config,incoming,releases}
chown -R "$DEPLOY_USER:$SERVICE_GROUP" "$APP_DIR"

# Recursive first, then the per-directory modes below, so this cannot undo
# them. On a re-run over a tree provisioned by an earlier version of this
# script, this is what actually retracts the world bits from release trees that
# were previously chmod'ed a+rX — the exposure does not fix itself just because
# new releases are written correctly. Symlinks are skipped by chmod -R, which
# is correct: on Linux a symlink's own mode is inert.
chmod -R u+rwX,g+rX,o-rwx "$APP_DIR/releases"

# And the same for incoming/, which the earlier version of this script left
# alone. A bundle scp'd there by a pre-fix deploy that then failed keeps the
# 0644 the deploy user's ssh umask gave it — deploy.sh only removes the bundle
# on its success path — and the half-two `find` check below would then abort
# provisioning with a message naming the file, i.e. detect the leftover without
# doing anything about it. Retracting it here means the check runs against a
# tree that has actually been fixed, rather than the operator being told to go
# and fix it by hand.
#
# `go-rwx`, not `g+rX`: releases/ is shared with the service account through
# the martyrology group, but incoming/ holds the *bundle*, which is a second
# copy of the licensed corpus in tarball form. Only the deploy user ever needs
# it — the service account reads the extracted release — so the group bits come
# off here too, and the check further down asserts exactly that.
chmod -R u+rwX,go-rwx "$APP_DIR/incoming"

# 0750, not 0755: the service account reaches the release tree through the
# shared martyrology group, and no other local uid has any business here.
chmod 0750 "$APP_DIR"
chmod 0750 "$APP_DIR/bin" "$APP_DIR/config"
# setgid, so release directories created later by deploy.sh inherit the
# martyrology group from the parent instead of the deploy user's primary group.
# deploy.sh chgrp's as well; this makes the inherited case the default rather
# than the repaired one, and covers anything created outside that chgrp (the
# venv, pip's caches) between extraction and the chgrp itself.
chmod 2750 "$APP_DIR/releases"
# 0700, not 0750: incoming/ holds the uploaded bundle, which *contains* the
# licensed corpus. Only the deploy user ever needs it; the service account
# reads the extracted release, never the tarball.
chmod 0700 "$APP_DIR/incoming"

install -o "$DEPLOY_USER" -g "$SERVICE_GROUP" -m 750 "$SCRIPT_DIR/deploy.sh" "$APP_DIR/bin/deploy.sh"

if [ ! -f "$RUNTIME_ENV" ]; then
    echo "Writing $RUNTIME_ENV"
    cat >"$RUNTIME_ENV" <<EOF
MARTYROLOGY_PORT=$PORT
MARTYROLOGY_MANIFEST_PATH=$APP_DIR/current/manifest.json
MARTYROLOGY_DATA_PATH=$APP_DIR/current/data/editions:$APP_DIR/current/data/texts
MARTYROLOGY_CRMEDR_PATH=$APP_DIR/current/data/crmedr
MARTYROLOGY_CLBDR_PATH=$APP_DIR/current/data/clbdr
EOF
else
    echo "Keeping existing $RUNTIME_ENV"
fi
# Outside the branch above: a re-run over a file written by an earlier version
# of this script must still have its mode retracted from 0644. 0640, not 0644 —
# systemd reads EnvironmentFile= as root, and the deploy script reads it as the
# owner; nothing else on the host needs the live port and paths.
chown "$DEPLOY_USER:$SERVICE_GROUP" "$RUNTIME_ENV"
chmod 0640 "$RUNTIME_ENV"

mkdir -p "$(dirname "$SECRET_ENV")"
if [ ! -f "$SECRET_ENV" ]; then
    echo "Writing $SECRET_ENV skeleton — fill these in before the first deploy"
    cat >"$SECRET_ENV" <<'EOF'
MARTYROLOGY_ZITADEL_ISSUER=
MARTYROLOGY_ZITADEL_CLIENT_ID=
MARTYROLOGY_ZITADEL_CLIENT_SECRET=
# Empty = the roles claim cannot be built, so every curation write 403s
# missing-role for every principal, even a correctly-configured issuer.
MARTYROLOGY_ZITADEL_PROJECT_ID=
MARTYROLOGY_OPENFGA_API_URL=
MARTYROLOGY_OPENFGA_STORE_ID=
MARTYROLOGY_OPENFGA_MODEL_ID=
# Empty = every check against an OpenFGA requiring a bearer token 401s, so
# curation is denied and restricted texts are redacted for everyone.
MARTYROLOGY_OPENFGA_API_TOKEN=
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

# Two halves, both of which have to hold and each of which fails loudly on its
# own. Neither can be satisfied by the other going wrong: the first asserts
# access the group grants, the second asserts the absence of access nobody
# should have. A tree that is world-readable passes half one and fails half
# two; a tree that is 0700 passes half two and fails half one.
#
# Half one — the service account really can reach what it needs. Asserted by
# impersonating it (this script runs as root, so it can; deploy.sh cannot,
# which is why deploy.sh asserts modes instead). Without this, the first
# failure is a bare "Permission denied" from ExecStart at unit start.
if ! sudo -u "$SERVICE_USER" test -x "$APP_DIR" \
    || ! sudo -u "$SERVICE_USER" test -x "$APP_DIR/releases" \
    || ! sudo -u "$SERVICE_USER" test -r "$RUNTIME_ENV"; then
    echo "ERROR: $SERVICE_USER cannot traverse $APP_DIR or $APP_DIR/releases," >&2
    echo "       or cannot read $RUNTIME_ENV. Check group membership and modes." >&2
    exit 1
fi

# Half two — nothing else can. The release tree holds the licensed corpus and
# this is a shared, Plesk-managed host, so any surviving "other" bit is a
# disclosure. Checked with find rather than by impersonation because there is
# no unrelated account to impersonate; the mode is what the kernel consults for
# a uid that is neither the owner nor in the group.
WORLD_ACCESSIBLE="$(find "$APP_DIR" ! -type l -perm /0007 2>&1)" || true
if [ -n "$WORLD_ACCESSIBLE" ]; then
    echo "$WORLD_ACCESSIBLE" >&2
    echo "ERROR: the paths above under $APP_DIR are accessible to every local" >&2
    echo "       account. The release tree contains licensed texts; refusing." >&2
    exit 1
fi

# Half two, continued: the deploy user must actually be in the shared group, or
# the deploy-time chgrp silently has nothing to chgrp to and every release lands
# unreadable by the service account.
if ! id -nG "$DEPLOY_USER" | tr ' ' '\n' | grep -qx "$SERVICE_GROUP"; then
    echo "ERROR: $DEPLOY_USER is not a member of group $SERVICE_GROUP" >&2
    exit 1
fi

# And the service account must not be able to reach the uploaded bundle, which
# is a second copy of the same corpus sitting in incoming/. This is the one
# check here whose *failure* is the pass, so it would be satisfied by `sudo -u`
# simply not working — but half one above required the same `sudo -u
# "$SERVICE_USER" test` invocation to succeed and exited if it did not, so by
# this point the mechanism is known to work and a false here means denied.
if sudo -u "$SERVICE_USER" test -r "$APP_DIR/incoming"; then
    echo "ERROR: $APP_DIR/incoming is readable by $SERVICE_USER; expected 0700" >&2
    exit 1
fi

# Report the port actually in force, not the default — a re-run that keeps
# an existing runtime.env may have a different value than $PORT.
ACTUAL_PORT="$(grep -E '^MARTYROLOGY_PORT=' "$RUNTIME_ENV" | cut -d= -f2 || true)"
ACTUAL_PORT="${ACTUAL_PORT:-$PORT}"

cat <<EOF

✓ Provisioned $DEPLOY_USER (deploys) and $SERVICE_USER (runtime).
✓ $APP_DIR ready ($DEPLOY_USER:$SERVICE_GROUP, 0750); deploy.sh installed at
  $APP_DIR/bin/deploy.sh.
✓ Unit enabled but NOT started — it needs a first release.
✓ $SERVICE_USER can traverse $APP_DIR and $APP_DIR/releases and read $RUNTIME_ENV.
✓ No path under $APP_DIR is readable by any other local account, and
  $APP_DIR/incoming (which holds the licensed corpus in bundle form) is
  reachable only by $DEPLOY_USER.

NEXT STEPS
──────────
1. Fill in the secrets in $SECRET_ENV.
   ⚠ Until you do: MARTYROLOGY_ZITADEL_ISSUER is empty, so config.py sets
     auth_enabled=False and authz_enabled=False — the API comes up healthy
     but PUBLICLY UNAUTHENTICATED. Do not skip this step.
   ⚠ Filling in every OTHER line but leaving MARTYROLOGY_OPENFGA_API_TOKEN or
     MARTYROLOGY_ZITADEL_PROJECT_ID empty does not reproduce that same open
     failure — it silently flips to fully closed instead: every OpenFGA check
     401s (token) or the roles claim cannot be built (project ID), so every
     curation write is denied and every restricted text is redacted for
     every principal, including admins. Set both.
2. Generate the deploy keypair on a workstation:
       ssh-keygen -t ed25519 -C "martyrology-api deploy" -f ./deploy-key
3. Append the PUBLIC half to $SSH_DIR/authorized_keys.
4. Capture the host key for pinning:
       ssh-keyscan -t ed25519,rsa <vps-hostname>
5. Confirm port $ACTUAL_PORT is free:
       ss -ltnp | sort -t: -k2 -n
   (to change the port on a re-run, use "sudo -E MARTYROLOGY_PORT=<port> $0"
   — plain sudo resets the environment and MARTYROLOGY_PORT would be lost)
6. Add the nginx proxy directives for the domain in Plesk (spec §6).
7. In the martyrology-api repo settings:
       Secrets:   VPS_HOST, VPS_SSH_KEY (private half), VPS_USERNAME=$DEPLOY_USER,
                  SUBMODULE_TOKEN
       Variables: VPS_HOST_KEY (ssh-keyscan output), APP_DIR=$APP_DIR
8. Publish a GitHub release to trigger the first deploy.
EOF
