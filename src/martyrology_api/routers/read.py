from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from ..auth import Identity, get_identity
from ..authz import user_ref
from ..grammar import ElogiaRequest, parse_elogia_path
from ..licensing import is_restricted, redact, texts_allowed
from ..models import (DayContentOut, DayOut, EditionMetadataOut,
                      EditionPlacementOut, ElogiumOut, EulogyOut, MetadataOut,
                      MonthOut)
from ..problems import ApiProblem
from ..registry import is_canonical_id, slug_of
from ..resolver import EditionUnavailableError, Resolution, resolve
from ..store import DayData, Elogium

router = APIRouter()


def _edition_meta_out(request: Request, edition_id: str) -> EditionMetadataOut:
    e = request.app.state.registry.editions[edition_id]
    return EditionMetadataOut(nature=e.nature, language=e.language, scope=e.scope,
                              promulgated=e.promulgated, decree=e.decree,
                              predecessor=e.predecessor, successor=e.successor,
                              translation_of=e.translation_of)


def elogium_out(e: Elogium) -> ElogiumOut:
    return ElogiumOut(id=e.id, entry=e.entry, asterisk=e.asterisk,
                      unnumbered=e.unnumbered,
                      anchor_day=f"{e.anchor_month:02d}-{e.anchor_day:02d}",
                      text=e.text)


def _day_content(d: DayData) -> DayContentOut:
    return DayContentOut(titulus=d.titulus,
                         elogia=[elogium_out(e) for e in d.elogia],
                         conclusio=d.conclusio)


def _explicit_edition(request: Request, edition_id: str) -> Resolution:
    registry = request.app.state.registry
    store = request.app.state.store
    if edition_id not in registry.editions:
        raise ApiProblem(404, "Unknown edition",
                         detail=f"'{edition_id}' is not a registered martyrology edition.",
                         type_slug="unknown-edition")
    if edition_id not in store.available():
        raise EditionUnavailableError(edition_id)
    return Resolution(edition_id=edition_id, resolved_from=None)


def resolve_request(request: Request, req: ElogiaRequest,
                    locale_q: str | None, edition_q: str | None) -> Resolution:
    edition = req.edition or edition_q
    if edition:
        return _explicit_edition(request, edition)
    locale = locale_q
    if locale is None:
        al = request.headers.get("accept-language")
        if al:
            locale = al.split(",")[0].split(";")[0].strip() or None
    return resolve(request.app.state.registry, request.app.state.store.available(),
                   nation=req.nation, year=req.year, locale=locale)


async def _draft_months(request: Request, identity: Identity | None,
                        edition_id: str, month: int) -> dict[int, DayData] | None:
    branch = request.headers.get("x-curation-branch")
    if not branch or identity is None:
        return None
    svc = getattr(request.app.state, "curation", None)
    if svc is None:
        return None
    if not await request.app.state.authz.check(
            user_ref(identity), "can_edit", edition_id):
        return None
    return await run_in_threadpool(svc.read_month_draft, edition_id, month, branch)


@router.get("/elogia/{rest:path}")
async def get_elogia(rest: str, request: Request,
                     locale: str | None = None, edition: str | None = None,
                     identity: Identity | None = Depends(get_identity)):
    req = parse_elogia_path(rest)
    resolution = resolve_request(request, req, locale, edition)
    store = request.app.state.store
    metadata = MetadataOut(edition=resolution.edition_id,
                           edition_metadata=_edition_meta_out(request, resolution.edition_id),
                           resolved_from=resolution.resolved_from,
                           month=req.month, day=req.day)

    allowed = await texts_allowed(request, identity, resolution.edition_id)
    settings = request.app.state.settings
    if is_restricted(resolution.edition_id, settings):
        if allowed:
            request.state.cache_private = True
        else:
            metadata.access = "restricted-texts"
            metadata.access_info = settings.access_info_url

    months = await _draft_months(request, identity, resolution.edition_id, req.month)
    if months is None:
        months = store.month(resolution.edition_id, req.month)
    else:
        # A genuine draft month is on the response: it must never be
        # shared-cached, since it reflects a specific curator's branch.
        request.state.cache_private = True

    if req.day is None:
        contents = {f"{d:02d}": _day_content(v) for d, v in sorted(months.items())}
        if not allowed:
            for c in contents.values():
                redact(c.elogia)
        return MonthOut(metadata=metadata, days=contents)

    day_data = months.get(req.day)
    if day_data is None:
        raise ApiProblem(404, "No entries for this day",
                         detail=f"Edition '{resolution.edition_id}' has no entries "
                                f"for {req.month:02d}-{req.day:02d}.",
                         type_slug="unknown-day")

    if req.slug is None:
        c = _day_content(day_data)
        if not allowed:
            redact(c.elogia)
        return DayOut(metadata=metadata, titulus=c.titulus,
                      elogia=c.elogia, conclusio=c.conclusio)

    hit = next((e for e in day_data.elogia if slug_of(e.id) == req.slug), None)
    if hit is None:
        raise ApiProblem(404, "Eulogy not on this day",
                         detail=f"No eulogy '{req.slug}' printed under "
                                f"{req.month:02d}-{req.day:02d} in "
                                f"'{resolution.edition_id}'.",
                         type_slug="unknown-eulogy")
    elogia = [elogium_out(hit)]
    if not allowed:
        redact(elogia)
    return DayOut(metadata=metadata, titulus=day_data.titulus,
                  elogia=elogia, conclusio=day_data.conclusio)


@router.get("/elogium/{canonical_id}")
async def get_elogium(canonical_id: str, request: Request, editions: str | None = None,
                      identity: Identity | None = Depends(get_identity)):
    registry = request.app.state.registry
    store = request.app.state.store
    settings = request.app.state.settings
    if not is_canonical_id(canonical_id) or canonical_id not in registry.entries:
        raise ApiProblem(404, "Unknown canonical id",
                         detail=f"'{canonical_id}' is not in the CRMEDR registry.",
                         type_slug="unknown-id")
    entry = registry.entries[canonical_id]
    wanted = set(editions.split(",")) if editions else None
    placements = {}
    for p in store.placements(canonical_id):
        if wanted is not None and p.edition_id not in wanted:
            continue
        text = p.text
        allowed = await texts_allowed(request, identity, p.edition_id)
        if not allowed:
            text = None
        if is_restricted(p.edition_id, settings):
            if allowed:
                request.state.cache_private = True
        placements[p.edition_id] = EditionPlacementOut(
            day_printed=p.day_printed, entry=p.entry, asterisk=p.asterisk,
            unnumbered=p.unnumbered, text=text)
    subject = {loc: registry.subjects(loc)[canonical_id]
               for loc in ("la", "en", "it")
               if canonical_id in registry.subjects(loc)}
    return EulogyOut(id=canonical_id, subject=subject,
                     anchor_day=f"{entry.month:02d}-{entry.day:02d}",
                     deprecated=entry.deprecated, editions=placements)
