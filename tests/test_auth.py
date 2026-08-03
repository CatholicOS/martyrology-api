import httpx2 as httpx
import pytest

from martyrology_api.auth import Authenticator, Identity

CALLS = {"n": 0}


def mock_transport(active: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        assert request.url.path.endswith("/oauth/v2/introspect")
        body = {
            "active": active,
            "sub": "u123",
            "username": "jdoe",
            "email": "j@example.org",
            "name": "J. Doe",
        }
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_active_token_yields_identity():
    a = Authenticator("https://zitadel.example", "cid", "sec", transport=mock_transport(True))
    ident = await a.identity("tok1")
    assert ident == Identity(subject="u123", username="jdoe", email="j@example.org", name="J. Doe")


@pytest.mark.asyncio
async def test_inactive_token_is_none():
    a = Authenticator("https://zitadel.example", "cid", "sec", transport=mock_transport(False))
    assert await a.identity("tok2") is None


@pytest.mark.asyncio
async def test_cache_avoids_second_call():
    CALLS["n"] = 0
    a = Authenticator("https://zitadel.example", "cid", "sec", transport=mock_transport(True))
    await a.identity("tok3")
    await a.identity("tok3")
    assert CALLS["n"] == 1


@pytest.mark.asyncio
async def test_disabled_when_no_issuer():
    a = Authenticator("", "", "")
    assert await a.identity("anything") is None


def mock_transport_status(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        return httpx.Response(status, json={"active": False})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_non_200_introspection_is_not_cached_and_is_retried():
    CALLS["n"] = 0
    a = Authenticator("https://zitadel.example", "cid", "sec", transport=mock_transport_status(500))
    assert await a.identity("tokErr") is None
    assert await a.identity("tokErr") is None
    assert CALLS["n"] == 2


def transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_transport_error_is_not_cached_and_is_retried():
    CALLS["n"] = 0
    a = Authenticator("https://zitadel.example", "cid", "sec", transport=transport_error())
    assert await a.identity("tokTransportErr") is None
    assert await a.identity("tokTransportErr") is None
    assert CALLS["n"] == 2


def mock_transport_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        return httpx.Response(200, content=b"not json")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_malformed_json_response_is_none():
    a = Authenticator(
        "https://zitadel.example", "cid", "sec", transport=mock_transport_malformed_json()
    )
    assert await a.identity("tokBadJson") is None


def mock_transport_active_no_sub():
    def handler(request: httpx.Request) -> httpx.Response:
        CALLS["n"] += 1
        return httpx.Response(200, json={"active": True, "username": "jdoe"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_active_without_sub_is_none():
    a = Authenticator(
        "https://zitadel.example", "cid", "sec", transport=mock_transport_active_no_sub()
    )
    assert await a.identity("tokNoSub") is None


@pytest.mark.asyncio
async def test_cache_stays_bounded():
    a = Authenticator(
        "https://zitadel.example", "cid", "sec", cache_max=3, transport=mock_transport(True)
    )
    for i in range(10):
        await a.identity(f"tok-bound-{i}")
    assert len(a._cache) <= 3


PROJECT = "384518610174869507"
OTHER_PROJECT = "999999999999999999"


def roles_transport(claims: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"active": True, "sub": "u123", "username": "jdoe"}
        body.update(claims)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _auth(claims: dict, project_id: str = PROJECT) -> Authenticator:
    return Authenticator(
        "https://zitadel.example",
        "cid",
        "sec",
        project_id=project_id,
        transport=roles_transport(claims),
    )


@pytest.mark.asyncio
async def test_roles_read_from_project_scoped_claim():
    claims = {f"urn:zitadel:iam:org:project:{PROJECT}:roles": {"admin": {}, "developer": {}}}
    ident = await _auth(claims).identity("t")
    assert ident is not None
    assert ident.roles == frozenset({"admin", "developer"})


@pytest.mark.asyncio
async def test_generic_roles_claim_is_ignored():
    claims = {"urn:zitadel:iam:org:project:roles": {"admin": {}}}
    ident = await _auth(claims).identity("t")
    assert ident is not None
    assert ident.roles == frozenset()


@pytest.mark.asyncio
async def test_roles_from_another_project_are_ignored():
    claims = {f"urn:zitadel:iam:org:project:{OTHER_PROJECT}:roles": {"admin": {}}}
    ident = await _auth(claims).identity("t")
    assert ident is not None
    assert ident.roles == frozenset()


@pytest.mark.asyncio
async def test_absent_claim_yields_no_roles():
    ident = await _auth({}).identity("t")
    assert ident is not None
    assert ident.roles == frozenset()


@pytest.mark.asyncio
async def test_non_object_claim_yields_no_roles():
    claims = {f"urn:zitadel:iam:org:project:{PROJECT}:roles": ["admin"]}
    ident = await _auth(claims).identity("t")
    assert ident is not None
    assert ident.roles == frozenset()


@pytest.mark.asyncio
async def test_no_project_id_configured_yields_no_roles():
    claims = {f"urn:zitadel:iam:org:project:{PROJECT}:roles": {"admin": {}}}
    ident = await _auth(claims, project_id="").identity("t")
    assert ident is not None
    assert ident.roles == frozenset()
