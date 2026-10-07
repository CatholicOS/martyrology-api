import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "notationes_changeset.py"
_spec = importlib.util.spec_from_file_location("notationes_changeset", _PATH)
assert _spec is not None and _spec.loader is not None
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

TEXTS = {
    "mr:0111-leucius": "Brundusij S. Leucij Episcopi & confessoris.",
    "mr:0111-honorata": "Papîæ S. Honoratæ virginis.",
}
NOTES = [{"ref": "1-11|e", "mark": "e", "lemma": "Palæmonis"}]


def _note(**kw):
    op = {
        "op": "attach_note",
        "id": "1-11|f",
        "class": "no-mark",
        "day": "1-11",
        "mark": "f",
        "proposed": {"id": "mr:0111-leucius", "after": "Episcopi"},
        "texts": TEXTS,
        "notes": NOTES,
        "decision": None,
        "edited": None,
    }
    return op | kw


def _mark(**kw):
    return _note(uid="1-11|g|mark", id="1-11|g", mark="g", **{"class": "mark-without-note"}) | kw


def _margin(**kw):
    op = {
        "op": "place_margin",
        "id": "64|m|T. 2. A. 160.",
        "class": "doubt",
        "proposed": {"note": "1-11|f"},
        "candidates": [
            {"ref": "1-11|f", "kind": "note"},
            {"ref": "mr:0111-honorata", "kind": "eulogy"},
        ],
        "decision": None,
        "edited": None,
    }
    return op | kw


def _apply(*ops, placement=None):
    overrides = {}
    placement = {"64": {"m|T. 2. A. 160.": {"note": "1-11|f", "doubt": "low: ?"}}} | (
        placement or {}
    )
    n = C.apply_decisions({"operations": list(ops)}, overrides, placement)
    return n, overrides, placement


def test_undecided_ops_are_left():
    n, overrides, placement = _apply(_note(), _margin())
    assert n == 0 and overrides == {}
    assert placement["64"]["m|T. 2. A. 160."]["doubt"]


def test_accepted_note_records_its_proposal():
    _, overrides, _ = _apply(_note(decision="accept"))
    assert overrides == {"1-11|f": {"id": "mr:0111-leucius", "after": "Episcopi", "mark": "f"}}


def test_edited_note_takes_the_edits_and_null_anchor():
    op = _note(decision="edit", edited={"id": "mr:0111-honorata", "after": None, "mark": "g"})
    _, overrides, _ = _apply(op)
    assert overrides["1-11|f"] == {"id": "mr:0111-honorata", "after": None, "mark": "g"}


def test_rejected_note_is_dropped():
    _, overrides, _ = _apply(_note(decision="reject"))
    assert overrides == {"1-11|f": "DROP"}


@pytest.mark.parametrize(
    "edited, message",
    [
        ({"after": "virginis"}, "must occur once"),  # not in this eulogy
        ({"after": "Leuci"}, "must occur once"),  # not a whole word
        ({"id": "mr:0112-x"}, "not a eulogy of the day"),
        ({"mark": "F"}, "one letter"),
    ],
)
def test_invalid_note_edits_are_refused(edited, message):
    with pytest.raises(ValueError, match=message):
        _apply(_note(decision="edit", edited=edited))


def test_mark_without_note():
    _, overrides, _ = _apply(_mark(decision="accept"))
    assert overrides == {"1-11|g|mark": C.MISSING}
    _, overrides, _ = _apply(_mark(decision="reject"))
    assert overrides == {"1-11|g|mark": C.NOT_A_MARK}
    _, overrides, _ = _apply(_mark(decision="edit", edited={"note": "1-11|e"}))
    assert overrides == {"1-11|e": {"id": "mr:0111-leucius", "after": "Episcopi", "mark": "g"}}
    with pytest.raises(ValueError, match="one of the day's notes"):
        _apply(_mark(decision="edit", edited={"note": "1-12|a"}))


def test_margin_decisions_replace_the_doubtful_placement():
    key = "m|T. 2. A. 160."
    _, _, placement = _apply(_margin(decision="accept"))
    assert placement["64"][key] == {"note": "1-11|f"}
    _, _, placement = _apply(_margin(decision="edit", edited={"id": "mr:0111-honorata"}))
    assert placement["64"][key] == {"id": "mr:0111-honorata"}
    _, _, placement = _apply(_margin(decision="reject"))
    assert placement["64"][key] == "SKIP"
    op = _margin(id="65|m|new", decision="accept", **{"class": "no-image"})
    _, _, placement = _apply(op)
    assert placement["65"] == {"m|new": {"note": "1-11|f"}}


def test_invalid_margin_decisions_are_refused():
    with pytest.raises(ValueError, match="not a note or eulogy"):
        _apply(_margin(decision="edit", edited={"note": "2-1|a"}))
    with pytest.raises(ValueError, match="either `note` or `id`"):
        _apply(_margin(decision="accept", proposed=None))
    with pytest.raises(ValueError, match="unknown decision"):
        _apply(_margin(decision="maybe"))


def test_errors_apply_nothing_reported_all_at_once():
    with pytest.raises(ValueError) as e:
        _apply(_note(decision="edit", edited={"mark": "1"}), _margin(decision="maybe"))
    assert "1-11|f" in str(e.value) and "unknown decision" in str(e.value)


def test_new_changeset_and_sorting():
    cs = C.new_changeset([])
    assert cs["schema"] == "crmedr-changeset/v1" and cs["operations"] == []
    keys = list(C.sorted_overrides({"10-2|a": 1, "2-10|b|mark": 1, "2-10|a#2": 1, "2-9|c": 1}))
    assert keys == ["2-9|c", "2-10|a#2", "2-10|b|mark", "10-2|a"]
    assert C.image_url(48).endswith("/page/n47")
