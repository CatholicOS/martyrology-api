import re

from fastapi import APIRouter, Body, Depends, Header, Path, Request
from starlette.concurrency import run_in_threadpool

from ..auth import Identity, get_identity
from ..authz import user_ref
from ..models import (DayPatchIn, EditionCreateIn, EditionPatchIn,
                      ElogiumPatchIn, ElogiumPutIn, WriteReceiptOut)
from ..problems import ApiProblem
from ..writer.service import WriteReceipt

router = APIRouter(prefix="/editions")

MONTH = Path(pattern=r"^\d{2}$")
DAY = Path(pattern=r"^\d{2}$")

TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def valid_topic(topic: str | None = None) -> str | None:
    if topic is not None and not TOPIC_RE.fullmatch(topic):
        raise ApiProblem(422, "Invalid topic",
                         detail=f"'{topic}' is not a valid topic slug "
                                "(expected ^[a-z0-9][a-z0-9._-]{0,63}$).",
                         type_slug="invalid-topic")
    return topic


def _service(request: Request):
    svc = request.app.state.curation
    if svc is None:
        raise ApiProblem(503, "Curation not configured",
                         detail="No VCS backend is configured in this deployment.",
                         type_slug="curation-unconfigured")
    return svc


def require_relation(relation: str):
    async def dep(request: Request, edition_id: str,
                  identity: Identity | None = Depends(get_identity)) -> Identity:
        if identity is None:
            raise ApiProblem(401, "Authentication required",
                             type_slug="authentication-required")
        allowed = await request.app.state.authz.check(
            user_ref(identity), relation, edition_id)
        if not allowed:
            raise ApiProblem(403, "Forbidden",
                             detail=f"'{relation}' on '{edition_id}' denied.",
                             type_slug="forbidden")
        return identity
    return dep


def _out(receipt: WriteReceipt) -> WriteReceiptOut:
    return WriteReceiptOut(branch=receipt.branch, commit_sha=receipt.commit_sha,
                           pr_url=receipt.pr_url)


# -- elogia routes are declared before the /{month} routes: Starlette matches
# in declaration order, and "elogia" would otherwise risk being captured as
# a month segment. The month path regex (^\d{2}$) rejects "elogia" anyway,
# but declaring the more specific routes first is the robust choice.

@router.put("/{edition_id}/elogia/{canonical_id}")
async def put_elogium(request: Request, edition_id: str, canonical_id: str,
                      body: ElogiumPutIn,
                      topic: str | None = Depends(valid_topic),
                      if_match: str | None = Header(default=None),
                      identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.put_elogium, identity, edition_id, canonical_id, body.text,
        body.day, body.position, topic, if_match)
    return _out(receipt)


@router.patch("/{edition_id}/elogia/{canonical_id}")
async def patch_elogium(request: Request, edition_id: str, canonical_id: str,
                        body: ElogiumPatchIn,
                        topic: str | None = Depends(valid_topic),
                        if_match: str | None = Header(default=None),
                        identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.patch_elogium, identity, edition_id, canonical_id, body.text,
        topic, if_match)
    return _out(receipt)


@router.delete("/{edition_id}/elogia/{canonical_id}")
async def delete_elogium(request: Request, edition_id: str, canonical_id: str,
                         topic: str | None = Depends(valid_topic),
                         if_match: str | None = Header(default=None),
                         identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.delete_elogium, identity, edition_id, canonical_id, topic, if_match)
    return _out(receipt)


@router.put("/{edition_id}", status_code=201)
async def put_edition(request: Request, edition_id: str, body: EditionCreateIn,
                      topic: str | None = Depends(valid_topic),
                      identity: Identity = Depends(require_relation("can_admin"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.create_edition, identity, edition_id, body.shape, body.note, topic)
    return _out(receipt)


@router.patch("/{edition_id}")
async def patch_edition(request: Request, edition_id: str, body: EditionPatchIn,
                        topic: str | None = Depends(valid_topic),
                        identity: Identity = Depends(require_relation("can_admin"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.patch_edition, identity, edition_id, body.model_dump(), topic)
    return _out(receipt)


@router.put("/{edition_id}/{month}")
async def put_month(request: Request, edition_id: str,
                    month: str = MONTH, body: dict = Body(...),
                    topic: str | None = Depends(valid_topic),
                    if_match: str | None = Header(default=None),
                    identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    receipt = await run_in_threadpool(
        svc.put_month, identity, edition_id, int(month), body, topic, if_match)
    return _out(receipt)


@router.patch("/{edition_id}/{month}/{day}")
async def patch_day(request: Request, edition_id: str,
                    month: str = MONTH, day: str = DAY,
                    body: DayPatchIn = Body(...),
                    topic: str | None = Depends(valid_topic),
                    if_match: str | None = Header(default=None),
                    identity: Identity = Depends(require_relation("can_edit"))):
    svc = _service(request)
    payload = {k: getattr(body, k) for k in body.model_fields_set}
    receipt = await run_in_threadpool(
        svc.patch_day, identity, edition_id, int(month), int(day),
        payload, topic, if_match)
    return _out(receipt)
