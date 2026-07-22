import time
from dataclasses import dataclass

import httpx2 as httpx
from fastapi import Request

from .problems import ApiProblem


@dataclass(frozen=True)
class Identity:
    subject: str
    username: str
    email: str | None = None
    name: str | None = None


class Authenticator:
    def __init__(self, issuer: str, client_id: str, client_secret: str,
                 cache_ttl: int = 300,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.cache_ttl = cache_ttl
        self._transport = transport
        self._cache: dict[str, tuple[Identity | None, float]] = {}

    async def identity(self, token: str) -> Identity | None:
        if not self.issuer:
            return None
        hit = self._cache.get(token)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(
                f"{self.issuer}/oauth/v2/introspect",
                data={"token": token},
                auth=(self.client_id, self.client_secret))
        ident: Identity | None = None
        if resp.status_code == 200:
            body = resp.json()
            if body.get("active"):
                ident = Identity(
                    subject=body["sub"],
                    username=body.get("username") or body.get("preferred_username")
                             or body["sub"],
                    email=body.get("email"), name=body.get("name"))
        self._cache[token] = (ident, time.monotonic() + self.cache_ttl)
        return ident


async def get_identity(request: Request) -> Identity | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiProblem(401, "Invalid Authorization header",
                         detail="Expected 'Authorization: Bearer <token>'.",
                         type_slug="invalid-token")
    ident = await request.app.state.authenticator.identity(token.strip())
    if ident is None:
        raise ApiProblem(401, "Invalid or expired token",
                         type_slug="invalid-token")
    return ident
