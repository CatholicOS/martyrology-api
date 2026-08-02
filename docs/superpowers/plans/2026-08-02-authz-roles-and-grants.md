# Zitadel Role Gate, Platform Superuser, and Grant Endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the governance-body OpenFGA model operable — fix the dead
authorization check, gate curation writes on a Zitadel project role, express
platform authority as an OpenFGA type, and add a grant endpoint scoped to
governance bodies.

**Architecture:** `Authz` becomes a general OpenFGA client (authenticated
check / write / delete / read against arbitrary object refs) rather than an
edition-only checker. `Identity` carries the project-scoped Zitadel role set,
which curation write routes require before OpenFGA is consulted. A new
`/api/v1/admin/permissions` router writes `governance_body` tuples and nothing
else; the object type is fixed by the route shape, not validated from input.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, `httpx2` (imported as
`httpx`), pytest + `httpx.MockTransport`, OpenFGA HTTP API, Zitadel OIDC
introspection.

**Spec:** `docs/superpowers/specs/2026-08-02-authz-roles-and-grants-design.md`

## Global Constraints

- Python 3.12 (`target-version = "py312"`), ruff `line-length = 100`.
- No new runtime dependencies. HTTP is `import httpx2 as httpx`, matching
  every existing module.
- All new settings use the `MARTYROLOGY_` env prefix via `Settings`.
- Fail closed: any error, non-200, malformed body, or missing configuration
  results in denial, never in an allow.
- Tests must not touch the network. Use `httpx.MockTransport` for `Authz` and
  `Authenticator`, and the existing `StaticAuth` / `Grants` stub pattern for
  route tests.
- Never break existing constructor call sites. `Identity(...)` and
  `Authz(url, store, model)` are constructed positionally in tests; every new
  field and parameter is keyword-with-default and appended last.
- Commits are GPG-signed. Never pass `--no-gpg-sign`.
- Route paths below are written in full. All routers mount under `/api/v1`, so
  the spec's `/admin/permissions` is `/api/v1/admin/permissions` in code.
- Run tests with `uv run pytest`.

---

### Task 1: Authenticate to OpenFGA, and check arbitrary objects

Fixes the blocking defect: `Authz.check()` sends no `Authorization` header
while production OpenFGA runs `OPENFGA_AUTHN_METHOD=preshared`, so every
check 401s and returns `False`. Also generalises the object ref, which every
later task needs.

**Files:**
- Modify: `src/martyrology_api/config.py`
- Modify: `src/martyrology_api/authz.py`
- Modify: `src/martyrology_api/app.py:33-35`
- Test: `tests/test_authz.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Settings.openfga_api_token: str` (default `""`)
  - `Authz(api_url, store_id, model_id, api_token="", transport=None)`
  - `Authz.check_object(user: str, relation: str, obj: str) -> bool` — `obj`
    is a full `"<type>:<id>"` ref
  - `Authz.check(user: str, relation: str, edition_id: str) -> bool` —
    unchanged signature, now delegates to `check_object`
  - `Authz._headers() -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_authz.py`:

```python
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
        "https://fga.example", "store1", "model1", api_token="k3y",
        transport=header_capturing_transport(seen),
    )
    assert await a.check("user:u", "can_edit", "ed1") is True
    assert seen["auth"] == "Bearer k3y"


@pytest.mark.asyncio
async def test_check_omits_header_when_no_token():
    seen: dict = {}
    a = Authz(
        "https://fga.example", "store1", "model1",
        transport=header_capturing_transport(seen),
    )
    assert await a.check("user:u", "can_edit", "ed1") is True
    assert seen["auth"] is None


@pytest.mark.asyncio
async def test_check_object_takes_a_full_object_ref():
    seen: dict = {}
    a = Authz(
        "https://fga.example", "store1", "model1", api_token="k",
        transport=header_capturing_transport(seen),
    )
    assert await a.check_object("user:u", "admin", "governance_body:cei") is True
    assert seen["body"]["tuple_key"]["object"] == "governance_body:cei"


@pytest.mark.asyncio
async def test_check_still_prefixes_edition():
    seen: dict = {}
    a = Authz(
        "https://fga.example", "store1", "model1", api_token="k",
        transport=header_capturing_transport(seen),
    )
    await a.check("user:u", "can_edit", "martyrologium_romanum_2004")
    assert seen["body"]["tuple_key"]["object"] == "edition:martyrologium_romanum_2004"


@pytest.mark.asyncio
async def test_check_object_fails_closed_when_unconfigured():
    assert await Authz("", "", "").check_object("user:u", "admin", "governance_body:cei") is False
```

Note the existing `transport()` helper at the top of this file asserts
`body["tuple_key"]["object"].startswith("edition:")`. Leave it as-is — it
still describes the `check()` wrapper the older tests exercise.

Append to `tests/test_config.py`:

```python
def test_authz_enabled_requires_a_token():
    from martyrology_api.config import Settings

    base = dict(openfga_api_url="https://fga.example", openfga_store_id="s1")
    assert Settings(_env_file=None, **base).authz_enabled is False
    assert Settings(_env_file=None, **base, openfga_api_token="k").authz_enabled is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_authz.py tests/test_config.py -v`
Expected: FAIL — `TypeError: Authz.__init__() got an unexpected keyword
argument 'api_token'`, `AttributeError: 'Authz' object has no attribute
'check_object'`, and `Settings` rejecting `openfga_api_token`.

- [ ] **Step 3: Add the setting**

In `src/martyrology_api/config.py`, after `openfga_model_id`:

```python
    openfga_api_token: str = ""
```

and replace `authz_enabled`:

```python
    @property
    def authz_enabled(self) -> bool:
        return bool(self.openfga_api_url and self.openfga_store_id and self.openfga_api_token)
```

- [ ] **Step 4: Rewrite `Authz`**

Replace the body of `src/martyrology_api/authz.py` below `user_ref` with:

```python
class Authz:
    def __init__(
        self,
        api_url: str,
        store_id: str,
        model_id: str,
        api_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.store_id = store_id
        self.model_id = model_id
        self.api_token = api_token
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"} if self.api_token else {}

    async def check(self, user: str, relation: str, edition_id: str) -> bool:
        return await self.check_object(user, relation, f"edition:{edition_id}")

    async def check_object(self, user: str, relation: str, obj: str) -> bool:
        if not self.api_url or not self.store_id:
            return False
        body: dict[str, object] = {
            "tuple_key": {"user": user, "relation": relation, "object": obj}
        }
        if self.model_id:
            body["authorization_model_id"] = self.model_id
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(
                    f"{self.api_url}/stores/{self.store_id}/check",
                    json=body,
                    headers=self._headers(),
                )
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            payload = resp.json()
        except ValueError:
            return False
        return payload.get("allowed") is True
```

- [ ] **Step 5: Wire the token through and warn on a half-configured deployment**

In `src/martyrology_api/app.py`, replace lines 33-35 with:

```python
    app.state.authz = Authz(
        settings.openfga_api_url,
        settings.openfga_store_id,
        settings.openfga_model_id,
        settings.openfga_api_token,
    )
    if settings.openfga_api_url and not settings.openfga_api_token:
        logging.getLogger(__name__).warning(
            "MARTYROLOGY_OPENFGA_API_URL is set but MARTYROLOGY_OPENFGA_API_TOKEN is empty; "
            "an OpenFGA running with preshared authentication will reject every check, "
            "denying all curation writes and redacting all restricted texts."
        )
```

Add `import logging` to the top of `app.py`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_authz.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole suite and the linter**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS — nothing else calls `Authz` with a fourth positional argument.

- [ ] **Step 8: Commit**

```bash
git add src/martyrology_api/config.py src/martyrology_api/authz.py \
        src/martyrology_api/app.py tests/test_authz.py tests/test_config.py
git commit -m "Authenticate to OpenFGA and allow checks on any object type"
```

---

### Task 2: Carry project-scoped Zitadel roles on Identity

**Files:**
- Modify: `src/martyrology_api/config.py`
- Modify: `src/martyrology_api/auth.py`
- Modify: `src/martyrology_api/app.py:30-32`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `Settings.zitadel_project_id: str` (default `""`)
  - `Identity.roles: frozenset[str]` (default `frozenset()`, appended last so
    positional construction elsewhere keeps working)
  - `Authenticator(issuer, client_id, client_secret, project_id="", cache_ttl=300,
    cache_max=10_000, transport=None)`
  - `Authenticator.roles_claim -> str` — `""` when no project ID is configured

The claim shape, confirmed by live introspection against
`auth.catholicdigitalcommons.org`:

```json
"urn:zitadel:iam:org:project:384518610174869507:roles": {
  "martyrology_editor": { "384518609990320131": "martyrology.auth.catholicdigitalcommons.org" }
}
```

Roles are the object's **keys**. The generic
`urn:zitadel:iam:org:project:roles` claim is deliberately NOT read — it can
carry roles held in other projects of the same umbrella instance.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
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
        "https://zitadel.example", "cid", "sec",
        project_id=project_id, transport=roles_transport(claims),
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL — `TypeError: Authenticator.__init__() got an unexpected
keyword argument 'project_id'`.

- [ ] **Step 3: Add the setting**

In `src/martyrology_api/config.py`, after `zitadel_client_secret`:

```python
    zitadel_project_id: str = ""
```

- [ ] **Step 4: Extend Identity and Authenticator**

In `src/martyrology_api/auth.py`, extend the dataclass — `roles` goes last so
existing positional and keyword construction is untouched:

```python
@dataclass(frozen=True)
class Identity:
    subject: str
    username: str
    email: str | None = None
    name: str | None = None
    roles: frozenset[str] = frozenset()
```

Add `project_id` to `Authenticator.__init__`, after `client_secret` and before
`cache_ttl`:

```python
        project_id: str = "",
```

with `self.project_id = project_id` alongside the other assignments, and:

```python
    @property
    def roles_claim(self) -> str:
        if not self.project_id:
            return ""
        return f"urn:zitadel:iam:org:project:{self.project_id}:roles"

    def _roles(self, body: dict) -> frozenset[str]:
        claim_key = self.roles_claim
        if not claim_key:
            return frozenset()
        claim = body.get(claim_key)
        if not isinstance(claim, dict):
            return frozenset()
        return frozenset(k for k in claim if isinstance(k, str))
```

In `identity()`, replace the `Identity(...)` construction with:

```python
                ident = Identity(
                    subject=sub,
                    username=body.get("username") or body.get("preferred_username") or sub,
                    email=body.get("email"),
                    name=body.get("name"),
                    roles=self._roles(body),
                )
```

- [ ] **Step 5: Wire the project ID through**

In `src/martyrology_api/app.py`, replace lines 30-32 with:

```python
    app.state.authenticator = Authenticator(
        settings.zitadel_issuer,
        settings.zitadel_client_id,
        settings.zitadel_client_secret,
        settings.zitadel_project_id,
    )
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole suite and the linter**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS. The existing
`test_active_token_yields_identity` compares against
`Identity(subject="u123", username="jdoe", email="j@example.org", name="J. Doe")`,
which still holds because `roles` defaults to the empty frozenset and that
`Authenticator` is built without a project ID.

- [ ] **Step 8: Commit**

```bash
git add src/martyrology_api/config.py src/martyrology_api/auth.py \
        src/martyrology_api/app.py tests/test_auth.py
git commit -m "Read project-scoped Zitadel roles into Identity"
```

---

### Task 3: Gate curation writes on a project role

The gate is a population filter: it says the caller is a curator of this
property, not which editions they may touch. It runs **before** the OpenFGA
check, so a caller without a role never causes a network round trip.

`admin` grants no OpenFGA bypass — it only satisfies this gate. Read routes
are deliberately not gated: `licensing.texts_allowed` serves licensed readers
who hold no project role, and `read._draft_months` is a read whose `can_edit`
check already restricts it to curators.

**Files:**
- Modify: `src/martyrology_api/routers/curation.py:51-69`
- Test: `tests/test_curation_api.py`

**Interfaces:**
- Consumes: `Identity.roles` from Task 2.
- Produces: `curation.CURATION_ROLES: frozenset[str]` — `{"admin",
  "martyrology_editor"}`; a 403 problem with `type_slug="missing-role"`.

- [ ] **Step 1: Write the failing tests**

The existing `StaticAuth` in `tests/test_curation_api.py` returns an
`Identity` with no roles, so gating would break every write test in the file.
Give it roles by default and add a switch. Replace the `StaticAuth` class
with:

```python
class StaticAuth:
    def __init__(self, roles=frozenset({"martyrology_editor"})):
        self.roles = roles

    async def identity(self, token):
        return (
            Identity(
                subject="u123", username="jdoe", email="j@example.org", roles=self.roles
            )
            if token == "good"
            else None
        )
```

Every existing `StaticAuth()` call site keeps working and now supplies the
editor role. Append these tests:

```python
def test_missing_role_is_denied_before_openfga_is_consulted(client):
    class ExplodingAuthz:
        async def check(self, *a, **k):
            raise AssertionError("OpenFGA must not be consulted without a role")

    client.app.state.authenticator = StaticAuth(roles=frozenset())
    client.app.state.authz = ExplodingAuthz()
    r = client.patch(
        "/api/v1/editions/martyrologium_romanum_1584/elogia/mr:0102-concordius",
        json={"text": "x"},
        headers={"Authorization": "Bearer good"},
    )
    assert r.status_code == 403
    assert r.json()["type"].endswith("/missing-role")


def test_irrelevant_role_does_not_satisfy_the_gate(client):
    client.app.state.authenticator = StaticAuth(roles=frozenset({"developer"}))
    r = client.patch(
        "/api/v1/editions/martyrologium_romanum_1584/elogia/mr:0102-concordius",
        json={"text": "x"},
        headers={"Authorization": "Bearer good"},
    )
    assert r.status_code == 403
    assert r.json()["type"].endswith("/missing-role")


def test_admin_role_satisfies_the_gate_but_does_not_bypass_openfga(client):
    client.app.state.authenticator = StaticAuth(roles=frozenset({"admin"}))
    client.app.state.authz = Grants(set())
    r = client.patch(
        "/api/v1/editions/martyrologium_romanum_1584/elogia/mr:0102-concordius",
        json={"text": "x"},
        headers={"Authorization": "Bearer good"},
    )
    assert r.status_code == 403
    assert r.json()["type"].endswith("/forbidden")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_curation_api.py -v`
Expected: FAIL — the two `missing-role` tests get 403 with type
`.../forbidden` (or an `AssertionError` from `ExplodingAuthz`) because no
role gate exists yet.

- [ ] **Step 3: Add the gate**

In `src/martyrology_api/routers/curation.py`, add after the `TOPIC_RE`
definition:

```python
CURATION_ROLES = frozenset({"admin", "martyrology_editor"})
```

and insert the role check into `require_relation`'s inner `dep`, between the
`identity is None` guard and the `authz.check` call:

```python
        if not (identity.roles & CURATION_ROLES):
            raise ApiProblem(
                403,
                "Forbidden",
                detail="Curation requires the 'martyrology_editor' or 'admin' role "
                "on the Martyrology project.",
                type_slug="missing-role",
            )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_curation_api.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite and the linter**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS. If `tests/test_read_api.py` builds its own identity stub for
the draft-read path, it must remain unchanged — that path is not gated.

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/routers/curation.py tests/test_curation_api.py
git commit -m "Require a Martyrology project role for curation writes"
```

---

### Task 4: Give Authz write, delete, and read operations

**Files:**
- Modify: `src/martyrology_api/authz.py`
- Test: `tests/test_authz.py`

**Interfaces:**
- Consumes: `Authz._headers()` and `check_object` from Task 1.
- Produces:
  - `class AuthzError(Exception)` with `.status: int`, `.code: str`,
    `.message: str`
  - `Authz.write(user, relation, obj) -> None`
  - `Authz.delete(user, relation, obj) -> None`
  - `Authz.read_tuples(obj, relation="") -> list[dict]` — each item is a
    tuple key dict with `user`, `relation`, `object`

Both mutations raise `AuthzError` on failure. Callers decide what a given
`code` means; `Authz` never swallows an error into silent success.

OpenFGA wire shapes:

```
POST /stores/{id}/write
  {"writes":  {"tuple_keys": [{"user":..,"relation":..,"object":..}]},
   "authorization_model_id": "01..."}
  {"deletes": {"tuple_keys": [{"user":..,"relation":..,"object":..}]},
   "authorization_model_id": "01..."}

POST /stores/{id}/read
  {"tuple_key": {"object": "governance_body:cei"}, "page_size": 100}
  -> {"tuples": [{"key": {...}, "timestamp": "..."}], "continuation_token": ""}
```

The Read API takes no `authorization_model_id`; do not send one.

- [ ] **Step 1: Write the failing tests**

Ruff's lint set is `["E", "F", "W", "I", "UP", "B"]`, so E402 and isort are
active: add these two imports at the **top** of `tests/test_authz.py`, not
mid-file. `json` joins the stdlib block, and `AuthzError` joins the existing
`from martyrology_api.authz import Authz, user_ref` line:

```python
import json

from martyrology_api.authz import Authz, AuthzError, user_ref
```

Then append the tests themselves (`json` below refers to that top-level
import; note the existing `transport()` helper does its own function-local
`import json`, which is fine to leave alone):

```python
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
        "https://fga.example", "s1", "m1", api_token="k",
        transport=write_transport(
            seen, status=400,
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
        "https://fga.example", "s1", "m1", api_token="k",
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
        "https://fga.example", "s1", "m1", api_token="k",
        transport=write_transport(seen, payload={"tuples": [], "continuation_token": ""}),
    )
    assert await a.read_tuples("governance_body:cei", "editor") == []
    assert seen["body"]["tuple_key"] == {"object": "governance_body:cei", "relation": "editor"}
    assert await Authz("", "", "").read_tuples("governance_body:cei") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_authz.py -v`
Expected: FAIL — `ImportError: cannot import name 'AuthzError'`.

- [ ] **Step 3: Implement**

Add to the top of `src/martyrology_api/authz.py`, above `class Authz`:

```python
class AuthzError(Exception):
    def __init__(self, status: int, code: str = "", message: str = ""):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"OpenFGA {status} {code}: {message}".rstrip(": "))
```

Add to `Authz`, after `check_object`:

```python
    MAX_READ_PAGES = 10

    async def write(self, user: str, relation: str, obj: str) -> None:
        await self._mutate("writes", user, relation, obj)

    async def delete(self, user: str, relation: str, obj: str) -> None:
        await self._mutate("deletes", user, relation, obj)

    async def _mutate(self, key: str, user: str, relation: str, obj: str) -> None:
        if not self.api_url or not self.store_id:
            raise AuthzError(0, "not_configured", "OpenFGA is not configured")
        body: dict[str, object] = {
            "tuple_keys": [{"user": user, "relation": relation, "object": obj}]
        }
        payload: dict[str, object] = {key: body}
        if self.model_id:
            payload["authorization_model_id"] = self.model_id
        try:
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.post(
                    f"{self.api_url}/stores/{self.store_id}/write",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise AuthzError(0, "transport_error", str(exc)) from exc
        if resp.status_code == 200:
            return
        code, message = "", ""
        try:
            err = resp.json()
        except ValueError:
            err = {}
        if isinstance(err, dict):
            code = err.get("code") or ""
            message = err.get("message") or ""
        raise AuthzError(resp.status_code, code, message)

    async def read_tuples(self, obj: str, relation: str = "") -> list[dict]:
        if not self.api_url or not self.store_id:
            return []
        tuple_key: dict[str, str] = {"object": obj}
        if relation:
            tuple_key["relation"] = relation
        out: list[dict] = []
        token = ""
        for _ in range(self.MAX_READ_PAGES):
            payload: dict[str, object] = {"tuple_key": tuple_key, "page_size": 100}
            if token:
                payload["continuation_token"] = token
            try:
                async with httpx.AsyncClient(transport=self._transport) as client:
                    resp = await client.post(
                        f"{self.api_url}/stores/{self.store_id}/read",
                        json=payload,
                        headers=self._headers(),
                    )
            except httpx.HTTPError:
                return out
            if resp.status_code != 200:
                return out
            try:
                page = resp.json()
            except ValueError:
                return out
            for item in page.get("tuples") or []:
                keyed = item.get("key") if isinstance(item, dict) else None
                if isinstance(keyed, dict):
                    out.append(keyed)
            token = page.get("continuation_token") or ""
            if not token:
                break
        return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_authz.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite and the linter**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/martyrology_api/authz.py tests/test_authz.py
git commit -m "Add write, delete, and read operations to the OpenFGA client"
```

---

### Task 5: The grant endpoint

Four routes over `governance_body` tuples and nothing else. The object type
is fixed by the route shape rather than taken from input, so no request can
reach an `edition` or `platform` tuple — the allowlist is structural.

Authorization is OpenFGA `admin` on the target body alone. No role gate and
no global-admin bypass: a body admin who is not platform staff holds no
project role, and gating grants on a platform-wide role would centralise
exactly what the governance model decentralises. The platform superuser
reaches every body through the model change in Task 6.

**Files:**
- Create: `src/martyrology_api/routers/admin.py`
- Modify: `src/martyrology_api/models.py`
- Modify: `src/martyrology_api/app.py`
- Test: `tests/test_admin_api.py` (create)

**Interfaces:**
- Consumes: `Authz.check_object`, `write`, `delete`, `read_tuples`,
  `AuthzError` (Tasks 1 and 4); `get_identity`, `Identity` (Task 2);
  `user_ref`, `ApiProblem`.
- Produces: router mounted at `/api/v1/admin/permissions`.

Routes, in full:

| Method | Path | Input |
|---|---|---|
| GET | `/api/v1/admin/permissions` | query `governance_body`, optional `relation` |
| POST | `/api/v1/admin/permissions` | JSON `{user, governance_body, relation}` |
| DELETE | `/api/v1/admin/permissions` | query `user`, `governance_body`, `relation` |
| GET | `/api/v1/admin/permissions/check` | query `user`, `governance_body`, `relation` |

`DELETE` takes query parameters, not LitCal's request body — a body on DELETE
is poorly supported by intermediaries and buys nothing here.

Idempotency: a duplicate grant and a revoke of an absent tuple both return
200. OpenFGA reports both as `400 write_failed_due_to_invalid_input`. Because
the relation is allowlisted and the object type is fixed before the call, that
code cannot mean malformed input by the time it is observed, so mapping it to
success is safe. Any other code becomes a 502.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_api.py`:

```python
import pytest

from martyrology_api.auth import Identity
from martyrology_api.authz import AuthzError

BASE = "/api/v1/admin/permissions"


class StaticAuth:
    def __init__(self, roles=frozenset()):
        self.roles = roles

    async def identity(self, token):
        if token == "admin":
            return Identity(subject="adm", username="adm", roles=self.roles)
        if token == "plain":
            return Identity(subject="plain", username="plain", roles=self.roles)
        return None


class FakeAuthz:
    """Admins on governance_body:cei; records every mutation."""

    def __init__(self, admins=frozenset({"user:adm"}), tuples=None, fail=None):
        self.admins = admins
        self.tuples = tuples if tuples is not None else []
        self.fail = fail
        self.writes: list[tuple] = []
        self.deletes: list[tuple] = []

    async def check_object(self, user, relation, obj):
        if relation == "admin" and obj == "governance_body:cei":
            return user in self.admins
        return (user, relation, obj) in {
            (t["user"], t["relation"], t["object"]) for t in self.tuples
        }

    async def write(self, user, relation, obj):
        if self.fail:
            raise self.fail
        self.writes.append((user, relation, obj))

    async def delete(self, user, relation, obj):
        if self.fail:
            raise self.fail
        self.deletes.append((user, relation, obj))

    async def read_tuples(self, obj, relation=""):
        return [
            t for t in self.tuples
            if t["object"] == obj and (not relation or t["relation"] == relation)
        ]


@pytest.fixture
def client(make_client):
    c = make_client()
    c.app.state.authenticator = StaticAuth()
    c.app.state.authz = FakeAuthz()
    return c


def hdr(token="admin"):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_is_401(client):
    r = client.get(f"{BASE}?governance_body=cei")
    assert r.status_code == 401


def test_non_body_admin_is_403(client):
    r = client.get(f"{BASE}?governance_body=cei", headers=hdr("plain"))
    assert r.status_code == 403
    assert r.json()["type"].endswith("/forbidden")


def test_body_admin_can_grant(client):
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 200
    assert r.json() == {"user": "user:u9", "governance_body": "cei", "relation": "editor"}
    assert client.app.state.authz.writes == [("user:u9", "editor", "governance_body:cei")]


def test_grant_normalises_a_prefixed_user(client):
    client.post(
        BASE,
        json={"user": "user:u9", "governance_body": "cei", "relation": "reader"},
        headers=hdr(),
    )
    assert client.app.state.authz.writes == [("user:u9", "reader", "governance_body:cei")]


def test_grant_on_a_body_you_do_not_administer_is_403(client):
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "editio_typica", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 403
    assert client.app.state.authz.writes == []


def test_relation_outside_the_allowlist_is_422(client):
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "superuser"},
        headers=hdr(),
    )
    assert r.status_code == 422
    assert client.app.state.authz.writes == []


def test_malformed_body_id_is_422(client):
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei:x", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 422


def test_duplicate_grant_is_idempotent(client):
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "exists")
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 200


def test_other_openfga_errors_are_502(client):
    client.app.state.authz.fail = AuthzError(500, "internal_error", "boom")
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 502


def test_revoke_removes_the_tuple(client):
    r = client.request(
        "DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor", headers=hdr()
    )
    assert r.status_code == 200
    assert client.app.state.authz.deletes == [("user:u9", "editor", "governance_body:cei")]


def test_revoke_of_an_absent_tuple_is_idempotent(client):
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "missing")
    r = client.request(
        "DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor", headers=hdr()
    )
    assert r.status_code == 200


def test_list_returns_the_bodys_tuples(client):
    client.app.state.authz.tuples = [
        {"user": "user:a", "relation": "editor", "object": "governance_body:cei"},
        {"user": "user:b", "relation": "admin", "object": "governance_body:cei"},
    ]
    r = client.get(f"{BASE}?governance_body=cei", headers=hdr())
    assert r.status_code == 200
    assert r.json()["governance_body"] == "cei"
    assert {p["user"] for p in r.json()["permissions"]} == {"user:a", "user:b"}


def test_check_reports_allowed(client):
    client.app.state.authz.tuples = [
        {"user": "user:a", "relation": "editor", "object": "governance_body:cei"}
    ]
    r = client.get(f"{BASE}/check?user=a&governance_body=cei&relation=editor", headers=hdr())
    assert r.status_code == 200
    assert r.json()["allowed"] is True


def test_no_route_can_address_an_edition_or_platform_object(client):
    """The object type is fixed by the route, so it cannot be supplied."""
    r = client.post(
        BASE,
        json={
            "user": "u9",
            "governance_body": "cei",
            "relation": "editor",
            "object_type": "platform",
        },
        headers=hdr(),
    )
    assert r.status_code == 200
    assert client.app.state.authz.writes == [("user:u9", "editor", "governance_body:cei")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_admin_api.py -v`
Expected: FAIL — every route returns 404; the router does not exist.

- [ ] **Step 3: Add the models**

Append to `src/martyrology_api/models.py`:

```python
class GrantIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: str
    governance_body: str
    relation: str


class PermissionOut(BaseModel):
    user: str
    governance_body: str
    relation: str


class PermissionListOut(BaseModel):
    governance_body: str
    permissions: list[PermissionOut]


class PermissionCheckOut(BaseModel):
    user: str
    governance_body: str
    relation: str
    allowed: bool
```

Import `ConfigDict` from `pydantic` if the module does not already import it.
`extra="ignore"` is what makes
`test_no_route_can_address_an_edition_or_platform_object` pass: an
`object_type` in the payload is discarded rather than honoured.

The three fields are plain `str` with **no** pydantic patterns, deliberately.
A pattern failure raises `RequestValidationError`, which
`install_problem_handlers` maps to **400**; the tests expect **422** with a
`invalid-grant` problem type. All field validation therefore lives in the
handler helpers written in Step 4, which are the contract.

- [ ] **Step 4: Write the router**

Create `src/martyrology_api/routers/admin.py`:

```python
import logging
import re

from fastapi import APIRouter, Depends, Query, Request

from ..auth import Identity, get_identity
from ..authz import AuthzError, user_ref
from ..models import GrantIn, PermissionCheckOut, PermissionListOut, PermissionOut
from ..problems import ApiProblem

router = APIRouter(prefix="/admin/permissions", tags=["admin"])
log = logging.getLogger(__name__)

VALID_RELATIONS = frozenset({"admin", "editor", "reader"})
BODY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")

# OpenFGA reports both "tuple already exists" and "tuple does not exist"
# with this code. The relation is allowlisted and the object type is fixed
# before the call, so by the time it is observed it can only mean one of
# those two — both of which are the caller's desired end state.
IDEMPOTENT_CODE = "write_failed_due_to_invalid_input"


async def _authenticated(identity: Identity | None = Depends(get_identity)) -> Identity:
    if identity is None:
        raise ApiProblem(401, "Authentication required", type_slug="authentication-required")
    return identity


def _invalid(detail: str) -> ApiProblem:
    return ApiProblem(422, "Invalid request", detail=detail, type_slug="invalid-grant")


def _body_ref(body_id: str) -> str:
    if not BODY_RE.fullmatch(body_id):
        raise _invalid(f"'{body_id}' is not a valid governance body id.")
    return f"governance_body:{body_id}"


def _normalise_user(user: str) -> str:
    bare = user[5:] if user.startswith("user:") else user
    if not USER_RE.fullmatch(bare):
        raise _invalid(f"'{user}' is not a valid user id.")
    return f"user:{bare}"


def _relation(relation: str) -> str:
    if relation not in VALID_RELATIONS:
        raise _invalid(
            f"'{relation}' is not grantable. Valid relations: "
            f"{', '.join(sorted(VALID_RELATIONS))}."
        )
    return relation


async def _require_body_admin(request: Request, identity: Identity, body_ref: str) -> None:
    allowed = await request.app.state.authz.check_object(user_ref(identity), "admin", body_ref)
    if not allowed:
        raise ApiProblem(
            403,
            "Forbidden",
            detail=f"No admin permission on '{body_ref}'.",
            type_slug="forbidden",
        )


async def _mutate(request: Request, identity: Identity, op: str, ref: str, rel: str, obj: str):
    fn = request.app.state.authz.write if op == "grant" else request.app.state.authz.delete
    outcome = "ok"
    try:
        await fn(ref, rel, obj)
    except AuthzError as exc:
        if exc.code != IDEMPOTENT_CODE:
            outcome = f"error:{exc.code or exc.status}"
            log.warning(
                "permission %s by %s: %s %s on %s -> %s",
                op, user_ref(identity), ref, rel, obj, outcome,
            )
            raise ApiProblem(
                502,
                "Authorization store error",
                detail="The authorization store rejected the change.",
                type_slug="authz-store-error",
            ) from exc
        outcome = "noop"
    log.info(
        "permission %s by %s: %s %s on %s -> %s",
        op, user_ref(identity), ref, rel, obj, outcome,
    )


@router.get("", response_model=PermissionListOut)
async def list_permissions(
    request: Request,
    governance_body: str = Query(...),
    relation: str | None = Query(default=None),
    identity: Identity = Depends(_authenticated),
):
    obj = _body_ref(governance_body)
    rel = _relation(relation) if relation is not None else ""
    await _require_body_admin(request, identity, obj)
    tuples = await request.app.state.authz.read_tuples(obj, rel)
    return PermissionListOut(
        governance_body=governance_body,
        permissions=[
            PermissionOut(
                user=t.get("user", ""),
                governance_body=governance_body,
                relation=t.get("relation", ""),
            )
            for t in tuples
            if t.get("relation") in VALID_RELATIONS
        ],
    )


@router.post("", response_model=PermissionOut)
async def grant_permission(
    request: Request,
    body: GrantIn,
    identity: Identity = Depends(_authenticated),
):
    obj = _body_ref(body.governance_body)
    rel = _relation(body.relation)
    ref = _normalise_user(body.user)
    await _require_body_admin(request, identity, obj)
    await _mutate(request, identity, "grant", ref, rel, obj)
    return PermissionOut(user=ref, governance_body=body.governance_body, relation=rel)


@router.delete("", response_model=PermissionOut)
async def revoke_permission(
    request: Request,
    user: str = Query(...),
    governance_body: str = Query(...),
    relation: str = Query(...),
    identity: Identity = Depends(_authenticated),
):
    obj = _body_ref(governance_body)
    rel = _relation(relation)
    ref = _normalise_user(user)
    await _require_body_admin(request, identity, obj)
    await _mutate(request, identity, "revoke", ref, rel, obj)
    return PermissionOut(user=ref, governance_body=governance_body, relation=rel)


@router.get("/check", response_model=PermissionCheckOut)
async def check_permission(
    request: Request,
    user: str = Query(...),
    governance_body: str = Query(...),
    relation: str = Query(...),
    identity: Identity = Depends(_authenticated),
):
    obj = _body_ref(governance_body)
    rel = _relation(relation)
    ref = _normalise_user(user)
    await _require_body_admin(request, identity, obj)
    allowed = await request.app.state.authz.check_object(ref, rel, obj)
    return PermissionCheckOut(
        user=ref, governance_body=governance_body, relation=rel, allowed=allowed
    )
```

`_body_ref` / `_relation` / `_normalise_user` are the only validators. They
raise `ApiProblem(422, ..., type_slug="invalid-grant")`, which is what the
tests assert; keeping validation out of pydantic is what makes that status
reachable.

- [ ] **Step 5: Mount the router**

In `src/martyrology_api/app.py`, add `admin` to the routers import:

```python
from .routers import admin, curation, discovery, read
```

and mount it alongside the others:

```python
    app.include_router(admin.router, prefix="/api/v1")
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_admin_api.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole suite and the linter**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: PASS. `tests/test_openapi.py` may assert on the route inventory; if
it does, extend it to include the four new paths.

- [ ] **Step 8: Commit**

```bash
git add src/martyrology_api/routers/admin.py src/martyrology_api/models.py \
        src/martyrology_api/app.py tests/test_admin_api.py
git commit -m "Add a grant endpoint scoped to governance bodies"
```

---

### Task 6: Platform type, roles, and handoff — in cdcf-infra

This task happens in a **different repository**:
`/home/johnrdorazio/development/CatholicOS_org/cdcf-infra`, on its own
branch, with its own PR. Do not commit it to martyrology-api.

**Files:**
- Modify: `auth/models/Martyrology.json`
- Modify: `auth/models/Martyrology.tuples.json`
- Modify: `auth/setup-zitadel.sh` (`do_provision_martyrology`)
- Modify: `auth/handoffs/martyrology.md`

**Interfaces:**
- Consumes: nothing from Tasks 1-5 — but the API cannot resolve superuser
  inheritance until the model here is uploaded and
  `MARTYROLOGY_OPENFGA_MODEL_ID` is repointed at the new model.
- Produces: `platform` type; `governance_body.on_platform` relation; the
  three Zitadel role definitions.

- [ ] **Step 1: Create the branch**

```bash
cd /home/johnrdorazio/development/CatholicOS_org/cdcf-infra
git checkout main && git pull
git checkout -b feat/martyrology-platform-superuser
```

- [ ] **Step 2: Add the platform type to the model**

In `auth/models/Martyrology.json`, insert a new type definition before
`governance_body`:

```json
    {
      "type": "platform",
      "relations": {
        "superuser": { "this": {} }
      },
      "metadata": {
        "relations": {
          "superuser": { "directly_related_user_types": [{ "type": "user" }] }
        }
      }
    },
```

Then replace the `governance_body` type definition with:

```json
    {
      "type": "governance_body",
      "relations": {
        "on_platform": { "this": {} },
        "admin": {
          "union": {
            "child": [
              { "this": {} },
              {
                "tupleToUserset": {
                  "tupleset": { "object": "", "relation": "on_platform" },
                  "computedUserset": { "object": "", "relation": "superuser" }
                }
              }
            ]
          }
        },
        "editor": {
          "union": {
            "child": [
              { "this": {} },
              { "computedUserset": { "object": "", "relation": "admin" } }
            ]
          }
        },
        "reader": {
          "union": {
            "child": [
              { "this": {} },
              { "computedUserset": { "object": "", "relation": "editor" } }
            ]
          }
        }
      },
      "metadata": {
        "relations": {
          "on_platform": { "directly_related_user_types": [{ "type": "platform" }] },
          "admin":  { "directly_related_user_types": [{ "type": "user" }] },
          "editor": { "directly_related_user_types": [{ "type": "user" }] },
          "reader": { "directly_related_user_types": [{ "type": "user" }] }
        }
      }
    },
```

The `edition` type is unchanged: a superuser becomes `admin` on every body,
and `can_admin` / `can_edit` / `can_read_texts` already derive from the
governing body through `governed_by`.

- [ ] **Step 3: Add the structural tuples**

In `auth/models/Martyrology.tuples.json`, add one `on_platform` tuple per
governance body, matching the file's existing entry shape:

```json
{ "user": "platform:martyrology", "relation": "on_platform", "object": "governance_body:editio_typica" },
{ "user": "platform:martyrology", "relation": "on_platform", "object": "governance_body:cei" },
{ "user": "platform:martyrology", "relation": "on_platform", "object": "governance_body:unassigned_en_translatio" }
```

Read the file first and match its existing formatting exactly — the eight
`governed_by` tuples already there define the shape. No `superuser` tuple is
seeded here; the bootstrap grant is made out-of-band in Step 6.

- [ ] **Step 4: Define the Zitadel roles**

`do_provision_martyrology` (line 741) currently creates the Project and API
app with no roles. The existing helper is `create_roles PROJECT_ID
"key:Display" ...` (defined at line 366); LitCal calls it at line 708 as
`create_roles "$project_id" "${LITCAL_ROLES[@]}"`. Reuse it — do not write a
new helper.

Declare the catalogue next to `LITCAL_ROLES` (line 288):

```bash
# `developer` is defined but enforced by nothing today: martyrology-api has no
# API-consumer features to gate. It exists so the role vocabulary is uniform
# across CDCF properties before principals are onboarded, since issuing a role
# to already-onboarded principals later is the disruptive path.
MARTYROLOGY_ROLES=("admin:System Administrator" \
                   "martyrology_editor:Martyrology Editor" \
                   "developer:Developer (API consumer)")
```

Match the existing line-continuation style of `LITCAL_ROLES` exactly. In
`do_provision_martyrology`, call it immediately after `create_project`
returns:

```bash
    create_roles "$project_id" "${MARTYROLOGY_ROLES[@]}"
```

`create_project` (line 340) already sets `projectRoleAssertion: true`, so no
extra step is needed for roles to appear in tokens — and the live
introspection probe confirmed they do.

Three comment blocks now state the opposite of the truth and must be
rewritten, not merely deleted:

- lines 35-36 in the file header ("NO project roles: martyrology-api performs
  zero Zitadel role/scope checks")
- line 77's `--provision-martyrology` usage line ("client_secret_basic, NO
  roles")
- lines 734-739, the `do_provision_martyrology` NOTE arguing that unused roles
  are a liability, and line 781's emitted `"# No project roles were created —
  all authorization is OpenFGA."`

The corrected position: three roles exist; `admin` and `martyrology_editor`
are a coarse population gate on curation writes; neither bypasses OpenFGA,
which remains the authority on every per-resource decision.

- [ ] **Step 5: Upload the model and seed, then record the new model ID**

```bash
cd /home/johnrdorazio/development/CatholicOS_org/cdcf-infra
./auth/setup-openfga.sh --target production --create-martyrology-store
```

`--create-martyrology-store` is the shorthand for `--create-store
Martyrology`, and `do_create_store` is idempotent against an existing store:
`create_or_find_store` reuses store `01KZ1M9NJR1JHTMTV091X5DMYZ`,
`upload_model_if_changed` uploads because the type definitions changed, and
`seed_tuples_if_present` writes the three new `on_platform` tuples while
skipping the eight `governed_by` tuples that already exist.

Do **not** use `--seed-tuples Martyrology` here: it seeds tuples with no model
upload, so the `on_platform` tuples would be written against a model that has
no such relation.

The command prints `OPENFGA_MODEL_ID=<new id>` in its handoff block. Capture
it — step 6 needs it.

- [ ] **Step 6: Bootstrap the first superuser and repoint the model pin**

This is the only grant made outside the API, and it is what breaks the
chicken-and-egg: the grant endpoint requires a body admin, and until this
tuple exists there is none.

```bash
# On the VPS, with the preshared key available:
curl -sS -X POST "https://authz.catholicdigitalcommons.org/stores/$STORE/write" \
  -H "Authorization: Bearer $OPENFGA_PRESHARED_KEY" \
  -H "Content-Type: application/json" \
  -d '{"writes":{"tuple_keys":[{"user":"user:<OPERATOR_SUB>","relation":"superuser","object":"platform:martyrology"}]}}'
```

Then update `/etc/martyrology/api.env`:

```
MARTYROLOGY_ZITADEL_PROJECT_ID=384518610174869507
MARTYROLOGY_OPENFGA_API_TOKEN=<the preshared key>
MARTYROLOGY_OPENFGA_MODEL_ID=<the new model ID from step 5>
```

Repointing the model ID is mandatory. The old model has no `on_platform`
relation, so pinned checks against it will resolve superuser inheritance to
false — safely, but uselessly.

- [ ] **Step 7: Update the handoff doc**

In `auth/handoffs/martyrology.md`, document the three roles and which are
enforced, the `platform` type and the superuser bootstrap, and the grant
endpoint's scope and authorization rule.

Correct the standing inaccuracy: the doc claims `admin` "is the intended
role-granting authority", which nothing implemented. Granting authority is
the OpenFGA `admin` relation on a governance body, reached either directly or
through `platform:martyrology`.

- [ ] **Step 8: Commit and open the PR**

```bash
git add auth/models/Martyrology.json auth/models/Martyrology.tuples.json \
        auth/setup-zitadel.sh auth/handoffs/martyrology.md
git commit -m "Add the platform superuser type and Martyrology project roles"
git push -u origin feat/martyrology-platform-superuser
gh pr create --fill
```

- [ ] **Step 9: Verify end to end**

After the API is released and deployed, as the operator:

```bash
# 1. Grants are reachable — 200, not 403.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://api.romanmartyrology.com/api/v1/admin/permissions?governance_body=cei"

# 2. Restricted texts resolve for a licensed reader — text, not null.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://api.romanmartyrology.com/api/v1/editions/martyrologium_romanum_2004/01/01"

# 3. A principal with no project role is refused with the distinct type.
#    Expect 403 and type ".../problems/missing-role".
```

Superuser inheritance is a property of the model, not of the API codebase;
step 1 returning 200 is what proves it resolved.

---

## Rollout order

Tasks 1-5 are safe to merge and deploy on their own: curation is already
denied for everyone today, so the role gate introduces no regression, and
Task 1 alone repairs restricted-text reads for anyone who already holds a
reader grant. Task 6 must complete before any superuser exists or any grant
can be made.

The one ordering trap is the model ID pin. Task 6 Step 5 mints a new model
ID; Step 6 must repoint `MARTYROLOGY_OPENFGA_API_TOKEN`,
`MARTYROLOGY_ZITADEL_PROJECT_ID`, and `MARTYROLOGY_OPENFGA_MODEL_ID` together
in the same edit, and the service must be restarted afterwards.
