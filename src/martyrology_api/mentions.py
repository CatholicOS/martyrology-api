"""The persons and places the eulogies name (crmedr `data/mentions.json`), and the check that each
one still lands on the words it names. crmedr stores no printed words: each mention carries a
`check` of them, and the API reads the words from its own texts."""

import hashlib
import logging
from collections.abc import Callable, Collection
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .models import FootnoteOut, MentionBase, MentionFootnote, MentionOut

log = logging.getLogger(__name__)


class MentionIn(MentionBase):
    """A mention as crmedr writes it: `check` is `span_check` of the words it covers."""

    check: str = Field(pattern=r"^[0-9a-f]{8}$")


# Edition → canonical id → the eulogy's mentions, text first, then its footnotes in order.
Mentions = dict[str, dict[str, list[MentionIn]]]


class TextsPin(BaseModel):
    """The martyrology-texts commit crmedr took the offsets from; null when it did not record it."""

    commit: str | None = None


class MentionsFile(BaseModel):
    """crmedr's `data/mentions.json`."""

    editions: Mentions
    texts: TextsPin = Field(default_factory=TextsPin)

    @property
    def texts_commit(self) -> str | None:
        return self.texts.commit


def load_mentions(path: Path) -> MentionsFile:
    """crmedr's `data/mentions.json`, validated. A missing file means no mentions (a crmedr pin
    from before them), with a warning. A malformed one raises at once, naming the file, as a
    malformed `footnotes.json` does."""
    if not path.exists():
        log.warning("%s is missing: the eulogies are served without mentions.", path)
        return MentionsFile(editions={})
    try:
        return MentionsFile.model_validate_json(path.read_bytes())
    except ValidationError as err:
        raise ValueError(f"{path}: invalid mentions.json: {err}") from err


def span_check(words: str) -> str:
    """The first 8 hex digits of SHA-256 over the UTF-8 bytes of `words`, as printed: neither
    folded nor normalized."""
    return hashlib.sha256(words.encode("utf-8")).hexdigest()[:8]


def utf16_slice(s: str, start: int, end: int) -> str | None:
    """`s[start:end]` counted in UTF-16 code units; None when the span runs past the end of `s`,
    cuts a surrogate pair in two, or `s` holds a lone surrogate (no UTF-16 form, so no match)."""
    try:
        units = s.encode("utf-16-le")
    except UnicodeEncodeError:
        return None
    if end * 2 > len(units):
        return None
    try:
        return units[start * 2 : end * 2].decode("utf-16-le")
    except UnicodeDecodeError:
        return None


def lands(m: MentionIn, text: str | None, footnotes: list[FootnoteOut]) -> str | None:
    """The words the mention covers in `text`, or in the footnote it names, when they are the
    words crmedr checked; None when they are not, or are not there."""
    if isinstance(m.where, MentionFootnote):
        n = m.where.footnote
        target = footnotes[n - 1].text if n <= len(footnotes) else None
    else:
        target = text
    words = utf16_slice(target, m.start, m.end) if target is not None else None
    return words if words is not None and span_check(words) == m.check else None


def served(ms: list[MentionIn], text: str | None, footnotes: list[FootnoteOut]) -> list[MentionOut]:
    """The mentions that land in this text, as served: each with the words it covers as `form`."""
    out = []
    for m in ms:
        words = lands(m, text, footnotes)
        if words is not None:
            out.append(MentionOut.model_validate(m.model_dump(exclude={"check"}) | {"form": words}))
    return out


def _where(m: MentionIn) -> str:
    return f"footnote {m.where.footnote}" if isinstance(m.where, MentionFootnote) else "text"


def check_mentions(
    mentions: Mentions,
    attached: Collection[str],
    texts_of: Callable[[str], dict[str, str | None]],
    footnotes_of: Callable[[str], dict[str, list[FootnoteOut]]],
    texts_commit: str | None,
) -> Mentions:
    """The mentions that land on the words crmedr checked, in the attached texts. Each one that
    doesn't (its text corrected since crmedr's extraction, a footnote the eulogy lacks, a eulogy
    the edition does not print) is logged once and dropped; an edition whose texts are not
    attached is skipped. The count is logged at the end, with the texts commit the offsets came
    from."""
    kept: Mentions = {}
    served_n = dropped = 0
    for edition_id, by_id in mentions.items():
        if edition_id not in attached:
            log.info("The mentions of %s are not served: its texts are not attached.", edition_id)
            continue
        texts, notes = texts_of(edition_id), footnotes_of(edition_id)
        for cid, ms in by_id.items():
            for m in ms:
                if cid not in texts:
                    reason = "eulogy not printed"
                elif isinstance(m.where, MentionFootnote) and m.where.footnote > len(
                    notes.get(cid, [])
                ):
                    reason = "no such footnote"
                elif lands(m, texts[cid], notes.get(cid, [])) is None:
                    reason = "check mismatch"
                else:
                    kept.setdefault(edition_id, {}).setdefault(cid, []).append(m)
                    served_n += 1
                    continue
                dropped += 1
                log.warning(
                    "Mention dropped (%s): %s %s, %s at %d-%d, check %s.",
                    reason,
                    edition_id,
                    cid,
                    _where(m),
                    m.start,
                    m.end,
                    m.check,
                )
    log.info(
        "Mentions: %d served, %d dropped (offsets from martyrology-texts %s).",
        served_n,
        dropped,
        texts_commit or "an unrecorded commit",
    )
    return kept


def compare_texts_pins(extracted: str | None, deployed: str | None) -> None:
    """Warn when crmedr took the offsets from other texts than this deployment serves: its
    mentions then miss wherever the two differ. Either commit may be abbreviated; nothing is
    compared when either is unknown."""
    if (
        extracted
        and deployed
        and not (extracted.startswith(deployed) or deployed.startswith(extracted))
    ):
        log.warning(
            "crmedr's mentions were taken from martyrology-texts %s, but this deployment serves "
            "%s: expect dropped mentions until the two pins agree.",
            extracted,
            deployed,
        )
