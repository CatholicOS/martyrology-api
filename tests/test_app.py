import logging

import pytest


@pytest.mark.parametrize(
    "overrides,expected",
    [
        (
            {
                "openfga_api_url": "https://fga.example",
                "openfga_store_id": "s1",
                "openfga_api_token": "",
            },
            "MARTYROLOGY_OPENFGA_API_TOKEN",
        ),
        (
            {
                "openfga_api_url": "https://fga.example",
                "openfga_store_id": "",
                "openfga_api_token": "k",
            },
            "MARTYROLOGY_OPENFGA_STORE_ID",
        ),
        (
            {"zitadel_issuer": "https://issuer.example", "zitadel_project_id": ""},
            "MARTYROLOGY_ZITADEL_PROJECT_ID is empty",
        ),
    ],
)
def test_partial_configuration_warns(make_client, caplog, overrides, expected):
    with caplog.at_level(logging.WARNING, logger="martyrology_api.app"):
        make_client(**overrides)
    assert any(expected in record.message for record in caplog.records)


def test_openfga_warning_does_not_fire_when_fully_configured(make_client, caplog):
    with caplog.at_level(logging.WARNING, logger="martyrology_api.app"):
        make_client(
            openfga_api_url="https://fga.example",
            openfga_store_id="s1",
            openfga_api_token="k",
        )
    assert not any("MARTYROLOGY_OPENFGA_API_TOKEN" in r.message for r in caplog.records)


def test_openfga_warning_does_not_fire_when_entirely_unconfigured(make_client, caplog):
    """All three empty is a deployment that simply does not use OpenFGA."""
    with caplog.at_level(logging.WARNING, logger="martyrology_api.app"):
        make_client(openfga_api_url="", openfga_store_id="", openfga_api_token="")
    assert not any("OpenFGA is partially configured" in r.message for r in caplog.records)


def test_openfga_warning_fires_when_only_the_token_is_set(make_client, caplog):
    """A token with no URL or store is partial, not absent — it denies everything."""
    with caplog.at_level(logging.WARNING, logger="martyrology_api.app"):
        make_client(openfga_api_token="k")
    warnings = [r.message for r in caplog.records if "partially configured" in r.message]
    assert warnings, "expected a partial-configuration warning"
    assert "MARTYROLOGY_OPENFGA_API_URL" in warnings[0]
    assert "MARTYROLOGY_OPENFGA_STORE_ID" in warnings[0]


def test_zitadel_warning_does_not_fire_when_fully_configured(make_client, caplog):
    with caplog.at_level(logging.WARNING, logger="martyrology_api.app"):
        make_client(
            zitadel_issuer="https://issuer.example",
            zitadel_client_id="c1",
            zitadel_client_secret="s1",
            zitadel_project_id="p1",
        )
    assert not any("MARTYROLOGY_ZITADEL_PROJECT_ID" in r.message for r in caplog.records)


def test_zitadel_warning_does_not_fire_when_issuer_unset(make_client, caplog):
    with caplog.at_level(logging.WARNING, logger="martyrology_api.app"):
        make_client(zitadel_project_id="p1")
    assert not any("MARTYROLOGY_ZITADEL_PROJECT_ID" in r.message for r in caplog.records)
