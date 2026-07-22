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


def resolve(registry: Registry, available: set[str],
            nation: str | None = None, year: int | None = None,
            locale: str | None = None) -> Resolution:
    candidates = list(registry.editions.values())

    if nation:
        national = [e for e in candidates if e.scope == nation]
        candidates = national or [e for e in candidates if e.scope == "universal"]
    else:
        candidates = [e for e in candidates if e.scope == "universal"]

    if locale:
        wanted = [e for e in candidates if _primary(e.language) == _primary(locale)]
        if wanted:
            candidates = wanted
        else:
            # Widen, but never past universal scope: a locale request for
            # nation X must never resolve to an edition scoped to nation Y.
            universal = [e for e in registry.editions.values() if e.scope == "universal"]
            wanted_universal = [e for e in universal if _primary(e.language) == _primary(locale)]
            if wanted_universal:
                candidates = wanted_universal
            else:
                # Fall back to universal-scoped "la" editions
                candidates = [e for e in universal if _primary(e.language) == "la"]

    if year is not None:
        candidates = [e for e in candidates if e.promulgated_year <= year]
        if not candidates:
            raise PreFirstEditionError(year)

    def rank(e: EditionMeta):
        return (e.promulgated_year, e.nature != "translatio", e.id)

    winner = max(candidates, key=rank)
    if winner.id not in available:
        raise EditionUnavailableError(winner.id)

    resolved_from = {k: v for k, v in
                     (("nation", nation), ("year", year), ("locale", locale))
                     if v is not None}
    return Resolution(edition_id=winner.id, resolved_from=resolved_from)
