#!/usr/bin/env bash
#
# smoke.sh — assert the bring-up invariants a compose file can get wrong.
#
# Not a substitute for pytest: this checks wiring, not behaviour. Run it after
# `setup-stack.sh --update-env` with the API running on the host.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
set -a; . ./.env; set +a

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  ✓ %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  ✗ %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  ~ %s\n' "$1"; SKIP=$((SKIP+1)); }

API="http://localhost:${API_PORT:-8000}"
FGA="${MARTYROLOGY_OPENFGA_API_URL:-http://localhost:8083}"
ISSUER="${MARTYROLOGY_ZITADEL_ISSUER:-http://localhost:8080}"

echo "1. Zitadel discovery"
curl -sf "$ISSUER/.well-known/openid-configuration" | jq -e '.issuer' >/dev/null \
    && ok "discovery served at $ISSUER" || bad "no discovery document at $ISSUER"

echo "2. OpenFGA structural tuples"
COUNT=$(curl -sf -X POST "$FGA/stores/$MARTYROLOGY_OPENFGA_STORE_ID/read" \
    -H "Authorization: Bearer $MARTYROLOGY_OPENFGA_API_TOKEN" \
    -H "Content-Type: application/json" -d '{}' | jq '.tuples | length')
[[ "$COUNT" == "11" ]] && ok "11 structural tuples" || bad "expected 11 tuples, got ${COUNT:-none}"

echo "3. Alembic is at head"
CUR=$(docker compose run --rm --entrypoint alembic api-migrate current 2>/dev/null | tr -d '\r')
grep -q '(head)' <<<"$CUR" && ok "alembic current is at head" || bad "alembic not at head: $CUR"

echo "4. API health"
curl -sf "$API/healthz" >/dev/null && ok "GET /healthz 200" || bad "GET /healthz failed"

echo "5. Anonymous read of a restricted edition is redacted"
# "no such edition" and "redacted" are BOTH 200-shaped to a careless check, so
# distinguish them: absent edition => skip, present => must be redacted.
BODY=$(curl -sf "$API/api/v1/elogia/edition/martyrologium_romanum_2004/01/02" 2>/dev/null)
if [[ -z "$BODY" ]]; then
    skip "martyrologium_romanum_2004 not attached (martyrology-texts not mounted)"
else
    ACCESS=$(jq -r '.metadata.access // empty' <<<"$BODY")
    TEXT=$(jq -r '.elogia[0].text // "null"' <<<"$BODY")
    [[ "$ACCESS" == "restricted-texts" && "$TEXT" == "null" ]] \
        && ok "access=restricted-texts with text=null" \
        || bad "expected redaction, got access=$ACCESS text=$TEXT"
fi

echo
printf 'passed %d, failed %d, skipped %d\n' "$PASS" "$FAIL" "$SKIP"
[[ $FAIL -eq 0 ]]
