"""The persons and places the eulogies name (crmedr `data/mentions.json`), and the check that each
one still lands on the words it names. crmedr stores no printed words: each mention carries a
`check` of them, and the API reads the words from its own texts."""

import hashlib
import logging
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
    """`s[start:end]` counted in UTF-16 code units; None when the span runs past the end of `s` or
    cuts a surrogate pair in two."""
    units = s.encode("utf-16-le")
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
