#!/usr/bin/env bash
#
# deploy.sh — install and activate a martyrology-api release bundle.
#
# Runs on the VPS as the martyrology-deploy user, invoked over ssh by
# .github/workflows/deploy.yml. Installed at $APP_DIR/bin/deploy.sh by
# scripts/deploy/setup-vps-deploy-user.sh; deliberately NOT refreshed from
# the bundle, so updating it stays an operator action.
#
# The bundle's own wheels are installed and its venv's python/uvicorn are
# invoked, but only the fixed entrypoints below are ever run, and only after
# the checksum matches and every tar member name and link target has been
# screened for path traversal. Nothing else in the payload is ever executed,
# and nothing runs before those checks pass.

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

# Reads the live port from runtime.env without ever raising on a missing
# file or an unset variable, so callers (including the rollback trap) can
# treat "unknown port" as an ordinary failure rather than a script abort.
get_live_port() {
    [ -f "$RUNTIME_ENV" ] || return 1
    local port=""
    # shellcheck source=/dev/null
    port="$(. "$RUNTIME_ENV" && printf '%s' "${MARTYROLOGY_PORT:-}")"
    [ -n "$port" ] || return 1
    printf '%s' "$port"
}

# Armed immediately after `current` is flipped to the new release and
# disarmed only once the live health check passes, so any failure in
# between — a failed restart, a missing runtime.env, an unset
# MARTYROLOGY_PORT, an unhealthy service, or a signal — restores the
# previous release instead of leaving the flip half-done. set -e can exit
# the script at any of those points, and EXIT traps do not fire on their
# own for a signal, so the trap is registered for EXIT, INT, and TERM
# alike; this function still runs either way.
#
# INT/TERM are wired to call this with an explicit "143" argument instead
# of relying on the bare EXIT trap's `$?`: bash only runs a signal trap
# once the current foreground command finishes, and does not retroactively
# set $? to a signal-derived value — if the signal arrives while a plain
# external command (e.g. the `sleep 1` inside wait_healthy) is running and
# that command then completes normally on its own, $? is that command's
# own (successful) exit status, not the signal. A SIGTERM delivered to
# this process alone (a CI cancellation, an ssh disconnect) would then
# read as status 0 and skip rollback entirely, leaving `current` flipped
# to an unverified release while reporting success. Giving INT/TERM their
# own explicit, non-zero status means a signal can never present as 0.
#
# Runs under `set -e`, so every step that could itself fail (the relink,
# the restart) is explicitly guarded: an unguarded failure here would abort
# the trap mid-rollback, losing both the original failure's exit status and
# the diagnostic explaining what happened. Every path below ends by
# reaching the final `exit "$status"`.
ROLLBACK_ARMED=0
PREVIOUS=""

rollback_on_failure() {
    local status="${1:-$?}"
    trap - EXIT INT TERM
    if [ "$ROLLBACK_ARMED" -ne 1 ] || [ "$status" -eq 0 ]; then
        exit "$status"
    fi
    echo "ERROR: activation of $VERSION failed (exit $status); rolling back" >&2
    if [ -z "$PREVIOUS" ]; then
        echo "No previous release to roll back to" >&2
        exit "$status"
    fi
    if [ ! -d "$PREVIOUS" ]; then
        echo "ERROR: previous release $PREVIOUS no longer exists; cannot roll back" >&2
        exit "$status"
    fi
    if ! ln -sfn "$PREVIOUS" "$APP_DIR/current.new"; then
        echo "ERROR: failed to prepare rollback symlink for $PREVIOUS" >&2
        exit "$status"
    fi
    if ! mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"; then
        echo "ERROR: failed to activate rollback symlink for $PREVIOUS" >&2
        exit "$status"
    fi
    if ! sudo /usr/bin/systemctl restart "$SERVICE"; then
        echo "ERROR: failed to restart $SERVICE while rolling back to $PREVIOUS" >&2
        exit "$status"
    fi
    local rollback_port=""
    rollback_port="$(get_live_port || true)"
    if [ -n "$rollback_port" ] && wait_healthy "$rollback_port"; then
        echo "Rolled back to $PREVIOUS and it is healthy on port $rollback_port" >&2
    else
        echo "ERROR: rolled back to $PREVIOUS but it did not become healthy" >&2
    fi
    exit "$status"
}

# Accept --dry-run in any position and a single positional <version>;
# anything else is rejected rather than silently ignored (a stray
# "deploy.sh <version> --dry-run" must not fall through to a real deploy).
DRY_RUN=0
VERSION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            ;;
        -*)
            die "usage: deploy.sh [--dry-run] <version>"
            ;;
        *)
            [ -z "$VERSION" ] || die "usage: deploy.sh [--dry-run] <version>"
            VERSION="$1"
            ;;
    esac
    shift
done
[ -n "$VERSION" ] || die "usage: deploy.sh [--dry-run] <version>"

# Anchored, no metacharacters: the version becomes part of a path and of a
# filename, so anything outside this shape is refused outright.
[[ "$VERSION" =~ ^v?[0-9]+(\.[0-9]+)*$ ]] || die "refusing suspicious version string: $VERSION"

BUNDLE="$APP_DIR/incoming/martyrology-${VERSION}-linux-x86_64-cp312.tar.gz"
[ -f "$BUNDLE" ] || die "bundle not found: $BUNDLE"
[ -f "$BUNDLE.sha256" ] || die "checksum not found: $BUNDLE.sha256"

# Verify the digest directly against the bundle's own bytes, and assert the
# checksum file actually names this bundle — `sha256sum -c` only checks that
# the digest matches whatever filename is written in the .sha256 file, so a
# checksum file naming an unrelated file would otherwise verify cleanly
# without ever hashing the tarball.
BUNDLE_BASENAME="$(basename "$BUNDLE")"
CHECKSUM_EXPECTED="$(awk '{print $1}' "$BUNDLE.sha256")"
CHECKSUM_NAMED="$(awk '{print $2}' "$BUNDLE.sha256" | sed 's|^\*||')"
[ "$CHECKSUM_NAMED" = "$BUNDLE_BASENAME" ] \
    || die "checksum file names $CHECKSUM_NAMED, not $BUNDLE_BASENAME"
CHECKSUM_ACTUAL="$(sha256sum "$BUNDLE" | awk '{print $1}')"
[ "$CHECKSUM_EXPECTED" = "$CHECKSUM_ACTUAL" ] || die "checksum mismatch for $BUNDLE"

# Two separate captures, neither piped into grep: grep -q exits as soon as
# it finds a match, which closes the pipe out from under a still-writing
# tar and makes it exit on SIGPIPE — under `pipefail` that turns the whole
# pipeline non-zero, so `if pipeline; then die; fi` sees a FALSE condition
# and the guard never fires. Capturing to a variable first makes tar always
# run to completion before anything is screened.
#
# BUNDLE_NAMES (plain `tar -t`) is one member name per line, verbatim, and
# is grepped whole-line — not split into fields — so a name containing a
# space is still screened as a unit. GNU tar itself strips a leading "/"
# or "../" from member names by default on extraction, so this check is
# mostly defense-in-depth over tar's own behavior; it exists because not
# every tar implementation does that, and because relying on it silently
# would be exactly the kind of assumption this script exists to avoid.
#
# BUNDLE_MEMBERS (`tar -tv`) is the verbose listing, needed separately
# because `tar -t` never prints where a symlink or hardlink points — only
# its own name — and a symlink's target gets no sanitization from tar at
# all, on extraction or otherwise. GNU tar does eagerly normalize hard
# link targets (both here and at extraction), but that is this tar
# implementation's behavior, not a guarantee this script can rely on, so
# both link kinds are screened the same way regardless. The verbose
# listing renders a symlink as "name -> target" and a hardlink as
# "name link to target"; the check below matches either rendering and
# reads the text right after it directly, rather than splitting the line
# into whitespace fields, so a target containing a space is still
# screened correctly. The escape patterns themselves catch an absolute
# target, a "../" anywhere in it, and also a bare ".." (or a component
# ending in "..", e.g. "a/..") with nothing after it — not just one
# followed by a slash — since that also walks up a directory.
BUNDLE_NAMES="$(tar -tzf "$BUNDLE")" \
    || die "failed to list bundle contents: $BUNDLE (corrupt or truncated?)"
BUNDLE_MEMBERS="$(tar -tvzf "$BUNDLE")" \
    || die "failed to list bundle contents: $BUNDLE (corrupt or truncated?)"

if grep -Eq '^/|(^|/)\.\.(/|$)' <<<"$BUNDLE_NAMES"; then
    die "bundle contains absolute or parent-relative paths"
fi

if grep -Eq '(^| )[hl]?[rwx-]{9}.* (->|link to) (/|.*\.\.(/|$))' <<<"$BUNDLE_MEMBERS"; then
    die "bundle contains a link pointing outside the release tree"
fi

RELEASE="$APP_DIR/releases/$VERSION"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: $BUNDLE verified; would install to $RELEASE"
    exit 0
fi

# Refuse to redeploy the version that is already live: rm -rf below would
# tear down the active release before a replacement is verified, and a
# rollback afterwards would just relink the same now-empty directory.
if [ -L "$APP_DIR/current" ] && [ "$(readlink "$APP_DIR/current")" = "$RELEASE" ]; then
    die "$VERSION is the currently active release; deactivate or bump the version before redeploying"
fi

echo "Installing $VERSION to $RELEASE"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
tar -xzf "$BUNDLE" -C "$RELEASE"

echo "Building venv (offline)"
python3.12 -m venv "$RELEASE/venv"
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

echo "Smoke-checking the new release before activating it"
SMOKE_PORT="$("$RELEASE/venv/bin/python" -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
SMOKE_LOG="$(mktemp)"
SMOKE_PID=""
# Armed right after the temp file is created, before the background
# process even starts, so SMOKE_LOG cannot leak if something between here
# and the `&` below exits the script; $SMOKE_PID is expanded when the trap
# actually fires, so it picks up the real pid once one exists. Also covers
# INT/TERM, not just EXIT, so a signal during the smoke phase still kills
# the smoke uvicorn and removes the log instead of orphaning both.
trap 'kill "$SMOKE_PID" 2>/dev/null || true; rm -f "$SMOKE_LOG"' EXIT INT TERM

MARTYROLOGY_MANIFEST_PATH="$RELEASE/manifest.json" \
MARTYROLOGY_DATA_PATH="$RELEASE/data/editions:$RELEASE/data/texts" \
MARTYROLOGY_CRMEDR_PATH="$RELEASE/data/crmedr" \
MARTYROLOGY_CLBDR_PATH="$RELEASE/data/clbdr" \
    "$RELEASE/venv/bin/uvicorn" martyrology_api.app:create_app --factory \
    --host 127.0.0.1 --port "$SMOKE_PORT" >"$SMOKE_LOG" 2>&1 &
SMOKE_PID=$!

if ! wait_healthy "$SMOKE_PORT"; then
    kill "$SMOKE_PID" 2>/dev/null || true
    cat "$SMOKE_LOG" >&2
    die "smoke check failed; $VERSION was not activated"
fi

EDITIONS="$(curl -fsS "http://127.0.0.1:${SMOKE_PORT}/healthz" \
    | "$RELEASE/venv/bin/python" -c 'import json,sys; print(len(json.load(sys.stdin)["editions"]))')"
[ "$EDITIONS" -gt 0 ] || die "smoke check served zero editions; $VERSION was not activated"
echo "Smoke check passed: $EDITIONS editions"

kill "$SMOKE_PID" 2>/dev/null || true
rm -f "$SMOKE_LOG"
trap - EXIT INT TERM

if [ -L "$APP_DIR/current" ]; then
    PREVIOUS="$(readlink "$APP_DIR/current")"
fi

echo "Activating $VERSION"
ln -sfn "$RELEASE" "$APP_DIR/current.new"
mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"

ROLLBACK_ARMED=1
trap rollback_on_failure EXIT
trap 'rollback_on_failure 143' INT TERM

sudo /usr/bin/systemctl restart "$SERVICE"

LIVE_PORT="$(get_live_port)" || die "could not determine MARTYROLOGY_PORT from $RUNTIME_ENV"
wait_healthy "$LIVE_PORT" || die "$VERSION is unhealthy on port $LIVE_PORT"

echo "$VERSION is live and healthy on port $LIVE_PORT"

ROLLBACK_ARMED=0
trap - EXIT INT TERM

rm -f "$BUNDLE" "$BUNDLE.sha256"
CURRENT_TARGET="$(readlink "$APP_DIR/current")"
# shellcheck disable=SC2012
ls -1dt "$APP_DIR"/releases/*/ 2>/dev/null | tail -n "+$((KEEP_RELEASES + 1))" | while read -r old; do
    [ "${old%/}" = "$CURRENT_TARGET" ] && continue
    echo "Pruning ${old%/}"
    rm -rf "${old%/}"
done
