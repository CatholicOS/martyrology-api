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
