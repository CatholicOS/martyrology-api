import pytest

from martyrology_api.problems import ApiProblem
from martyrology_api.registry import Registry
from martyrology_api.writer.validation import validate_month_payload, validate_or_raise


@pytest.fixture
def reg(crmedr_path, clbdr_path):
    return Registry.load(crmedr_path, clbdr_path)


GOOD_DAY = {"1": {"titulus": "t", "elogia": {"mr:0101-basilius": "x"}, "conclusio": None}}


def test_day_structured_valid(reg):
    assert validate_month_payload(GOOD_DAY, 1, "day-structured", reg) == []


def test_day_structured_errors(reg):
    bad = {
        "0": {"elogia": {}},  # invalid day
        "32": {"elogia": {}},  # invalid day
        "1": {"elogia": {"mr:0101-basilius": "x"}, "extra": 1},  # unknown key
        "2": {"elogia": {"mr:9999-nobody": "x"}},  # unknown id
        "3": {"elogia": {"mr:0101-basilius": ""}},  # empty text + dup with day 1
    }
    errs = validate_month_payload(bad, 1, "day-structured", reg)
    assert len(errs) >= 5
    assert any("appears more than once" in e for e in errs)


def test_deprecated_ids_are_accepted(reg):
    raw = {"1": {"elogia": {"mr:0101-circumcisio-domini": "x"}}}
    assert validate_month_payload(raw, 1, "day-structured", reg) == []


def test_flat_valid_and_errors(reg):
    assert validate_month_payload({"mr:0101-basilius": "x"}, 1, "flat", reg) == []
    errs = validate_month_payload(
        {
            "mr:0228-romanus": "x",  # anchor month 2 != file month 1
            "mr:9999-nobody": "x",  # unknown
            "mr:0101-basilius": "",
        },  # empty text
        1,
        "flat",
        reg,
    )
    assert len(errs) == 3


def test_validate_or_raise(reg):
    with pytest.raises(ApiProblem) as ei:
        validate_or_raise({"mr:9999-nobody": "x"}, 1, "flat", reg)
    assert ei.value.status == 422
    assert ei.value.extensions["errors"]


def test_padded_and_exotic_day_keys_rejected(reg):
    # Zero-padded day key "01" should be rejected (unpadded only)
    errs = validate_month_payload(
        {"01": {"elogia": {"mr:0101-basilius": "x"}}}, 1, "day-structured", reg
    )
    assert len(errs) == 1 and "unpadded" in errs[0]

    # Exotic digit "²" should produce clean error string, never raise ValueError
    errs2 = validate_month_payload({"²": {"elogia": {}}}, 1, "day-structured", reg)
    assert len(errs2) == 1  # clean error string, no ValueError raised

    # Unicode decimal digit mixed with ASCII ("1١" == Arabic-Indic digit one)
    # must be rejected outright: Python's int() would happily parse it
    # (int("1١") == 11), so the day-key regex must be pure ASCII.
    errs3 = validate_month_payload({"1١": {"elogia": {}}}, 1, "day-structured", reg)
    assert len(errs3) == 1 and "invalid day key" in errs3[0]
