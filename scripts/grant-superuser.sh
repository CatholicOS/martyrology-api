#!/usr/bin/env bash
#
# grant-superuser.sh — write the platform:martyrology superuser tuple.
#
# SIBLING NOTE: byte-for-byte the same tool as martyrology-frontend's
# scripts/grant-superuser.sh — both write to the exact same OpenFGA store and
# object (superuser is a platform-wide grant, not scoped to either app).
# Duplicated rather than shared for the same reason as setup-stack.sh (no
# submodule/package relationship between the two repos). If you change this
# file, apply the same fix to martyrology-frontend's copy, and vice versa.
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
# OpenFGA does not validate that a sub corresponds to a real user — a
# transposed digit silently grants superuser to a nonexistent identity while
# the intended person still cannot do anything, with nothing to surface the
# mistake. So this script always prints exactly what it is about to do
# before writing, and asks for confirmation unless --yes/-y is passed.
#
# Usage:   ./scripts/grant-superuser.sh <zitadel-sub> [--revoke] [--yes|-y]
# Revoke:  ./scripts/grant-superuser.sh <zitadel-sub> --revoke

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() { echo "Usage: $0 <zitadel-sub> [--revoke] [--yes|-y]" >&2; exit 64; }

SUB="${1:-}"
[[ -n "$SUB" ]] || usage
shift || true

OP="writes"
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --revoke) OP="deletes" ;;
        --yes|-y) ASSUME_YES=1 ;;
        *) usage ;;
    esac
done

ENV_FILE=".env"
API_URL="$(grep -E '^MARTYROLOGY_OPENFGA_API_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
STORE_ID="$(grep -E '^MARTYROLOGY_OPENFGA_STORE_ID=' "$ENV_FILE" | cut -d= -f2- || true)"
TOKEN="$(grep -E '^MARTYROLOGY_OPENFGA_API_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)"

for v in API_URL STORE_ID TOKEN; do
    [[ -n "${!v}" ]] || { echo "$v missing from $ENV_FILE — run setup-stack.sh first" >&2; exit 1; }
done

OP_LABEL="grant"
[[ "$OP" == "deletes" ]] && OP_LABEL="revoke"

# Announce the exact effect before writing — the minimum defense against a
# mistyped sub, since OpenFGA will happily accept one that names no one.
echo "About to $OP_LABEL superuser:"
echo "  user:   user:$SUB"
echo "  object: platform:martyrology"
echo "  store:  $STORE_ID"
echo "  api:    $API_URL"

if [[ $ASSUME_YES -eq 0 ]]; then
    # Read from the controlling terminal, not stdin — stdin may be
    # redirected (e.g. piped input), in which case a plain `read` would
    # silently consume that instead of prompting, and either hang or
    # auto-answer from unrelated data. `-r /dev/tty` only checks the
    # device node's permission bits, which can be true even with no
    # controlling terminal attached (open then fails with ENXIO) — so
    # actually attempt to open it and check THAT, not just the bits.
    #
    # The open attempt is confined to a subshell: a bare `exec 3</dev/tty`
    # in the main shell would rebind fd 3 (and any co-listed redirection,
    # such as 2>/dev/null) for the REST OF THE SCRIPT, not just this
    # attempt — on the success path that silently swallows all later
    # stderr, including "Aborted." and a genuine failing `curl`, which
    # would then look identical to success. The subshell's `2>/dev/null`
    # only suppresses the ENXIO probe's own diagnostic and evaporates when
    # the subshell exits either way; the actual read below opens
    # /dev/tty fresh, scoped to that one command, so fd 2 in this shell is
    # never touched.
    if ! ( exec 3</dev/tty ) 2>/dev/null; then
        echo "No controlling terminal to confirm on — pass --yes/-y to proceed non-interactively." >&2
        exit 1
    fi
    REPLY=""
    read -r -p "Proceed? [y/N] " REPLY < /dev/tty
    case "$REPLY" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "Aborted." >&2; exit 1 ;;
    esac
fi

# Built with jq rather than string interpolation: a sub containing a quote,
# backslash, or newline would otherwise produce malformed or reshaped JSON.
BODY="$(jq -n --arg op "$OP" --arg sub "$SUB" \
    '{($op): {tuple_keys: [{user: ("user:" + $sub), relation: "superuser", object: "platform:martyrology"}]}}')"

# --connect-timeout/--max-time bound this single call so an unreachable
# OpenFGA fails fast instead of hanging on curl's own defaults.
curl -sS --fail-with-body --connect-timeout 5 --max-time 15 -X POST "$API_URL/stores/$STORE_ID/write" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$BODY"

echo
echo "✓ $OP_LABEL superuser tuple for user:$SUB"
