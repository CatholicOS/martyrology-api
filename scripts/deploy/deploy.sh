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
# The group shared with the service account, created by
# setup-vps-deploy-user.sh, which also adds this (deploy) user to it. Every
# release tree is chgrp'ed to it, and that group is the *only* way anything
# other than the deploy user reaches the licensed corpus under data/texts.
SERVICE_GROUP="${SERVICE_GROUP:-martyrology}"
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

# Armed immediately before `current` is flipped to the new release and
# disarmed only once the live health check passes, so any failure in
# between — a failed flip, a failed restart, a missing runtime.env, an unset
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

# The bundle contains the licensed corpus, and scp created it with whatever
# umask the deploy user's ssh session had. Tighten it the moment the path is
# known to exist and before anything else touches it: incoming/ is 0700 so the
# directory already gates access, but the bundle is only removed on the success
# path far below, so a failed deploy would otherwise leave a permissive copy
# sitting there until the next successful one.
chmod 600 "$BUNDLE" "$BUNDLE.sha256"

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
# Both listings are taken with -P ("absolute names"), which tells tar to
# report what the archive actually stores rather than what tar would
# choose to install. Without it GNU tar silently rewrites the listing
# before this script can look at it — most importantly it collapses a hard
# link target of "/etc/passwd" or "../../etc/passwd" down to a harmless
# "etc/passwd" — so the screen would be checking tar's sanitized rendering
# instead of the bundle's real contents, i.e. depending on exactly the
# behavior these checks exist not to depend on. -P is deliberately NOT
# passed to the extraction below: there, tar's sanitization is a wanted
# last line of defense, and turning it off would let an absolute member
# name write outside the release tree.
#
# BUNDLE_NAMES (plain `tar -P -t`) is one member name per line, verbatim,
# and is grepped whole-line — not split into fields — so a name containing
# a space is still screened as a unit. GNU tar strips a leading "/" or
# "../" from member names when it extracts (that part is unaffected by -P
# here, since extraction runs without it), so this check is defense in
# depth over tar's own behavior; it exists because not every tar
# implementation does that, and because relying on it silently would be
# exactly the kind of assumption this script exists to avoid.
#
# BUNDLE_MEMBERS (`tar -P -tv`) is the verbose listing, needed separately
# because `tar -t` never prints where a symlink or hardlink points — only
# its own name. A symlink's target gets no sanitization from tar at all,
# on extraction or otherwise; a hardlink's does, but only as this tar
# implementation's behavior, not a guarantee, and -P is what keeps that
# rewriting out of the listing so the real target is what gets screened.
# The verbose listing renders a symlink as "name -> target" and a hardlink
# as "name link to target"; the check below matches either rendering and
# reads the text right after it directly, rather than splitting the line
# into whitespace fields, so a target containing a space is still
# screened correctly. The escape patterns themselves catch an absolute
# target, a "../" anywhere in it, and also a bare ".." (or a component
# ending in "..", e.g. "a/..") with nothing after it — not just one
# followed by a slash — since that also walks up a directory.
BUNDLE_NAMES="$(tar -P -tzf "$BUNDLE")" \
    || die "failed to list bundle contents: $BUNDLE (corrupt or truncated?)"
BUNDLE_MEMBERS="$(tar -P -tvzf "$BUNDLE")" \
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

# $APP_DIR, releases/ and incoming/ get their modes from
# setup-vps-deploy-user.sh, which asserts them once — at provisioning time.
# Nothing re-asserted them afterwards, so a later `chmod 0755 /opt/martyrology`
# (an operator debugging a permission problem, a restore that did not preserve
# modes, a Plesk tool tidying up) went unnoticed by every subsequent deploy: the
# per-release normalisation below would keep reporting a correctly locked-down
# release tree while the directory above it published that tree — and the
# uploaded bundle in incoming/ — to every other uid on this shared host.
#
# Non-recursive on purpose. The contents of releases/ are covered by
# normalise_release_permissions() and the contents of incoming/ by the `chmod
# 600` on the bundle above; what is unowned by any other check is these three
# directories' own modes. Each is tested separately so the message names the one
# to fix rather than pointing at the tree in general.
#
# `find -L` so a symlinked $APP_DIR is judged by the mode of the directory it
# resolves to rather than by the link's own inert 0777; a broken link makes find
# write to stderr, which is captured into the same variable and therefore fails
# loudly instead of reading as "no other bits found".
for GUARDED_DIR in "$APP_DIR" "$APP_DIR/releases" "$APP_DIR/incoming"; do
    [ -d "$GUARDED_DIR" ] || die "required directory is missing: $GUARDED_DIR"
    WORLD_ACCESSIBLE="$(find -L "$GUARDED_DIR" -maxdepth 0 -perm /0007 2>&1)" || true
    [ -z "$WORLD_ACCESSIBLE" ] || die \
        "$GUARDED_DIR is accessible to every local account; expected no 'other' permission bits (run setup-vps-deploy-user.sh)"
done

echo "Installing $VERSION to $RELEASE"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
# No -P here, unlike the listings above: extraction keeps tar's own
# stripping of leading "/" and "../" as a last line of defense behind the
# screen that has already run.
tar -xzf "$BUNDLE" -C "$RELEASE"

echo "Building venv (offline)"
python3.12 -m venv "$RELEASE/venv"
"$RELEASE/venv/bin/pip" install --quiet --no-index \
    --find-links "$RELEASE/wheels" martyrology-api

# The service account runs the app, and reaches this tree solely through the
# shared $SERVICE_GROUP. Tar member modes come from the CI runner, directory
# modes from this script's umask, and the group from whatever releases/'s
# setgid bit propagated — none of which is guaranteed, so normalise all three
# here rather than discover it when systemd fails to exec.
#
# A function rather than a straight-line block because it has to run more than
# once: everything written into $RELEASE *after* the first call — most
# realistically __pycache__ from the smoke check, which runs the app out of
# this very tree — would otherwise never be normalised and never be checked.
# One definition, called at both points, so the fix and its proof cannot drift
# apart between the two. It is idempotent by construction: chgrp/chmod restate
# the wanted end state rather than adjusting relative to the current one.
#
# What must NOT happen is the obvious `a+rX`: data/texts holds the licensed
# martyrology-texts corpus, and this is a shared Plesk host where every other
# subscription runs its own non-chrooted uid. A world-readable release tree
# publishes the corpus to all of them, which is exactly what the private
# submodule exists to prevent. Group in, everyone else out.
#
# chgrp needs the deploy user to be a member of $SERVICE_GROUP —
# setup-vps-deploy-user.sh's `usermod -aG` is what makes that true, and its own
# membership check is what makes a missing membership loud there rather than
# here. Capital X (not lowercase x) only sets the execute bit on directories
# and on files that already have an execute bit somewhere, so it does not make
# every JSON data file executable. Both run once per call, after the tree is
# complete, not per-file or in a loop.
normalise_release_permissions() {
    chgrp -R "$SERVICE_GROUP" "$RELEASE"
    chmod -R u+rwX,g+rX,o-rwx "$RELEASE"

    # The two lines above are the fix; this proves they stuck, without
    # impersonating the service account (this script has no sudo grant for
    # that — see the provisioning script's own `sudo -u martyrology test -x/-r`
    # check, which runs as root at provisioning time, not here). It asserts
    # both halves, and neither can mask the other: group bits present *and*
    # every "other" bit absent *and* the group actually being $SERVICE_GROUP. A
    # tree left world-readable fails it just as loudly as one the service
    # account cannot read — which is the point, since the world-readable case
    # is the one that leaks the corpus while looking like a healthy deploy.
    #
    # Symlinks are excluded because on Linux a symlink's own mode is inert
    # (always lrwxrwxrwx, and chmod -R does not follow them); access is decided
    # by the target, which find visits in its own right. Without this exclusion
    # every venv symlink would trip the "other bits set" arm and the check
    # would fail always, for no real reason — a check that always fires teaches
    # operators to ignore it.
    #
    # find descends fully here because u+rwX above guarantees this user can
    # traverse everything it owns, so nothing is skipped unexamined. `|| true`
    # on the capture is deliberate: it exists so a nonzero exit from find (e.g.
    # a permission error on something this user somehow does not own) still
    # reaches the is-empty check below instead of tripping `set -e` and
    # discarding the diagnostic before it can be printed — the stderr text is
    # captured into the same variable, so such a failure reports rather than
    # passes. UNREADABLE is deliberately not `local`: the tests splice this
    # block out of the script verbatim and run it at top level.
    UNREADABLE="$(find "$RELEASE" ! -type l \( \
        \( -type d ! -perm -0050 \) -o \
        \( -type f ! -perm -0040 \) -o \
        -perm /0007 -o \
        ! -group "$SERVICE_GROUP" \) 2>&1)" || true
    if [ -n "$UNREADABLE" ]; then
        echo "$UNREADABLE" >&2
        die "release tree is not group-readable by $SERVICE_GROUP with all other-access denied"
    fi
}

normalise_release_permissions

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
# actually fires, so it picks up the real pid once one exists.
#
# INT/TERM are registered separately from EXIT, and their handler ends in
# an explicit `exit 143`, for the same reason the rollback trap below
# splits them: a bash trap for a signal does NOT terminate the script. It
# runs and then returns control to wherever execution was, so a single
# `trap '<cleanup>' EXIT INT TERM` would clean up on a TERM and then carry
# straight on into the flip, the systemctl restart and the prune — turning
# a cancelled deploy into an apparently successful one. Only the EXIT case
# ends the script on its own. With no trap installed at all, bash's
# default disposition would already have exited 143 here, so this wiring
# has to reproduce that explicitly rather than weaken it.
#
# The handler clears all three traps LAST, not first. Clearing first left a
# window — between the `trap -` and the `kill`/`rm` — in which the traps
# were already gone but the cleanup had not happened yet, so a signal
# landing there got bash's default disposition and killed the script on the
# spot, orphaning the smoke uvicorn and leaving $SMOKE_LOG in /tmp. Small,
# but it is the one window in this phase where a signal loses the cleanup
# entirely. Clearing last means a signal arriving mid-body is still trapped
# and the handler simply runs again; the body is idempotent (`kill … ||
# true`, `rm -f`), so a double run is harmless, whereas a missed run is
# not. The clear still happens before the function returns, so it remains
# the disarm the success path below relies on.
smoke_cleanup() {
    kill "$SMOKE_PID" 2>/dev/null || true
    rm -f "$SMOKE_LOG"
    trap - EXIT INT TERM
}
trap smoke_cleanup EXIT
trap 'smoke_cleanup; exit 143' INT TERM

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

# Same handler on the success path, so the teardown has exactly one
# definition and cannot drift from what the traps run; it clears its own
# traps as its last act, which is also the disarm this phase needs before
# the rollback trap below is armed.
smoke_cleanup

# Second call, and the reason this is a function. The smoke check ran the app
# out of $RELEASE, so python may have written __pycache__ directories and .pyc
# files into it since the first normalisation — created with this process's
# umask, not with the modes just asserted. Re-normalise and re-assert now that
# the smoke uvicorn is dead and nothing else will write here, so what gets
# activated below is the tree that was checked, not the tree as it was several
# steps ago.
normalise_release_permissions

if [ -L "$APP_DIR/current" ]; then
    PREVIOUS="$(readlink "$APP_DIR/current")"
fi

echo "Activating $VERSION"

# Armed BEFORE the flip, not after. Arming afterwards left a window between
# `mv -Tf` and the `trap` lines in which a TERM (a cancelled CI run, an ssh
# disconnect) exited with `current` already pointing at the new release and the
# service never restarted — no rollback, because the trap did not exist yet.
#
# Arming early is safe in both directions. $PREVIOUS is captured just above, so
# the handler always knows where to go back to, and it has three independent
# early exits for the "nothing to roll back" cases: ROLLBACK_ARMED not 1,
# status 0, and an empty or vanished $PREVIOUS. The only new paths this opens
# are a failing `ln` or `mv` — i.e. `current` never moved — where the handler
# relinks `current` to the value it already has and restarts the service. That
# is a redundant restart of an already-correct release, not a wrong state, and
# it still exits with the original failure's status.
ROLLBACK_ARMED=1
trap rollback_on_failure EXIT
trap 'rollback_on_failure 143' INT TERM

ln -sfn "$RELEASE" "$APP_DIR/current.new"
mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"

sudo /usr/bin/systemctl restart "$SERVICE"

LIVE_PORT="$(get_live_port)" || die "could not determine MARTYROLOGY_PORT from $RUNTIME_ENV"
wait_healthy "$LIVE_PORT" || die "$VERSION is unhealthy on port $LIVE_PORT"

# wait_healthy only proves that *something* is answering /healthz on that port.
# If the restart did not actually swap processes — systemd reporting success
# while the old unit kept running, a restart racing an already-running
# instance, a `current` flip that silently did not take — the previous release
# answers, the poll passes, and the deploy reports success for a version that
# was never activated. So assert the served version is the one just installed,
# and do it BEFORE the rollback trap is disarmed below, so a mismatch takes the
# rollback path (via die → EXIT trap) rather than merely printing.
#
# The version is read the same way the smoke check reads its editions count:
# with the release's own python, which is the only JSON parser this script can
# rely on being present. A here-string rather than a pipe, so a parser that
# exits early can never SIGPIPE the writer and turn a failed check into a
# passed one under `pipefail`.
#
# $VERSION may arrive as "0.1.0" (the workflow passes the bare pyproject
# version) or as "v0.1.0" (a manual invocation; the argument regex accepts
# both), while HealthOut.version is always the bare form — so strip one leading
# "v" before comparing rather than comparing two different spellings.
LIVE_HEALTH="$(curl -fsS "http://127.0.0.1:${LIVE_PORT}/healthz")" \
    || die "$VERSION answered the health poll on port $LIVE_PORT but /healthz could not be re-read"
SERVED_VERSION="$("$RELEASE/venv/bin/python" -c \
    'import json,sys; print(json.load(sys.stdin).get("version", ""))' <<<"$LIVE_HEALTH")" \
    || die "$VERSION is live on port $LIVE_PORT but /healthz did not parse as JSON"
EXPECTED_VERSION="${VERSION#v}"
[ "$SERVED_VERSION" = "$EXPECTED_VERSION" ] || die \
    "port $LIVE_PORT is serving version '$SERVED_VERSION', not the just-deployed '$EXPECTED_VERSION'; the restart did not swap processes"

echo "$VERSION is live and healthy on port $LIVE_PORT (serving version $SERVED_VERSION)"

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
