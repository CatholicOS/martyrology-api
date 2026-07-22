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
                 cache_ttl: int = 300, cache_max: int = 10_000,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.cache_ttl = cache_ttl
        self.cache_max = cache_max
        self._transport = transport
        self._cache: dict[str, tuple[Identity | None, float]] = {}

    def _insert_cache(self, token: str, ident: Identity | None) -> None:
        if len(self._cache) >= self.cache_max:
            now = time.monotonic()
            expired = [t for t, (_, exp) in self._cache.items() if exp <= now]
            for t in expired:
                del self._cache[t]
            if len(self._cache) >= self.cache_max:
                oldest = min(self._cache, key=lambda t: self._cache[t][1])
                del self._cache[oldest]
        self._cache[token] = (ident, time.monotonic() + self.cache_ttl)

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
        if resp.status_code != 200:
            # Don't cache failures from the introspection endpoint itself
            # (outage, rate limit, etc.) — only cache a definitive answer.
            return None
        body = resp.json()
        ident: Identity | None = None
        if body.get("active"):
            ident = Identity(
                subject=body["sub"],
                username=body.get("username") or body.get("preferred_username")
                         or body["sub"],
                email=body.get("email"), name=body.get("name"))
        self._insert_cache(token, ident)
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
