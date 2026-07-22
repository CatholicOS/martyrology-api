from dataclasses import dataclass

from .problems import ApiProblem
from .registry import EditionMeta, Registry


class PreFirstEditionError(ApiProblem):
    def __init__(self, year: int):
        super().__init__(
            404, "No edition in force",
            detail=f"No Roman Martyrology edition was promulgated on or before {year}; "
                   "the first typical edition is 1584.",
            type_slug="pre-first-edition", editions_url="/api/v1/editions")


class EditionUnavailableError(ApiProblem):
    def __init__(self, edition_id: str):
        super().__init__(
            404, "Edition texts unavailable",
            detail=f"Edition '{edition_id}' is registered but its texts are not "
                   "attached in this deployment.",
            type_slug="edition-unavailable", edition=edition_id,
            editions_url="/api/v1/editions")


@dataclass
class Resolution:
    edition_id: str
    resolved_from: dict | None


def _primary(lang: str) -> str:
    return lang.split("-")[0].lower()


def _scoped_candidates(registry: Registry, nation: str | None) -> list[EditionMeta]:
    candidates = list(registry.editions.values())
    if nation:
        national = [e for e in candidates if e.scope == nation]
        return national or [e for e in candidates if e.scope == "universal"]
    return [e for e in candidates if e.scope == "universal"]


def _apply_locale(registry: Registry, candidates: list[EditionMeta],
                  locale: str | None) -> list[EditionMeta]:
    if not locale:
        return candidates
    wanted = [e for e in candidates if _primary(e.language) == _primary(locale)]
    if wanted:
        return wanted
    # Widen, but never past universal scope: a locale request for
    # nation X must never resolve to an edition scoped to nation Y.
    universal = [e for e in registry.editions.values() if e.scope == "universal"]
    wanted_universal = [e for e in universal if _primary(e.language) == _primary(locale)]
    if wanted_universal:
        return wanted_universal
    # Fall back to universal-scoped "la" editions
    return [e for e in universal if _primary(e.language) == "la"]


def resolve(registry: Registry, available: set[str],
            nation: str | None = None, year: int | None = None,
            locale: str | None = None) -> Resolution:
    candidates = _scoped_candidates(registry, nation)
    candidates = _apply_locale(registry, candidates, locale)
    if not locale:
        # Translations are only eligible for resolution when the requested
        # locale actively selects their language; without a locale they must
        # never outrank a non-translation edition.
        candidates = [e for e in candidates if e.nature != "translatio"]

    if year is not None:
        year_filtered = [e for e in candidates if e.promulgated_year <= year]
        if not year_filtered and nation:
            # The national candidate set was emptied by the year filter;
            # national editions are preferred but not required, so retry
            # against universal-scoped candidates. `nation` is still
            # recorded in resolved_from below.
            universal_candidates = _apply_locale(
                registry, [e for e in registry.editions.values() if e.scope == "universal"],
                locale)
            if not locale:
                universal_candidates = [e for e in universal_candidates
                                        if e.nature != "translatio"]
            year_filtered = [e for e in universal_candidates if e.promulgated_year <= year]
        if not year_filtered:
            raise PreFirstEditionError(year)
        candidates = year_filtered

    def rank(e: EditionMeta):
        return (e.promulgated_year, e.nature != "translatio", e.id)

    winner = max(candidates, key=rank)
    if winner.id not in available:
        raise EditionUnavailableError(winner.id)

    resolved_from = {k: v for k, v in
                     (("nation", nation), ("year", year), ("locale", locale))
                     if v is not None}
    return Resolution(edition_id=winner.id, resolved_from=resolved_from)
