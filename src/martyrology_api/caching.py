import hashlib
from typing import cast

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse


def declare_public_cache(request: Request) -> None:
    """Declare a router's 200 GET responses shared-cacheable.

    Caching is opt-in: a router that does not depend on this gets
    `private, max-age=0`, so a new route cannot leak into shared caches by
    omission. An individual handler can still downgrade to private by
    setting `request.state.cache_private = True`, which always wins.
    """
    request.state.cache_public = True


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if (
            request.method != "GET"
            or not request.url.path.startswith("/api/v1")
            or response.status_code != 200
        ):
            return response

        # BaseHTTPMiddleware.call_next always returns a StreamingResponse
        # under the hood, but its declared return type is the base Response.
        body = b""
        async for chunk in cast(StreamingResponse, response).body_iterator:
            body += chunk.encode() if isinstance(chunk, str) else bytes(chunk)
        etag = '"' + hashlib.md5(body, usedforsecurity=False).hexdigest() + '"'

        if getattr(request.state, "cache_private", False) or not getattr(
            request.state, "cache_public", False
        ):
            cc = "private, max-age=0"
        elif "/edition/" in request.url.path or "edition" in request.query_params:
            cc = "public, max-age=31536000, immutable"
        else:
            cc = "public, max-age=86400"

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.update(
            {
                "etag": etag,
                "cache-control": cc,
                "vary": "Authorization, Accept-Language, X-Curation-Branch",
            }
        )
        if request.headers.get("if-none-match") == etag:
            return Response(
                status_code=304,
                headers={
                    "etag": etag,
                    "cache-control": cc,
                    "vary": "Authorization, Accept-Language, X-Curation-Branch",
                },
            )
        return Response(content=body, status_code=200, headers=headers)
