from fastapi import Request

from .auth import Identity
from .authz import user_ref
from .models import ElogiumOut


def is_restricted(edition_id: str, settings) -> bool:
    return edition_id in settings.restricted_set


async def texts_allowed(request: Request, identity: Identity | None, edition_id: str) -> bool:
    if not is_restricted(edition_id, request.app.state.settings):
        return True
    if identity is None:
        return False
    return await request.app.state.authz.check(user_ref(identity), "can_read_texts", edition_id)


def redact(elogia: list[ElogiumOut]) -> None:
    for e in elogia:
        e.text = None
