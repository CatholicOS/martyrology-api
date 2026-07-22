import json

import httpx2 as httpx
import pytest

from martyrology_api.auth import Authenticator, Identity

CALLS = {"n": 0}


def mock_transport(active: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        assert request.url.path.endswith("/oauth/v2/introspect")
        body = {"active": active, "sub": "u123", "username": "jdoe",
                "email": "j@example.org", "name": "J. Doe"}
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_active_token_yields_identity():
    a = Authenticator("https://zitadel.example", "cid", "sec",
                      transport=mock_transport(True))
    ident = await a.identity("tok1")
    assert ident == Identity(subject="u123", username="jdoe",
                             email="j@example.org", name="J. Doe")


@pytest.mark.asyncio
async def test_inactive_token_is_none():
    a = Authenticator("https://zitadel.example", "cid", "sec",
                      transport=mock_transport(False))
    assert await a.identity("tok2") is None


@pytest.mark.asyncio
async def test_cache_avoids_second_call():
    CALLS["n"] = 0
    a = Authenticator("https://zitadel.example", "cid", "sec",
                      transport=mock_transport(True))
    await a.identity("tok3")
    await a.identity("tok3")
    assert CALLS["n"] == 1


@pytest.mark.asyncio
async def test_disabled_when_no_issuer():
    a = Authenticator("", "", "")
    assert await a.identity("anything") is None
