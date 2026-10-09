import hashlib
import json
import logging

import pytest
from pydantic import ValidationError

from martyrology_api.mentions import (
    MentionIn,
    compare_texts_pins,
    lands,
    load_mentions,
    served,
    span_check,
    utf16_slice,
)
from martyrology_api.models import FootnoteOut, MentionFootnote, MentionOut
from martyrology_api.registry import Registry
from martyrology_api.store import Store


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


def test_utf16_slice_misses_on_a_span_starting_inside_a_surrogate_pair():
    assert utf16_slice("a\U0001d504b", 2, 3) is None
    assert utf16_slice("a\U0001d504b", 1, 3) == "\U0001d504"


def test_utf16_slice_misses_on_a_lone_surrogate_instead_of_raising():
    assert utf16_slice("a\ud800b", 0, 3) is None
    assert utf16_slice("Romæ\ud800", 0, 4) is None  # even where the span avoids the lone one


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


def test_served_gives_a_footnote_mention_its_footnote_as_where():
    notes = [FootnoteOut(mark="1", after=None, text="Quorum nomina: sancti Narcissus.")]
    narcissus = _m("Narcissus", where={"footnote": 1}, start=22, end=31, name="Narcissus")
    assert served([narcissus], "Textus.", notes) == [
        MentionOut(
            kind="person",
            where=MentionFootnote(footnote=1),
            start=22,
            end=31,
            form="Narcissus",
            qid=None,
            name="Narcissus",
        )
    ]


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


ED = "martyrologium_romanum_2004"


def _store(crmedr_path, clbdr_path, data_paths) -> Store:
    return Store(data_paths, Registry.load(crmedr_path, clbdr_path))


def test_the_store_serves_no_mentions_before_they_are_attached(crmedr_path, clbdr_path, data_paths):
    assert _store(crmedr_path, clbdr_path, data_paths).mentions(ED) == {}


def test_the_fixture_checks_are_the_definitions(crmedr_path, clbdr_path, data_paths):
    # The mentions the fixture means to serve, each hashed here, straight from the fixture's
    # texts, to the check the fixture gives: the fixture and the definition agree, whatever the
    # Store keeps.
    s = _store(crmedr_path, clbdr_path, data_paths)
    raw = json.loads((crmedr_path / "data/mentions.json").read_text(encoding="utf-8"))["editions"]
    intended = [
        (ED, "mr:0102-concordius", 0),
        (ED, "mr:0102-concordius", 1),
        (ED, "mr:0102-argeus-et-socii", 0),
        ("martyrologium_romanum_2004_it_IT", "mr:0102-concordius", 0),
        ("martyrologium_romanum_1749", "mr:0102-concordius", 0),
    ]
    for edition, cid, i in intended:
        m = raw[edition][cid][i]
        if m["where"] == "text":
            target = s._texts(edition)[cid]
        else:
            target = s.footnotes(edition)[cid][m["where"]["footnote"] - 1].text
        assert target is not None
        units = target.encode("utf-16-le")[m["start"] * 2 : m["end"] * 2]
        words = units.decode("utf-16-le")
        assert hashlib.sha256(words.encode("utf-8")).hexdigest()[:8] == m["check"]


def test_the_store_keeps_the_mentions_that_land_and_logs_the_rest(
    crmedr_path, clbdr_path, data_paths, caplog
):
    caplog.set_level(logging.INFO, logger="martyrology_api.mentions")
    s = _store(crmedr_path, clbdr_path, data_paths)
    s.attach_mentions(load_mentions(crmedr_path / "data/mentions.json"))

    assert [(m.start, m.end) for m in s.mentions(ED)["mr:0102-concordius"]] == [(0, 17), (26, 35)]
    argeus = s.mentions(ED)["mr:0102-argeus-et-socii"]
    assert [(m.name, m.where) for m in argeus] == [("Narcissus", MentionFootnote(footnote=1))]
    assert "mr:0109-nusquam" not in s.mentions(ED)
    assert s.mentions("martyrologium_romanum_2004_it_IT")["mr:0102-concordius"][0].end == 9
    assert s.mentions("martyrologium_romanum_1749")["mr:0102-concordius"][0].start == 15
    assert s.mentions("martyrologium_romanum_1630") == {}

    dropped = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(dropped) == 3
    assert all(d.startswith("Mention dropped:") for d in dropped)
    assert any("mr:0102-concordius" in d and "37-47" in d and "4b9b016f" in d for d in dropped)
    assert any("footnote 2" in d for d in dropped)  # a footnote the eulogy lacks
    assert any("mr:0109-nusquam" in d for d in dropped)  # a eulogy the edition does not print
    assert (
        "Mentions: 5 served, 3 dropped (offsets from martyrology-texts an unrecorded commit)."
        in caplog.text
    )
    assert "martyrologium_romanum_1630" in caplog.text  # said once: its texts are not attached


@pytest.mark.parametrize(
    ("extracted", "deployed", "warned"),
    [
        ("4e89ec8", "4e89ec8d1c0ffee", False),  # one abbreviates the other
        ("4e89ec8d1c0ffee", "4e89ec8", False),
        ("4e89ec8", "1234567", True),
        (None, "1234567", False),  # crmedr did not record it: nothing to compare
        ("4e89ec8", None, False),  # no bundle manifest (development): nothing to compare
    ],
)
def test_a_texts_commit_other_than_the_one_served_is_warned_of(extracted, deployed, warned, caplog):
    caplog.set_level(logging.WARNING, logger="martyrology_api.mentions")
    compare_texts_pins(extracted, deployed)
    assert bool(caplog.records) is warned
    if warned:
        assert extracted in caplog.text and deployed in caplog.text
