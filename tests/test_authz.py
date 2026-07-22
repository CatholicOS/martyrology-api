import httpx2 as httpx
import pytest

from martyrology_api.auth import Identity
from martyrology_api.authz import Authz, user_ref


def transport(allowed: bool, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stores/store1/check"
        import json

        body = json.loads(request.content)
        assert body["tuple_key"]["object"].startswith("edition:")
        return httpx.Response(status, json={"allowed": allowed})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_allowed():
    a = Authz("https://fga.example", "store1", "model1", transport=transport(True))
    assert await a.check("user:u123", "can_read_texts", "martyrologium_romanum_2004") is True


@pytest.mark.asyncio
async def test_denied():
    a = Authz("https://fga.example", "store1", "model1", transport=transport(False))
    assert await a.check("user:u123", "can_edit", "martyrologium_romanum_2004") is False


@pytest.mark.asyncio
async def test_fails_closed_on_error_and_unconfigured():
    a = Authz("https://fga.example", "store1", "model1", transport=transport(True, status=500))
    assert await a.check("user:u", "can_edit", "x") is False
    assert await Authz("", "", "").check("user:u", "can_edit", "x") is False


def test_user_ref():
    assert user_ref(Identity(subject="u123", username="jdoe")) == "user:u123"


def malformed_json_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_malformed_json_response_is_false():
    a = Authz("https://fga.example", "store1", "model1", transport=malformed_json_transport())
    assert await a.check("user:u", "can_edit", "x") is False


def truthy_non_true_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"allowed": "yes"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_truthy_non_true_allowed_is_false():
    a = Authz("https://fga.example", "store1", "model1", transport=truthy_non_true_transport())
    assert await a.check("user:u", "can_edit", "x") is False
