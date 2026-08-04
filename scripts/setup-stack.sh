#!/usr/bin/env bash
#
# setup-stack.sh — provision the local Zitadel and discover the OpenFGA IDs,
# then write both into .env.
#
# SIBLING NOTE: martyrology-frontend's scripts/setup-stack.sh is a near-
# duplicate of this file (same provisioning wait loop, cdcf-infra clone,
# capture-file handling, and set_env; it additionally provisions a frontend
# OIDC app and AUTH_SECRET, which this repo has no equivalent of). Duplicated
# rather than shared because the two repos have no submodule/package
# relationship and these scripts run on the host before any container
# exists — see martyrology-frontend's
# .superpowers/sdd/2026-08-04-local-development-stack/task-13-report.md for
# the full reasoning. If you change the shared parts of this file, apply the
# same fix to martyrology-frontend's copy, and vice versa.
#
# Phase 2 of the three-phase bring-up (see README.md → "Local development
# stack"). The store ID, model ID, client ID and client secret are all
# GENERATED at provisioning time, so they cannot be committed; this script
# captures them.
#
# ⚠ The client secret is emitted ONCE, by the run that creates the app.
# Zitadel's ListApplications API does not return secrets, so a re-run against
# an existing app cannot recover it. If .env is lost, rotate in the console:
#   Martyrology Org → Projects → MartyrologyAPI → Apps → Regenerate Client Secret
#
# Usage: ./scripts/setup-stack.sh --update-env

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

UPDATE_ENV=0
[[ "${1:-}" == "--update-env" ]] && UPDATE_ENV=1
if [[ $UPDATE_ENV -eq 0 ]]; then
    echo "Usage: $0 --update-env" >&2
    exit 64
fi

ENV_FILE=".env"
PAT_FILE="./.zitadel-data/automation-user.pat"
ZITADEL_PORT="$(grep -E '^ZITADEL_PORT=' "$ENV_FILE" | cut -d= -f2 || true)"
ZITADEL_PORT="${ZITADEL_PORT:-8080}"
OPENFGA_HTTP_PORT="$(grep -E '^OPENFGA_HTTP_PORT=' "$ENV_FILE" | cut -d= -f2 || true)"
OPENFGA_HTTP_PORT="${OPENFGA_HTTP_PORT:-8083}"
PRESHARED_KEY="$(grep -E '^OPENFGA_PRESHARED_KEY=' "$ENV_FILE" | cut -d= -f2 || true)"
[[ -n "$PRESHARED_KEY" ]] || { echo "OPENFGA_PRESHARED_KEY missing from $ENV_FILE" >&2; exit 1; }
CDCF_INFRA_REF="$(grep -E '^CDCF_INFRA_REF=' "$ENV_FILE" | cut -d= -f2 || true)"
CDCF_INFRA_REF="${CDCF_INFRA_REF:-main}"

ISSUER="http://localhost:${ZITADEL_PORT}"
WORKDIR=".stack-out"
mkdir -p "$WORKDIR"

# --- wait for Zitadel -----------------------------------------------------
echo "Waiting for Zitadel at $ISSUER ..."
for _ in $(seq 1 60); do
    if curl -sf "$ISSUER/.well-known/openid-configuration" >/dev/null; then break; fi
    sleep 2
done
curl -sf "$ISSUER/.well-known/openid-configuration" >/dev/null \
    || { echo "Zitadel never became ready" >&2; exit 1; }
[[ -s "$PAT_FILE" ]] || { echo "PAT not found at $PAT_FILE" >&2; exit 1; }

# --- clone or refresh cdcf-infra -----------------------------------------
INFRA_DIR="$WORKDIR/cdcf-infra"
if [[ -d "$INFRA_DIR/.git" ]]; then
    git -C "$INFRA_DIR" fetch --quiet origin "$CDCF_INFRA_REF"
    git -C "$INFRA_DIR" checkout --quiet "FETCH_HEAD"
else
    git clone --quiet --depth 1 --branch "$CDCF_INFRA_REF" \
        https://github.com/CatholicOS/cdcf-infra.git "$INFRA_DIR"
fi

# --- provision Zitadel ----------------------------------------------------
# ZITADEL_PAT_FILE must be absolute: setup-zitadel.sh runs from auth/.
cat > "$INFRA_DIR/auth/.env.local" <<EOF
ZITADEL_ISSUER=$ISSUER
ZITADEL_INTERNAL_URL=$ISSUER
ZITADEL_PAT_FILE=$(cd "$(dirname "$PAT_FILE")" && pwd)/$(basename "$PAT_FILE")
ZITADEL_ADMIN_EMAIL=root@martyrology.localhost
EOF

OUT="$WORKDIR/zitadel-provision.out"
# The provisioner's stdout carries the one-time client secret. Create the
# capture file with owner-only permissions BEFORE tee writes to it — tee
# opens an existing file without changing its mode, so pre-creating it
# closes the window where the secret would briefly land in a 644 file.
(umask 077; : > "$OUT")
chmod 600 "$OUT"
(
    cd "$INFRA_DIR/auth"
    ./setup-zitadel.sh --target local \
        --create-org Martyrology \
        --provision-martyrology
) | tee "$OUT"

# The handoff block prints `KEY=value` lines, some with a trailing comment.
# Colours are suppressed automatically because stdout is a pipe.
val() { sed -n "s/^$1=\([^ ]*\).*/\1/p" "$OUT" | head -1; }

CLIENT_ID="$(val MARTYROLOGY_ZITADEL_CLIENT_ID)"
CLIENT_SECRET="$(val MARTYROLOGY_ZITADEL_CLIENT_SECRET)"
PROJECT_ID="$(val ZITADEL_PROJECT_ID)"

# Parsed — the capture file's only reason to exist is gone, and it is the
# one place a plaintext copy of the one-time secret could otherwise survive
# indefinitely at rest. Remove it now rather than leaving even a
# permission-protected copy around.
rm -f "$OUT"

[[ -n "$CLIENT_ID" ]]  || { echo "No client ID in provisioner output" >&2; exit 1; }
[[ -n "$PROJECT_ID" ]] || { echo "No project ID in provisioner output" >&2; exit 1; }

# --- discover the OpenFGA IDs --------------------------------------------
# Queried from the API rather than parsed out of setup-openfga.sh's output:
# the store already exists (authz-seed created it), and an API read is stable
# where output parsing is not.
FGA="http://localhost:${OPENFGA_HTTP_PORT}"
STORE_ID="$(curl -sf -H "Authorization: Bearer $PRESHARED_KEY" "$FGA/stores" \
    | jq -r '.stores[] | select(.name=="Martyrology") | .id' | head -1)"
[[ -n "$STORE_ID" ]] || { echo "No Martyrology store found at $FGA" >&2; exit 1; }

MODEL_ID="$(curl -sf -H "Authorization: Bearer $PRESHARED_KEY" \
    "$FGA/stores/$STORE_ID/authorization-models?page_size=1" \
    | jq -r '.authorization_models[0].id')"
[[ -n "$MODEL_ID" && "$MODEL_ID" != "null" ]] \
    || { echo "No authorization model in store $STORE_ID" >&2; exit 1; }

# --- write .env -----------------------------------------------------------
# Values come from Zitadel/OpenFGA and may contain arbitrary punctuation
# (secrets especially). A sed `s|^KEY=.*|KEY=VALUE|` splice would treat `&`
# in VALUE as "the whole matched line" (silent corruption, not an error) and
# `|` would break the delimiter — so the key/value are passed through the
# environment into awk instead, matched by literal prefix (no regex, no
# replacement-string metacharacters), never interpolated into program text.
# Written to a temp file and renamed in rather than edited in place, so a
# key that isn't present is appended exactly once and one that is present
# is replaced exactly where it stood.
set_env() {
    local key="$1" value="$2"
    local tmp
    tmp="$(mktemp "$(dirname "$ENV_FILE")/.env.XXXXXX")"
    SET_ENV_KEY="$key" SET_ENV_VALUE="$value" awk '
        BEGIN {
            key = ENVIRON["SET_ENV_KEY"]
            value = ENVIRON["SET_ENV_VALUE"]
            prefix = key "="
            found = 0
        }
        {
            if (!found && substr($0, 1, length(prefix)) == prefix) {
                print prefix value
                found = 1
            } else {
                print
            }
        }
        END {
            if (!found) print prefix value
        }
    ' "$ENV_FILE" > "$tmp"
    # mv replaces $ENV_FILE with the temp file's own mode, so re-assert 600
    # on the temp file before the swap rather than trusting it survives.
    chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
}

set_env MARTYROLOGY_ZITADEL_ISSUER       "$ISSUER"
set_env MARTYROLOGY_ZITADEL_INTERNAL_URL "$ISSUER"
set_env MARTYROLOGY_ZITADEL_CLIENT_ID    "$CLIENT_ID"
set_env MARTYROLOGY_ZITADEL_PROJECT_ID   "$PROJECT_ID"
set_env MARTYROLOGY_OPENFGA_API_URL      "$FGA"
set_env MARTYROLOGY_OPENFGA_STORE_ID     "$STORE_ID"
set_env MARTYROLOGY_OPENFGA_MODEL_ID     "$MODEL_ID"
set_env MARTYROLOGY_OPENFGA_API_TOKEN    "$PRESHARED_KEY"

if [[ -n "$CLIENT_SECRET" ]]; then
    set_env MARTYROLOGY_ZITADEL_CLIENT_SECRET "$CLIENT_SECRET"
    echo "✓ Client secret captured (one-time emit)."
else
    echo "⚠ No client secret emitted — the app already existed." >&2
    echo "  Existing MARTYROLOGY_ZITADEL_CLIENT_SECRET in .env left untouched." >&2
    grep -qE '^MARTYROLOGY_ZITADEL_CLIENT_SECRET=.+' "$ENV_FILE" \
        || echo "  .env has NO secret. Rotate it in the Zitadel console." >&2
fi

# Belt and suspenders: set_env's mv already leaves $ENV_FILE at 600 (the
# temp file's own mode), but assert it explicitly — $ENV_FILE now holds a
# live client secret and must never be group/world-readable.
chmod 600 "$ENV_FILE"

echo
echo "✓ .env updated. Restart the API to pick up the new values."
