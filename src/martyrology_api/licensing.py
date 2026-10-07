from fastapi import Request

from .auth import Identity
from .authz import user_ref
from .models import ElogiumOut


def is_restricted(edition_id: str, settings) -> bool:
    """Whether the edition's texts are withheld from some callers: a copyrighted edition while
    the access rule is not "public"."""
    return edition_id in settings.gated_set


async def texts_allowed(request: Request, identity: Identity | None, edition_id: str) -> bool:
    if not is_restricted(edition_id, request.app.state.settings):
        return True
    if identity is None:
        return False
    if request.app.state.settings.restricted_texts_access == "authenticated":
        return True
    return await request.app.state.authz.check(user_ref(identity), "can_read_texts", edition_id)


def redact(elogia: list[ElogiumOut]) -> None:
    for e in elogia:
        e.text = None
        e.footnotes = []
        e.marginalia = []
        e.errata = []
