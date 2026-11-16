import threading
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request, Response

from ..auth import Identity, get_identity
from ..caching import declare_public_cache
from ..config import Settings
from ..licensing import texts_allowed
from ..models import (
    AccessOut,
    AvailabilityOut,
    CatalogEntryOut,
    CatalogOut,
    EditionAccessOut,
    EditionOut,
    EditionsOut,
    GovernanceOut,
    SourceOut,
    promulgation_dict,
    scope_dict,
)
from ..problems import ApiProblem
from ..registry import EditionMeta, Registry
from ..store import Store

router = APIRouter(dependencies=[Depends(declare_public_cache)])


def governance_for(scope: str) -> GovernanceOut:
    if scope == "universal":
        return GovernanceOut(
            governing_body="Dicastery for Divine Worship and the Discipline of the Sacraments",
            type="dicastery",
        )
    if scope == "IT":
        return GovernanceOut(
            governing_body="Conferenza Episcopale Italiana", type="bishops_conference", nation="IT"
        )
    return GovernanceOut(
        governing_body=f"Bishops' Conference ({scope})", type="bishops_conference", nation=scope
    )


def availability_status(edition_id: str, available: set[str], settings: Settings) -> str:
    if edition_id in settings.gated_set:
        return "restricted-texts"
    return "public" if edition_id in available else "unavailable"


def _edition_out(
    e: EditionMeta, available: set[str], settings: Settings, store: Store
) -> EditionOut:
    status = availability_status(e.id, available, settings)
    note = None
    if status == "restricted-texts":
        need = (
            "sign in to read them"
            if settings.restricted_texts_access == "authenticated"
            else "an approved access grant is required"
        )
        note = f"Copyrighted texts; {need}. See {settings.access_info_url}"
    elif status == "unavailable":
        note = "Registered in the CLBDR but no texts are attached in this deployment."
    return EditionOut(
        edition_id=e.id,
        year=e.promulgated_year,
        nature=e.nature,
        scope=scope_dict(e.scope),
        locale=e.language,
        promulgation=promulgation_dict(e.decree, e.promulgated),
        predecessor=e.predecessor,
        successor=e.successor,
        governance=governance_for(e.scope),
        availability=AvailabilityOut(status=status, note=note),
        aligned=store.aligned(e.id),
        source=None if (src := store.source(e.id)) is None else SourceOut.model_validate(src),
    )


@router.get("/editions")
def get_editions(request: Request) -> EditionsOut:
    registry = request.app.state.registry
    store = request.app.state.store
    available = store.available()
    settings = request.app.state.settings
    eds = sorted(registry.editions.values(), key=lambda e: (e.promulgated_year, e.id))
    return EditionsOut(editions=[_edition_out(e, available, settings, store) for e in eds])


@router.get("/access")
async def get_access(
    request: Request, identity: Identity | None = Depends(get_identity)
) -> AccessOut:
    """Which editions' texts the caller may read. The answer depends on the
    caller, so it is never shared-cacheable: cache_private overrides this
    router's public default."""
    request.state.cache_private = True
    registry = request.app.state.registry
    return AccessOut(
        editions={
            edition_id: EditionAccessOut(
                can_read_texts=await texts_allowed(request, identity, edition_id)
            )
            for edition_id in sorted(registry.editions)
        }
    )


@router.get("/elogia", response_model=CatalogOut)
def get_catalog(request: Request, locale: str = "la", edition: str | None = None) -> Response:
    registry = request.app.state.registry
    if edition is not None and edition not in registry.editions:
        raise ApiProblem(
            404,
            "Unknown edition",
            detail=f"'{edition}' is not a registered martyrology edition.",
            type_slug="unknown-edition",
        )
    # The catalog depends only on the edition and the language its subjects are given in, and the
    # registry and the texts never change while the service runs: each one is built and serialized
    # once (about a megabyte, 6000+ entries) and then served as is. The key is bounded: the
    # registered editions (or none) by the registry's languages (or none, for an unknown one).
    lang = locale.split("-")[0].lower()
    key = (edition, lang if lang in registry.locales() else None)
    cache: CatalogCache = request.app.state.catalog_cache
    body = cache.get(
        key,
        lambda: (
            _catalog(registry, request.app.state.store, locale, edition).model_dump_json().encode()
        ),
    )
    return Response(content=body, media_type="application/json")


class CatalogCache:
    """Serialized catalogs by (edition, language), each built at most once.

    A hit reads the dict without locking. A miss takes that key's lock and looks again before
    building, so a burst of requests for a catalog not yet built (just after a start, say) builds
    it once while the rest wait for it; other keys build in parallel.
    """

    def __init__(self) -> None:
        self._bodies: dict[tuple[str | None, str | None], bytes] = {}
        self._locks: dict[tuple[str | None, str | None], threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, key: tuple[str | None, str | None], build: Callable[[], bytes]) -> bytes:
        body = self._bodies.get(key)
        if body is not None:
            return body
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            body = self._bodies.get(key)
            if body is None:
                body = build()
                self._bodies[key] = body
        return body

    def keys(self) -> set[tuple[str | None, str | None]]:
        return set(self._bodies)


def _catalog(registry: Registry, store: Store, locale: str, edition: str | None) -> CatalogOut:
    subjects = registry.subjects(locale)
    items = []
    for e in sorted(
        registry.entries.values(),
        key=lambda x: (x.month, x.day, x.entry if x.entry is not None else float("inf"), x.id),
    ):
        item = CatalogEntryOut(
            id=e.id,
            subject=subjects.get(e.id),
            anchor_day=f"{e.month:02d}-{e.day:02d}",
            deprecated=e.deprecated,
        )
        if edition is not None:
            hit = next((p for p in store.placements(e.id) if p.edition_id == edition), None)
            item.present = hit is not None
            if hit:
                item.day_printed = hit.day_printed
                item.entry = hit.entry
        items.append(item)
    return CatalogOut(elogia=items)
