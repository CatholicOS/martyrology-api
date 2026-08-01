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
