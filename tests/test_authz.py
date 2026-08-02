import json

import httpx2 as httpx
import pytest

from martyrology_api.auth import Identity
from martyrology_api.authz import Authz, AuthzError, user_ref


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


def header_capturing_transport(seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"allowed": True})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_check_sends_bearer_token():
    seen: dict = {}
    a = Authz(
        "https://fga.example",
        "store1",
        "model1",
        api_token="k3y",
        transport=header_capturing_transport(seen),
    )
    assert await a.check("user:u", "can_edit", "ed1") is True
    assert seen["auth"] == "Bearer k3y"


@pytest.mark.asyncio
async def test_check_omits_header_when_no_token():
    seen: dict = {}
    a = Authz(
        "https://fga.example",
        "store1",
        "model1",
        transport=header_capturing_transport(seen),
    )
    assert await a.check("user:u", "can_edit", "ed1") is True
    assert seen["auth"] is None


@pytest.mark.asyncio
async def test_check_object_takes_a_full_object_ref():
    seen: dict = {}
    a = Authz(
        "https://fga.example",
        "store1",
        "model1",
        api_token="k",
        transport=header_capturing_transport(seen),
    )
    assert await a.check_object("user:u", "admin", "governance_body:cei") is True
    assert seen["body"]["tuple_key"]["object"] == "governance_body:cei"


@pytest.mark.asyncio
async def test_check_still_prefixes_edition():
    seen: dict = {}
    a = Authz(
        "https://fga.example",
        "store1",
        "model1",
        api_token="k",
        transport=header_capturing_transport(seen),
    )
    await a.check("user:u", "can_edit", "martyrologium_romanum_2004")
    assert seen["body"]["tuple_key"]["object"] == "edition:martyrologium_romanum_2004"


@pytest.mark.asyncio
async def test_check_object_fails_closed_when_unconfigured():
    assert await Authz("", "", "").check_object("user:u", "admin", "governance_body:cei") is False


def write_transport(seen: dict, status: int = 200, payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload if payload is not None else {})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_write_sends_a_writes_tuple_key():
    seen: dict = {}
    a = Authz("https://fga.example", "s1", "m1", api_token="k", transport=write_transport(seen))
    await a.write("user:u1", "editor", "governance_body:cei")
    assert seen["path"] == "/stores/s1/write"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["writes"]["tuple_keys"] == [
        {"user": "user:u1", "relation": "editor", "object": "governance_body:cei"}
    ]
    assert seen["body"]["authorization_model_id"] == "m1"


@pytest.mark.asyncio
async def test_delete_sends_a_deletes_tuple_key():
    seen: dict = {}
    a = Authz("https://fga.example", "s1", "m1", api_token="k", transport=write_transport(seen))
    await a.delete("user:u1", "editor", "governance_body:cei")
    assert seen["body"]["deletes"]["tuple_keys"] == [
        {"user": "user:u1", "relation": "editor", "object": "governance_body:cei"}
    ]
    assert "writes" not in seen["body"]


@pytest.mark.asyncio
async def test_write_raises_with_the_openfga_code():
    seen: dict = {}
    a = Authz(
        "https://fga.example",
        "s1",
        "m1",
        api_token="k",
        transport=write_transport(
            seen,
            status=400,
            payload={"code": "write_failed_due_to_invalid_input", "message": "already exists"},
        ),
    )
    with pytest.raises(AuthzError) as exc:
        await a.write("user:u1", "editor", "governance_body:cei")
    assert exc.value.status == 400
    assert exc.value.code == "write_failed_due_to_invalid_input"


@pytest.mark.asyncio
async def test_write_raises_when_unconfigured():
    with pytest.raises(AuthzError):
        await Authz("", "", "").write("user:u1", "editor", "governance_body:cei")


@pytest.mark.asyncio
async def test_read_tuples_returns_keys_and_follows_pagination():
    pages = [
        {
            "tuples": [
                {"key": {"user": "user:a", "relation": "editor", "object": "governance_body:cei"}}
            ],
            "continuation_token": "next",
        },
        {
            "tuples": [
                {"key": {"user": "user:b", "relation": "admin", "object": "governance_body:cei"}}
            ],
            "continuation_token": "",
        },
    ]
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=pages[len(calls) - 1])

    a = Authz(
        "https://fga.example",
        "s1",
        "m1",
        api_token="k",
        transport=httpx.MockTransport(handler),
    )
    got = await a.read_tuples("governance_body:cei")
    assert [t["user"] for t in got] == ["user:a", "user:b"]
    assert calls[0]["tuple_key"] == {"object": "governance_body:cei"}
    assert calls[1]["continuation_token"] == "next"
    assert "authorization_model_id" not in calls[0]


@pytest.mark.asyncio
async def test_read_tuples_filters_by_relation_and_fails_closed():
    seen: dict = {}
    a = Authz(
        "https://fga.example",
        "s1",
        "m1",
        api_token="k",
        transport=write_transport(seen, payload={"tuples": [], "continuation_token": ""}),
    )
    assert await a.read_tuples("governance_body:cei", "editor") == []
    assert seen["body"]["tuple_key"] == {"object": "governance_body:cei", "relation": "editor"}
    assert await Authz("", "", "").read_tuples("governance_body:cei") == []


def non_dict_json_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_read_tuples_non_dict_json_body_fails_closed():
    a = Authz(
        "https://fga.example",
        "s1",
        "m1",
        api_token="k",
        transport=non_dict_json_transport(),
    )
    assert await a.read_tuples("governance_body:cei") == []


@pytest.mark.asyncio
async def test_read_tuples_stops_at_max_pages_when_token_never_ends():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "tuples": [
                    {
                        "key": {
                            "user": f"user:{len(calls)}",
                            "relation": "editor",
                            "object": "governance_body:cei",
                        }
                    }
                ],
                "continuation_token": "still-more",
            },
        )

    a = Authz(
        "https://fga.example",
        "s1",
        "m1",
        api_token="k",
        transport=httpx.MockTransport(handler),
    )
    got = await a.read_tuples("governance_body:cei")
    assert len(calls) == Authz.MAX_READ_PAGES
    assert len(got) == Authz.MAX_READ_PAGES
