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
    """Admins on governance_body:cei; records every mutation.

    `fail` controls write/delete (the mutation call itself). `fail_read`
    independently controls read_tuples (the confirmation call `_mutate`
    makes after an idempotent-coded mutation failure) so a test can make
    the mutation fail in one way and the confirming read fail — or
    succeed — independently.
    """

    def __init__(self, admins=frozenset({"user:adm"}), tuples=None, fail=None, fail_read=None):
        self.admins = admins
        self.tuples = tuples if tuples is not None else []
        self.fail = fail
        self.fail_read = fail_read
        self.writes: list[tuple] = []
        self.deletes: list[tuple] = []

    async def check_object(self, user, relation, obj):
        if relation == "admin" and obj == "governance_body:cei":
            return user in self.admins
        if relation == "editor" and obj == "governance_body:cei" and user in self.admins:
            # Mirrors the real model's union: editor: [user] or admin.
            return True
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
        if self.fail_read:
            raise self.fail_read
        return [
            t
            for t in self.tuples
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
    # The postcondition already holds (the tuple exists), so the
    # idempotent-coded failure is confirmed as the caller's desired
    # end state and reported as success.
    client.app.state.authz.tuples = [
        {"user": "user:u9", "relation": "editor", "object": "governance_body:cei"}
    ]
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "exists")
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 200


def test_duplicate_grant_with_unconfirmed_postcondition_is_502(client):
    # The idempotent-coded failure fires, but the tuple still does not
    # exist afterward: the desired end state (grant holds) cannot be
    # confirmed, so this must not be swallowed as success.
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "exists")
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 502
    assert client.app.state.authz.writes == []


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
    # The postcondition already holds (no such tuple), so the
    # idempotent-coded failure is confirmed as the caller's desired
    # end state and reported as success.
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "missing")
    r = client.request(
        "DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor", headers=hdr()
    )
    assert r.status_code == 200


def test_revoke_with_unconfirmed_postcondition_is_502(client):
    # The idempotent-coded failure fires, but the tuple is still present
    # afterward: the desired end state (revoke holds) cannot be confirmed,
    # so this must not be swallowed as success.
    client.app.state.authz.tuples = [
        {"user": "user:u9", "relation": "editor", "object": "governance_body:cei"}
    ]
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "missing")
    r = client.request(
        "DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor", headers=hdr()
    )
    assert r.status_code == 502
    assert client.app.state.authz.deletes == []


def test_revoke_editor_from_an_admin_holder_confirms_via_direct_tuples(client):
    """The postcondition must be confirmed against the direct tuple, not
    the computed relation: editor is unioned with admin (editor: [user]
    or admin), so check_object("editor") on an admin-holder always
    returns True even when no direct editor tuple exists. Confirming with
    check_object would wrongly report 502 for a revoke that actually
    succeeded; confirming with read_tuples (direct tuples only) correctly
    reports 200."""
    client.app.state.authz.admins = frozenset({"user:adm", "user:u9"})
    client.app.state.authz.tuples = [
        {"user": "user:u9", "relation": "admin", "object": "governance_body:cei"},
    ]
    # Sanity check: the FakeAuthz stub models the union, so check_object
    # would (wrongly, if used as the oracle) report "editor" as present.
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "missing")
    r = client.request(
        "DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor", headers=hdr()
    )
    assert r.status_code == 200


def test_duplicate_grant_confirmation_read_failure_is_502(client):
    """If the confirming read_tuples call itself raises (infrastructure
    failure), that is an unconfirmed outcome, not a confirmed one."""
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "exists")
    client.app.state.authz.fail_read = AuthzError(500, "internal_error", "boom")
    r = client.post(
        BASE,
        json={"user": "u9", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 502


def test_revoke_confirmation_read_failure_is_502(client):
    """Same as above, for the revoke direction: a failed confirmation read
    must not be silently treated as a confirmed revoke."""
    client.app.state.authz.fail = AuthzError(400, "write_failed_due_to_invalid_input", "missing")
    client.app.state.authz.fail_read = AuthzError(500, "internal_error", "boom")
    r = client.request(
        "DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor", headers=hdr()
    )
    assert r.status_code == 502


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


def test_list_permissions_is_not_publicly_cached(client):
    r = client.get(f"{BASE}?governance_body=cei", headers=hdr())
    assert r.headers["cache-control"] == "private, max-age=0"


def test_check_permission_is_not_publicly_cached(client):
    r = client.get(f"{BASE}/check?user=a&governance_body=cei&relation=editor", headers=hdr())
    assert r.headers["cache-control"] == "private, max-age=0"


def test_list_permissions_is_502_when_the_store_is_unreachable(client):
    client.app.state.authz.fail_read = AuthzError(500, "internal_error", "boom")
    r = client.get(f"{BASE}?governance_body=cei", headers=hdr())
    assert r.status_code == 502


def test_delete_unauthenticated_is_401(client):
    r = client.request("DELETE", f"{BASE}?user=u9&governance_body=cei&relation=editor")
    assert r.status_code == 401


def test_delete_non_body_admin_is_403(client):
    r = client.request(
        "DELETE",
        f"{BASE}?user=u9&governance_body=cei&relation=editor",
        headers=hdr("plain"),
    )
    assert r.status_code == 403
    assert client.app.state.authz.deletes == []


def test_check_unauthenticated_is_401(client):
    r = client.get(f"{BASE}/check?user=a&governance_body=cei&relation=editor")
    assert r.status_code == 401


def test_check_non_body_admin_is_403(client):
    r = client.get(f"{BASE}/check?user=a&governance_body=cei&relation=editor", headers=hdr("plain"))
    assert r.status_code == 403


def test_double_prefixed_user_is_422(client):
    r = client.post(
        BASE,
        json={"user": "user:user:x", "governance_body": "cei", "relation": "editor"},
        headers=hdr(),
    )
    assert r.status_code == 422
    assert client.app.state.authz.writes == []


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
