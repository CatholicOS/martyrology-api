#!/usr/bin/env bash
#
# grant-superuser.sh — write the platform:martyrology superuser tuple.
#
# Out-of-band by design, exactly as in production. The API's
# /api/v1/admin/permissions endpoint fixes its object type to governance_body,
# so platform: tuples are structurally unreachable through it — otherwise any
# body admin could mint themselves a superuser. Every superuser grant, not just
# the first, is made this way.
#
# The `sub` only exists after that account has signed in once, which is why
# this cannot be folded into setup-stack.sh.
#
# Usage:   ./scripts/grant-superuser.sh <zitadel-sub>
# Revoke:  ./scripts/grant-superuser.sh <zitadel-sub> --revoke

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SUB="${1:-}"
[[ -n "$SUB" ]] || { echo "Usage: $0 <zitadel-sub> [--revoke]" >&2; exit 64; }
OP="writes"
[[ "${2:-}" == "--revoke" ]] && OP="deletes"

ENV_FILE=".env"
API_URL="$(grep -E '^MARTYROLOGY_OPENFGA_API_URL=' "$ENV_FILE" | cut -d= -f2-)"
STORE_ID="$(grep -E '^MARTYROLOGY_OPENFGA_STORE_ID=' "$ENV_FILE" | cut -d= -f2)"
TOKEN="$(grep -E '^MARTYROLOGY_OPENFGA_API_TOKEN=' "$ENV_FILE" | cut -d= -f2)"

for v in API_URL STORE_ID TOKEN; do
    [[ -n "${!v}" ]] || { echo "$v missing from $ENV_FILE — run setup-stack.sh first" >&2; exit 1; }
done

curl -sS --fail-with-body -X POST "$API_URL/stores/$STORE_ID/write" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"$OP\":{\"tuple_keys\":[{\"user\":\"user:$SUB\",\"relation\":\"superuser\",\"object\":\"platform:martyrology\"}]}}"

echo
echo "✓ $OP superuser tuple for user:$SUB"
