import openapi_spec_validator
import pytest


@pytest.fixture
def client(make_client):
    return make_client()


def test_openapi_schema_is_valid(client):
    schema = client.app.openapi()
    # FastAPI >=0.100 emits OpenAPI 3.1; `validate` auto-selects the
    # matching (3.1) dialect validator from the schema's `openapi` field.
    assert schema["openapi"].startswith("3.1")
    openapi_spec_validator.validate(schema)


def test_openapi_schema_metadata(client):
    schema = client.app.openapi()
    assert schema["info"]["title"] == "Roman Martyrology API"


def test_openapi_schema_has_expected_paths(client):
    schema = client.app.openapi()
    paths = schema["paths"]
    assert "/api/v1/elogia/{rest}" in paths
    assert "/api/v1/editions/{edition_id}" in paths


def test_openapi_every_route_declares_responses(client):
    """Sanity loop: every operation on every path must declare at least one
    response with either a schema-bearing content type or a plain
    description (e.g. a 204/304 with no body)."""
    schema = client.app.openapi()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in ("get", "put", "post", "patch", "delete", "options", "head"):
                continue
            responses = operation.get("responses")
            assert responses, f"{method.upper()} {path} declares no responses"
            for status, response in responses.items():
                assert "description" in response, (
                    f"{method.upper()} {path} response {status} has no description"
                )


def test_openapi_edition_params_enumerate_the_editions_they_accept(client):
    """Edition parameters reference shared schemas whose enums come from the CLBDR
    catalog and the attached texts loaded at startup: reads list the attached editions,
    creation lists the catalogued ones without texts, other writes list the catalog."""
    schema = client.app.openapi()
    catalogued = set(client.app.state.registry.editions)
    attached = client.app.state.store.available()
    components = schema["components"]["schemas"]
    assert components["EditionId"]["enum"] == sorted(catalogued & attached)
    assert components["NewEditionId"]["enum"] == sorted(catalogued - attached)
    assert components["CataloguedEditionId"]["enum"] == sorted(catalogued)
    # The fixtures exercise all three lists distinctly.
    assert catalogued & attached and catalogued - attached

    def ref(name):
        return {"$ref": f"#/components/schemas/{name}"}

    found = {}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for param in operation.get("parameters", []):
                if param["name"] in ("edition_id", "edition"):
                    found[(method, path)] = param["schema"]
    assert found.pop(("put", "/api/v1/editions/{edition_id}")) == ref("NewEditionId")
    for read_path in ("/api/v1/elogia", "/api/v1/elogia/{rest}"):
        assert found.pop(("get", read_path)) == {"anyOf": [ref("EditionId"), {"type": "null"}]}
    assert len(found) == 6
    for operation, param_schema in found.items():
        assert param_schema == ref("CataloguedEditionId"), operation


def test_openapi_documents_the_mentions_of_a_eulogy(client):
    schema = client.app.openapi()
    c = schema["components"]["schemas"]
    mention = {"$ref": "#/components/schemas/MentionOut"}
    assert c["ElogiumOut"]["properties"]["mentions"]["items"] == mention
    assert c["EditionPlacementOut"]["properties"]["mentions"]["items"] == mention
    m = c["MentionOut"]
    assert set(m["required"]) == {"kind", "where", "start", "end", "form", "qid", "name"}
    assert "check" not in m["properties"]  # crmedr's hash is not part of the response
    assert m["properties"]["kind"]["enum"] == ["person", "place"]
    assert c["MentionFootnote"]["properties"]["footnote"]["minimum"] == 1
    elogia = schema["paths"]["/api/v1/elogia/{rest}"]["get"]["responses"]["200"]
    assert elogia["content"]["application/json"]["schema"]["anyOf"] == [
        {"$ref": "#/components/schemas/DayOut"},
        {"$ref": "#/components/schemas/MonthOut"},
    ]
    one = schema["paths"]["/api/v1/elogium/{canonical_id}"]["get"]["responses"]["200"]
    assert one["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EulogyOut"
    }
