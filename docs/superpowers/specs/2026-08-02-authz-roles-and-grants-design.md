# Zitadel role gate, platform superuser, and grant endpoint

**Date:** 2026-08-02
**Status:** Design — approved decisions recorded, open questions marked
**Repos touched:** `CatholicOS/martyrology-api`, `CatholicOS/cdcf-infra`

## Problem

The API ships a governance-body-scoped OpenFGA model and consults it on
every curation write and on reads of restricted editions. Nothing else is
in place:

- No user holds any OpenFGA tuple, so every curation write is denied and
  every restricted text is redacted.
- Nothing can create the first grant. The model is seeded structurally by
  `cdcf-infra`; there is no path for a human to grant a human anything.
- The MartyrologyAPI Zitadel project defines no roles, so the API cannot
  distinguish a curator from any other authenticated principal in the
  umbrella instance.

This design adds the three pieces that make the model operable: a coarse
Zitadel role gate, a platform superuser expressed in OpenFGA, and a grant
endpoint scoped to governance bodies.

## Blocking defect found while designing

`Authz.check()` sends no `Authorization` header
(`src/martyrology_api/authz.py:33`), but the deployed OpenFGA runs with
`OPENFGA_AUTHN_METHOD=preshared`. Verified against production:

```
POST https://authz.catholicdigitalcommons.org/stores/01KZ.../check
→ 401 {"code":"bearer_token_missing","message":"missing bearer token"}
```

Every `check()` therefore takes the `resp.status_code != 200` path and
returns `False`. Two consequences in the live deployment today:

1. All curation writes return 403 for every caller, and would continue to
   after grants exist.
2. Restricted editions (the three 2004-family editions) have their texts
   redacted for every caller, including correctly licensed ones —
   `licensing.py:17` is the same dead check.

Fixing this is a precondition for the rest of the design; it is Task 1.

## Decisions

### D1 — Roles are a coarse gate, never a bypass

Three roles on the MartyrologyAPI Zitadel project:

| Key | Display name | Enforced by this design |
|---|---|---|
| `admin` | System Administrator | yes — satisfies the curation gate |
| `martyrology_editor` | Martyrology Editor | yes — satisfies the curation gate |
| `developer` | Developer (API consumer) | no — see D6 |

Every curation write route requires the caller to hold `martyrology_editor`
**or** `admin`. That check runs before OpenFGA is consulted and is purely a
population filter: it says the caller is a curator of this property, not
which editions they may touch. OpenFGA then makes the actual decision,
unchanged.

`admin` grants **no** OpenFGA bypass. This is a deliberate divergence from
LiturgicalCalendarAPI, where
`OpenFgaAuthorizationMiddleware.php:141` short-circuits every FGA check for
role holders. A bypass means a mis-issued role silently defeats the whole
governance model and leaves no per-resource record of who could do what.
Platform-wide authority is expressed in OpenFGA instead (D3), where it is a
tuple that can be listed, audited, and revoked.

The role gate does **not** apply to the read path. A licensed reader of a
restricted edition is not a curator and will not hold a project role;
gating `can_read_texts` on a role would deny exactly the population the
licensing check exists to serve.

### D2 — Read the project-scoped roles claim

Introspection was verified against the live Zitadel by granting a
throwaway role to the automation user and re-introspecting the same token.
Both claims appear:

```json
"urn:zitadel:iam:org:project:roles":                    { "_probe_role": { "<orgId>": "<orgDomain>" } },
"urn:zitadel:iam:org:project:384518610174869507:roles": { "_probe_role": { "<orgId>": "<orgDomain>" } }
```

The API reads the **project-scoped** variant,
`urn:zitadel:iam:org:project:<PROJECT_ID>:roles`. The generic claim can
carry roles the principal holds in other projects of the same umbrella
instance; the project-scoped one cannot. This closes the tenant boundary
that `identity()` currently lacks — the same gap `cdcf-website/lib/auth.ts`
documents when it notes that "the umbrella Zitadel happily authorizes any
instance-wide user against cdcf-website's client_id".

Role keys are the object keys of the claim, as in LitCal and cdcf-website.
A missing claim, a non-object claim, or a claim under a different project
ID all yield the empty set, which fails the gate.

This requires a new `MARTYROLOGY_ZITADEL_PROJECT_ID` setting. When auth is
enabled and the project ID is unset, the role set is always empty and every
curation write returns 403 — fail-closed, and diagnosable from the
`missing-role` problem type rather than a generic denial.

No fallback to the Zitadel Management API. LitCal added one
(`OidcAuthMiddleware.php:412+`) because service accounts using the JWT
Profile grant receive no roles claim. Martyrology has no service-account
curators; if that changes, it is a separate piece of work.

### D3 — Platform superuser as an OpenFGA type

Add a `platform` type and hang governance bodies off it:

```
type platform
  relations
    define superuser: [user]

type governance_body
  relations
    define on_platform: [platform]
    define admin:  [user] or superuser from on_platform
    define editor: [user] or admin
    define reader: [user] or editor
```

A platform superuser becomes `admin` on every body that points at the
platform, and through the existing `tupleToUserset` relations inherits
`can_admin` / `can_edit` / `can_read_texts` on every edition those bodies
govern. The `edition` type is unchanged.

This is the whole superuser mechanism. There is no code path that treats
any principal as privileged; superuser status is three tuples away from any
other grant and is answered by the same `check()` call.

New structural tuples, seeded by `cdcf-infra` alongside the existing eight:

```
platform:martyrology  ← governance_body:editio_typica          on_platform
platform:martyrology  ← governance_body:cei                    on_platform
platform:martyrology  ← governance_body:unassigned_en_translatio  on_platform
```

Note that this gives the superuser admin over `unassigned_en_translatio`,
which today has no members. That is intended: it is the only way anyone can
act on the two English editions while their governance is undecided.

### D4 — Grant endpoint, scoped to governance bodies only

```
GET    /admin/permissions?governance_body=<id>[&relation=<r>]
POST   /admin/permissions        {"user": "<sub>", "governance_body": "<id>", "relation": "<r>"}
DELETE /admin/permissions?user=<sub>&governance_body=<id>&relation=<r>
GET    /admin/permissions/check?user=<sub>&governance_body=<id>&relation=<r>
```

The path mirrors LitCal's `/admin/permissions` for consistency across CDCF
properties. `DELETE` takes query parameters rather than LitCal's request
body; a body on DELETE is poorly supported by intermediaries and buys
nothing here.

`relation` is one of `admin`, `editor`, `reader` — the three direct
relations on `governance_body`.

**The object type is not a parameter.** It is always `governance_body`.
Two object types are deliberately unreachable through this endpoint:

- `edition` — editions draw their permissions structurally from
  `governed_by`. If the API could write edition tuples, a body admin could
  re-point an edition at a body they control and take it over. Edition
  wiring stays in `cdcf-infra`.
- `platform` — if the API could write platform tuples, any body admin
  could mint themselves a superuser. Superuser grants stay in `cdcf-infra`,
  performed out-of-band.

Expressing the allowlist in the route shape rather than in a validator
means there is no string to get wrong.

**Authorization for the endpoint:** the caller must be authenticated and
must hold the OpenFGA `admin` relation on the target governance body. That
is it — no Zitadel role gate here, and no global-admin bypass.

This diverges from D1's "roles gate writes", and from LitCal, which accepts
*either* the global `admin` role *or* resource admin. The reason is
delegation: the model exists so that CEI governs the Italian edition and
the Congregation governs the typical editions. A body admin who is not
platform staff will hold no project role. Gating grants on a platform-wide
role would centralise exactly what the governance model decentralises.
Meanwhile FGA `admin` on the body is strictly more precise than any role,
so nothing is lost. The platform superuser reaches every body through D3
and so retains full authority without a special case.

**Idempotency:** granting a tuple that already exists returns 200, and
revoking one that does not exist returns 200. OpenFGA reports both as
`400 write_failed_due_to_invalid_input`; the endpoint maps that specific
condition to success. LitCal's Postgres outbox and idempotency keys are not
replicated — the API has no database, and the tuple set is itself the
durable state.

**Audit:** every grant and revoke emits one structured log line carrying
the actor's subject, the target user, the body, the relation, the action,
and the outcome. The authoritative record of who holds what is the tuple
set, readable through `GET /admin/permissions`.

### D5 — `Authz` gains write and read operations

`Authz` currently only calls `/check`. It gains:

- `write(user, relation, object)` → `POST /stores/{id}/write` with `writes`
- `delete(user, relation, object)` → the same endpoint with `deletes`
- `read(object, relation=None)` → `POST /stores/{id}/read`

All four operations, `check()` included, send
`Authorization: Bearer <MARTYROLOGY_OPENFGA_API_TOKEN>`.

This widens the API's blast radius from "can only ask questions" to "can
modify authorization", which is the price of a grant endpoint. D4's
structural scoping is what keeps that widening bounded: the token permits
arbitrary tuple writes at the OpenFGA layer, but the only tuples any route
can express are `governance_body` memberships.

The token name matches LitCal's `OPENFGA_API_TOKEN` convention, under this
project's prefix: `MARTYROLOGY_OPENFGA_API_TOKEN`. `authz_enabled` is
extended to require it, so a deployment that sets the URL and store but
forgets the token reports as disabled rather than silently denying
everything — the failure mode this design was written to end.

### D6 — `developer` role is defined but enforces nothing

The role is created in Zitadel so the vocabulary exists across CDCF
properties, and because issuing it later to already-onboarded principals is
more disruptive than defining it now. No route consults it in this design.

The candidate use is the one LitCal built it for: gating API-consumer
features such as key issuance and quota. Martyrology has none of that. When
it does, that work defines the enforcement; inventing it here would be
guessing.

## Rollout

The order matters. The model change produces a new authorization model ID,
and `MARTYROLOGY_OPENFGA_MODEL_ID` is pinned in `/etc/martyrology/api.env`.
Pinning to the old model is safe — it fails closed — but superuser
inheritance will not resolve until the pin is updated.

1. **cdcf-infra, Zitadel:** create the three roles on project
   `384518610174869507`. Grant `admin` to the operator's own subject.
2. **cdcf-infra, OpenFGA:** extend `auth/models/Martyrology.json` with the
   `platform` type and `on_platform` relation; add the three `on_platform`
   tuples to `Martyrology.tuples.json`; upload the model and seed. Record
   the new model ID.
3. **cdcf-infra, out-of-band:** write
   `user:<operator-sub> superuser platform:martyrology`. This is the
   bootstrap grant and the only one made outside the API.
4. **VPS:** add `MARTYROLOGY_OPENFGA_API_TOKEN` and
   `MARTYROLOGY_ZITADEL_PROJECT_ID` to `/etc/martyrology/api.env`; update
   `MARTYROLOGY_OPENFGA_MODEL_ID` to the ID from step 2.
5. **martyrology-api:** implement, release, deploy.
6. **Verify:** as the operator, `GET /admin/permissions?governance_body=cei`
   returns 200; a restricted-edition read returns text rather than
   `null`; a curation write against an edition of a governed body
   succeeds; the same call from a principal holding no project role
   returns 403 `missing-role`.

There is no lockout risk in enforcing the role gate on first deploy,
because curation is already denied for everyone today.

## Changes by file

**martyrology-api**

| File | Change |
|---|---|
| `src/martyrology_api/config.py` | add `openfga_api_token`, `zitadel_project_id`; `authz_enabled` requires the token |
| `src/martyrology_api/auth.py` | `Identity` gains `roles: frozenset[str]`; `Authenticator` takes `project_id` and parses the project-scoped claim |
| `src/martyrology_api/authz.py` | bearer header on every call; add `write`, `delete`, `read` |
| `src/martyrology_api/routers/curation.py` | `require_relation` checks the role gate before the FGA check; new `missing-role` problem type |
| `src/martyrology_api/routers/admin.py` | new — the grant endpoint |
| `src/martyrology_api/app.py` | mount the admin router; pass the new settings through |

**cdcf-infra**

| File | Change |
|---|---|
| `auth/models/Martyrology.json` | `platform` type, `on_platform` relation |
| `auth/models/Martyrology.tuples.json` | three `on_platform` tuples |
| `auth/setup-zitadel.sh` | create the three project roles in `do_provision_martyrology` |
| `auth/handoffs/martyrology.md` | document roles, superuser bootstrap, grant endpoint; correct the standing claim that `admin` "is the intended role-granting authority", which nothing implemented |

## Testing

Unit, with a mocked transport — the suite has no live Zitadel or OpenFGA:

- Role extraction: claim present; absent; not an object; present under a
  *different* project ID (must yield the empty set); multiple roles.
- Role gate: a caller with no role gets 403 `missing-role` and the FGA
  check is never issued; `martyrology_editor` and `admin` each pass;
  the read path is unaffected by roles.
- `Authz`: every method sends the bearer header; a 401 from OpenFGA yields
  `False` from `check` and an error from `write`/`delete` rather than
  silent success.
- Grant endpoint: 401 unauthenticated; 403 when not body admin; 200 when
  body admin; 422 on a relation outside the allowlist; duplicate grant and
  absent revoke both 200.
- Route shape: no route accepts an object type, so no test can construct a
  request that writes an `edition` or `platform` tuple.

Superuser inheritance is a property of the OpenFGA model, not of this
codebase; it is verified in `cdcf-infra` when the model is uploaded, and
again in rollout step 6.

## Out of scope

- Wiring `can_read_texts` to anything beyond the existing restricted-edition
  check. The licensing gate's static `restricted_editions` list stays as-is.
- Self-service access requests. LitCal has an `AccessRequestRepository`
  flow where users request roles and admins approve; that needs a database
  Martyrology does not have.
- Anything enforcing `developer` (D6).
- Service-account curators and the Management API role fallback (D2).

## Open questions

1. **D4's authorization rule** is the one place this design consciously
   departs from both LitCal and from D1's "roles gate everything".
   Confirm that a governance-body admin who holds no project role should be
   able to manage grants within their own body.
2. **English editions.** `unassigned_en_translatio` remains memberless.
   The superuser can act on it, so nothing is blocked, but the placeholder
   should not become permanent.
