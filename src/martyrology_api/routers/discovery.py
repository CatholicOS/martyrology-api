from fastapi import APIRouter, Request

from ..config import Settings
from ..models import (
    AvailabilityOut,
    CatalogEntryOut,
    CatalogOut,
    EditionOut,
    EditionsOut,
    GovernanceOut,
    promulgation_dict,
    scope_dict,
)
from ..problems import ApiProblem
from ..registry import EditionMeta
from ..store import Store

router = APIRouter()


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
    if edition_id in settings.restricted_set:
        return "restricted-texts"
    return "public" if edition_id in available else "unavailable"


def _edition_out(
    e: EditionMeta, available: set[str], settings: Settings, store: Store
) -> EditionOut:
    status = availability_status(e.id, available, settings)
    note = None
    if status == "restricted-texts":
        note = f"Copyrighted texts; an approved API key is required. See {settings.access_info_url}"
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
    )


@router.get("/editions")
def get_editions(request: Request) -> EditionsOut:
    registry = request.app.state.registry
    store = request.app.state.store
    available = store.available()
    settings = request.app.state.settings
    eds = sorted(registry.editions.values(), key=lambda e: (e.promulgated_year, e.id))
    return EditionsOut(editions=[_edition_out(e, available, settings, store) for e in eds])


@router.get("/elogia")
def get_catalog(request: Request, locale: str = "la", edition: str | None = None) -> CatalogOut:
    registry = request.app.state.registry
    store = request.app.state.store
    if edition is not None and edition not in registry.editions:
        raise ApiProblem(
            404,
            "Unknown edition",
            detail=f"'{edition}' is not a registered martyrology edition.",
            type_slug="unknown-edition",
        )
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
