import json
import re
from dataclasses import dataclass
from pathlib import Path

ID_RE = re.compile(r"^mr:(\d{4})-([a-z0-9-]+)$")
MARTYROLOGY_BOOK = "book:martyrologium-romanum"


def is_canonical_id(s: str) -> bool:
    return bool(ID_RE.match(s))


def anchor_day(canonical_id: str) -> tuple[int, int]:
    m = ID_RE.match(canonical_id)
    if not m:
        raise ValueError(f"not a canonical id: {canonical_id}")
    mmdd = m.group(1)
    return int(mmdd[:2]), int(mmdd[2:])


def slug_of(canonical_id: str) -> str:
    m = ID_RE.match(canonical_id)
    if not m:
        raise ValueError(f"not a canonical id: {canonical_id}")
    return m.group(2)


@dataclass(frozen=True)
class IdEntry:
    id: str
    month: int
    day: int
    entry: int | None
    asterisk: bool = False
    country: str | None = None
    unnumbered: bool = False
    deprecated: bool = False
    attested_in: str | None = None


@dataclass(frozen=True)
class EditionMeta:
    id: str
    nature: str
    language: str
    scope: str
    promulgated: str
    promulgated_year: int
    decree: str | None = None
    predecessor: str | None = None
    successor: str | None = None
    translation_of: str | None = None
    note: str | None = None


class Registry:
    def __init__(self, entries: dict[str, IdEntry],
                 editions: dict[str, EditionMeta],
                 i18n: dict[str, dict[str, str]]):
        self.entries = entries
        self.editions = editions
        self._i18n = i18n

    def subjects(self, locale: str) -> dict[str, str]:
        return self._i18n.get(locale.split("-")[0], {})

    def locales(self) -> list[str]:
        return sorted(self._i18n)

    def ids_for_day(self, month: int, day: int) -> list[IdEntry]:
        found = [e for e in self.entries.values()
                 if not e.deprecated and e.month == month and e.day == day]
        return sorted(found, key=lambda e: (not e.unnumbered,
                                             e.entry if e.entry is not None else float("inf"),
                                             e.id))

    @classmethod
    def load(cls, crmedr_path: Path, clbdr_path: Path) -> "Registry":
        raw = json.loads((crmedr_path / "data/martyrology_ids.json").read_text())
        entries: dict[str, IdEntry] = {}
        for e in raw["entries"]:
            entries[e["id"]] = IdEntry(
                id=e["id"], month=e["month"], day=e["day"], entry=e["entry"],
                asterisk=e.get("asterisk", False), country=e.get("country"),
                unnumbered=e.get("unnumbered", False))
        dep_raw = json.loads((crmedr_path / "data/deprecated_ids.json").read_text())
        dep_subjects_la: dict[str, str] = {}
        for e in dep_raw:
            entries[e["id"]] = IdEntry(
                id=e["id"], month=e["month"], day=e["day"], entry=e["entry"],
                deprecated=True, attested_in=e.get("attested_in"))
            if e.get("subject_la"):
                dep_subjects_la[e["id"]] = e["subject_la"]

        i18n: dict[str, dict[str, str]] = {}
        for f in sorted((crmedr_path / "i18n").glob("*.json")):
            i18n[f.stem] = json.loads(f.read_text())
        i18n.setdefault("la", {})
        for cid, subj in dep_subjects_la.items():
            i18n["la"].setdefault(cid, subj)

        ed_raw = json.loads((clbdr_path / "data/editions.json").read_text())
        editions: dict[str, EditionMeta] = {}
        for e in ed_raw["entries"]:
            if e.get("book") != MARTYROLOGY_BOOK:
                continue
            editions[e["id"]] = EditionMeta(
                id=e["id"], nature=e["nature"], language=e["language"],
                scope=e["scope"], promulgated=str(e["promulgated"]),
                promulgated_year=int(str(e["promulgated"])[:4]),
                decree=e.get("decree"), predecessor=e.get("predecessor"),
                successor=e.get("successor"), translation_of=e.get("translation_of"),
                note=e.get("note"))
        return cls(entries, editions, i18n)
