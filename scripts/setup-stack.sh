#!/usr/bin/env bash
#
# setup-stack.sh — provision the local Zitadel and discover the OpenFGA IDs,
# then write both into .env.
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
PRESHARED_KEY="$(grep -E '^OPENFGA_PRESHARED_KEY=' "$ENV_FILE" | cut -d= -f2)"
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
set_env() {
    local key="$1" value="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
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

echo
echo "✓ .env updated. Restart the API to pick up the new values."
