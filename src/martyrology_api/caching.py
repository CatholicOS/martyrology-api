import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if (request.method != "GET"
                or not request.url.path.startswith("/api/v1")
                or response.status_code != 200):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        etag = '"' + hashlib.md5(body).hexdigest() + '"'

        if getattr(request.state, "cache_private", False):
            cc = "private, max-age=0"
        elif "/edition/" in request.url.path or "edition" in request.query_params:
            cc = "public, max-age=31536000, immutable"
        else:
            cc = "public, max-age=86400"

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.update({"etag": etag, "cache-control": cc,
                        "vary": "Authorization, Accept-Language"})
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304,
                            headers={"etag": etag, "cache-control": cc,
                                     "vary": "Authorization, Accept-Language"})
        return Response(content=body, status_code=200, headers=headers)
