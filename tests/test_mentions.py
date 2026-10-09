import hashlib
import json
import logging

import pytest
from pydantic import ValidationError

from martyrology_api.mentions import (
    MentionIn,
    lands,
    load_mentions,
    served,
    span_check,
    utf16_slice,
)
from martyrology_api.models import FootnoteOut, MentionFootnote, MentionOut


def _check(words: str) -> str:
    """The definition: the first 8 hex digits of SHA-256 over the UTF-8 bytes of the words."""
    return hashlib.sha256(words.encode("utf-8")).hexdigest()[:8]


def _m(words: str = "x", **kw) -> MentionIn:
    base = {"kind": "person", "where": "text", "start": 0, "end": 1, "check": _check(words)}
    return MentionIn.model_validate(base | kw)


def test_span_check_is_pinned_by_a_known_vector():
    assert span_check("Basilíi") == "4729ea69"  # "í" precomposed, U+00ED
    assert span_check("Concordii") == _check("Concordii")


def test_span_check_does_not_normalize():
    decomposed = "Basilíi"  # "i" followed by a combining acute accent
    assert span_check(decomposed) == "16d99f80" != span_check("Basilíi")


def test_utf16_slice_counts_code_units_not_code_points():
    s = "\U0001d504 Romæ"  # the first character lies outside the BMP: two UTF-16 code units
    assert utf16_slice(s, 3, 7) == "Romæ"
    assert s[3:7] != "Romæ"  # Python's own indices would land one character late


def test_utf16_slice_refuses_a_span_past_the_end_or_through_a_surrogate_pair():
    assert utf16_slice("Romæ", 0, 5) is None
    assert utf16_slice("\U0001d504", 0, 1) is None


def test_a_text_mention_lands_on_its_words_and_nowhere_else():
    romae = _m("Romæ", start=7, end=11)
    assert lands(romae, "Natale Romæ", []) == "Romæ"
    assert lands(romae, "Natale Romæ, et", []) == "Romæ"  # text after the span is no matter
    assert lands(romae, "Natale Romam", []) is None  # the words changed
    assert lands(_m("Romæ", start=0, end=4), "Natale Romæ", []) is None  # the span moved
    assert lands(romae, None, []) is None  # withheld or unaligned text


def test_a_footnote_mention_lands_in_its_footnote_counted_from_one():
    notes = [FootnoteOut(mark="1", after=None, text="Quorum nomina: sancti Narcissus.")]
    narcissus = _m("Narcissus", where={"footnote": 1}, start=22, end=31)
    assert lands(narcissus, "Textus.", notes) == "Narcissus"
    # A text long enough for the span to be in range: `where` selects the footnote, not the text.
    long_text = "Quorum nomina: sancti Narcissi et aliorum."
    assert len(long_text) >= 31
    assert lands(narcissus.model_copy(update={"where": "text"}), long_text, notes) is None
    assert lands(_m("Narcissus", where={"footnote": 2}, start=22, end=31), "Textus.", notes) is None


def test_served_fills_form_from_the_text_and_leaves_the_check_behind():
    place = _m("Romæ", kind="place", start=7, end=11, qid="Q220")
    stale = _m("Romæ", start=0, end=4)
    out = served([place, stale], "Natale Romæ", [])
    assert out == [
        MentionOut(kind="place", where="text", start=7, end=11, form="Romæ", qid="Q220", name=None)
    ]
    assert "check" not in out[0].model_dump()
    assert out[0].model_dump()["name"] is None  # always present, null for a place


@pytest.mark.parametrize(
    "bad",
    [
        {"end": 0},  # end must follow start
        {"start": -1},
        {"check": "4729EA69"},  # lowercase hex only
        {"check": "4729ea6"},  # eight digits
        {"kind": "event"},
        {"where": {"footnote": 0}},  # footnotes count from 1
        {"where": {"footnote": 1, "mark": "a"}},  # nothing else in `where`
        {"where": "margin"},
    ],
)
def test_a_malformed_mention_is_refused(bad):
    with pytest.raises(ValidationError):
        _m(**bad)


def test_a_place_needs_no_name_and_a_qid_may_be_undecided():
    m = _m(kind="place")
    assert m.name is None and m.qid is None
    assert isinstance(_m(where={"footnote": 3}).where, MentionFootnote)


def test_load_mentions_reads_the_editions_and_the_texts_commit(tmp_path):
    path = tmp_path / "mentions.json"
    path.write_text(
        json.dumps(
            {
                "$comment": "…",
                "texts": {"commit": "4e89ec8"},
                "editions": {
                    "martyrologium_romanum_2004": {
                        "mr:0101-basilius": [
                            {
                                "kind": "person",
                                "where": "text",
                                "start": 46,
                                "end": 53,
                                "check": "4729ea69",
                                "name": "Basilius",
                                "qid": None,
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    got = load_mentions(path)
    assert got.texts_commit == "4e89ec8"
    assert got.editions["martyrologium_romanum_2004"]["mr:0101-basilius"][0].check == "4729ea69"


def test_a_file_without_a_texts_commit_reads_as_unknown(tmp_path):
    path = tmp_path / "mentions.json"
    path.write_text('{"editions": {}}', encoding="utf-8")
    assert load_mentions(path).texts_commit is None


def test_a_missing_mentions_file_is_no_mentions_with_a_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="martyrology_api.mentions")
    got = load_mentions(tmp_path / "mentions.json")
    assert got.editions == {} and got.texts_commit is None
    assert "mentions.json is missing" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        '{"editions": []}',
        '{"editions": {"e": {"i": [{}]}}}',
        # crmedr stores no printed words: a mention without its check is malformed
        '{"editions": {"e": {"i": [{"kind": "place", "where": "text", "start": 0, "end": 1}]}}}',
    ],
)
def test_a_malformed_mentions_file_fails_at_once_naming_it(tmp_path, body):
    path = tmp_path / "mentions.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=r"mentions\.json"):
        load_mentions(path)
