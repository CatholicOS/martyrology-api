import logging
import re

from fastapi import APIRouter, Depends, Query, Request

from ..auth import Identity, get_identity
from ..authz import AuthzError, user_ref
from ..models import GrantIn, PermissionCheckOut, PermissionListOut, PermissionOut
from ..problems import ApiProblem

router = APIRouter(prefix="/admin/permissions", tags=["admin"])
log = logging.getLogger(__name__)

VALID_RELATIONS = frozenset({"admin", "editor", "reader"})
BODY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")

# OpenFGA reports both "tuple already exists" and "tuple does not exist"
# with this code. The relation is allowlisted and the object type is fixed
# before the call, so by the time it is observed it can only mean one of
# those two — both of which are the caller's desired end state.
IDEMPOTENT_CODE = "write_failed_due_to_invalid_input"


async def _authenticated(identity: Identity | None = Depends(get_identity)) -> Identity:
    if identity is None:
        raise ApiProblem(401, "Authentication required", type_slug="authentication-required")
    return identity


def _invalid(detail: str) -> ApiProblem:
    return ApiProblem(422, "Invalid request", detail=detail, type_slug="invalid-grant")


def _body_ref(body_id: str) -> str:
    if not BODY_RE.fullmatch(body_id):
        raise _invalid(f"'{body_id}' is not a valid governance body id.")
    return f"governance_body:{body_id}"


def _normalise_user(user: str) -> str:
    bare = user[5:] if user.startswith("user:") else user
    if not USER_RE.fullmatch(bare):
        raise _invalid(f"'{user}' is not a valid user id.")
    return f"user:{bare}"


def _relation(relation: str) -> str:
    if relation not in VALID_RELATIONS:
        raise _invalid(
            f"'{relation}' is not grantable. Valid relations: {', '.join(sorted(VALID_RELATIONS))}."
        )
    return relation


async def _require_body_admin(request: Request, identity: Identity, body_ref: str) -> None:
    allowed = await request.app.state.authz.check_object(user_ref(identity), "admin", body_ref)
    if not allowed:
        raise ApiProblem(
            403,
            "Forbidden",
            detail=f"No admin permission on '{body_ref}'.",
            type_slug="forbidden",
        )


async def _mutate(request: Request, identity: Identity, op: str, ref: str, rel: str, obj: str):
    fn = request.app.state.authz.write if op == "grant" else request.app.state.authz.delete
    outcome = "ok"
    try:
        await fn(ref, rel, obj)
    except AuthzError as exc:
        if exc.code == IDEMPOTENT_CODE:
            # OpenFGA reports both "tuple already exists" (grant) and
            # "tuple does not exist" (revoke) with this code. That is
            # consistent with the caller's desired end state, but not
            # proof of it — confirm the postcondition rather than assume
            # it. Confirm against a *direct* tuple read, not check_object:
            # check_object evaluates the computed relation, but write/
            # delete manipulate direct tuples, and the model unions them
            # (e.g. editor: [user] or admin) — a computed check can
            # disagree with the direct-tuple state in either direction.
            # read_tuples raises AuthzError on infrastructure failure
            # (rather than failing closed like check_object does), so a
            # failure to confirm is itself treated as unconfirmed.
            desired_present = op == "grant"
            try:
                tuples = await request.app.state.authz.read_tuples(obj, rel)
                present = any(t.get("user") == ref for t in tuples)
                outcome = "noop" if present == desired_present else "error:unconfirmed"
            except AuthzError:
                outcome = "error:unconfirmed"
        else:
            outcome = f"error:{exc.code or exc.status}"
        if outcome != "noop":
            log.warning(
                "permission %s by %s: %s %s on %s -> %s",
                op,
                user_ref(identity),
                ref,
                rel,
                obj,
                outcome,
            )
            raise ApiProblem(
                502,
                "Authorization store error",
                detail="The authorization store rejected the change.",
                type_slug="authz-store-error",
            ) from exc
    log.info(
        "permission %s by %s: %s %s on %s -> %s",
        op,
        user_ref(identity),
        ref,
        rel,
        obj,
        outcome,
    )


@router.get("", response_model=PermissionListOut)
async def list_permissions(
    request: Request,
    governance_body: str = Query(...),
    relation: str | None = Query(default=None),
    identity: Identity = Depends(_authenticated),
):
    request.state.cache_private = True
    obj = _body_ref(governance_body)
    rel = _relation(relation) if relation is not None else ""
    await _require_body_admin(request, identity, obj)
    try:
        tuples = await request.app.state.authz.read_tuples(obj, rel)
    except AuthzError as exc:
        log.warning(
            "permission list by %s on %s -> error:%s",
            user_ref(identity),
            obj,
            exc.code or exc.status,
        )
        raise ApiProblem(
            502,
            "Authorization store error",
            detail="The authorization store could not be read.",
            type_slug="authz-store-error",
        ) from exc
    return PermissionListOut(
        governance_body=governance_body,
        permissions=[
            PermissionOut(
                user=t.get("user", ""),
                governance_body=governance_body,
                relation=t.get("relation", ""),
            )
            for t in tuples
            if t.get("relation") in VALID_RELATIONS
        ],
    )


@router.post("", response_model=PermissionOut)
async def grant_permission(
    request: Request,
    body: GrantIn,
    identity: Identity = Depends(_authenticated),
):
    obj = _body_ref(body.governance_body)
    rel = _relation(body.relation)
    ref = _normalise_user(body.user)
    await _require_body_admin(request, identity, obj)
    await _mutate(request, identity, "grant", ref, rel, obj)
    return PermissionOut(user=ref, governance_body=body.governance_body, relation=rel)


@router.delete("", response_model=PermissionOut)
async def revoke_permission(
    request: Request,
    user: str = Query(...),
    governance_body: str = Query(...),
    relation: str = Query(...),
    identity: Identity = Depends(_authenticated),
):
    obj = _body_ref(governance_body)
    rel = _relation(relation)
    ref = _normalise_user(user)
    await _require_body_admin(request, identity, obj)
    await _mutate(request, identity, "revoke", ref, rel, obj)
    return PermissionOut(user=ref, governance_body=governance_body, relation=rel)


@router.get("/check", response_model=PermissionCheckOut)
async def check_permission(
    request: Request,
    user: str = Query(...),
    governance_body: str = Query(...),
    relation: str = Query(...),
    identity: Identity = Depends(_authenticated),
):
    request.state.cache_private = True
    obj = _body_ref(governance_body)
    rel = _relation(relation)
    ref = _normalise_user(user)
    await _require_body_admin(request, identity, obj)
    allowed = await request.app.state.authz.check_object(ref, rel, obj)
    return PermissionCheckOut(
        user=ref, governance_body=governance_body, relation=rel, allowed=allowed
    )
