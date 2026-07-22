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
