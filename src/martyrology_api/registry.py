import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

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
    # Where another 2004-family edition differs from this (Latin print) placement:
    # its own entry/asterisk/unnumbered, or absent. See crmedr's registry format.
    editions: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict, hash=False, compare=False
    )
    # The same eulogy printed by another edition on another day.
    same_eulogy: tuple[str, ...] = ()

    def in_edition(self, edition_id: str) -> "IdEntry | None":
        """This eulogy as `edition_id` prints it, or None if it does not print it."""
        o = self.editions.get(edition_id)
        if not o:
            return self
        if o.get("absent"):
            return None
        return replace(
            self,
            entry=cast("int | None", o["entry"])
            if "entry" in o
            else self.entry,  # explicit null kept
            asterisk=bool(o.get("asterisk", self.asterisk)),
            unnumbered=bool(o.get("unnumbered", self.unnumbered)),
        )


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
    def __init__(
        self,
        entries: dict[str, IdEntry],
        editions: dict[str, EditionMeta],
        i18n: dict[str, dict[str, str]],
    ):
        self.entries = entries
        self.editions = editions
        self._i18n = i18n

    def subjects(self, locale: str) -> dict[str, str]:
        return self._i18n.get(locale.split("-")[0].lower(), {})

    def locales(self) -> list[str]:
        return sorted(self._i18n)

    def ids_for_day(self, month: int, day: int) -> list[IdEntry]:
        found = [
            e
            for e in self.entries.values()
            if not e.deprecated and e.month == month and e.day == day
        ]
        return sorted(
            found,
            key=lambda e: (
                not e.unnumbered,
                e.entry if e.entry is not None else float("inf"),
                e.id,
            ),
        )

    @classmethod
    def load(cls, crmedr_path: Path, clbdr_path: Path) -> "Registry":
        raw = json.loads((crmedr_path / "data/martyrology_ids.json").read_text())
        entries: dict[str, IdEntry] = {}
        for e in raw["entries"]:
            entries[e["id"]] = IdEntry(
                id=e["id"],
                month=e["month"],
                day=e["day"],
                entry=e["entry"],
                asterisk=e.get("asterisk", False),
                country=e.get("country"),
                unnumbered=e.get("unnumbered", False),
                editions=e.get("editions", {}),
                same_eulogy=tuple(e.get("same_eulogy", [])),
            )
        dep_raw = json.loads((crmedr_path / "data/deprecated_ids.json").read_text())
        for e in dep_raw:
            entries[e["id"]] = IdEntry(
                id=e["id"],
                month=e["month"],
                day=e["day"],
                entry=e["entry"],
                deprecated=True,
                attested_in=e.get("attested_in"),
                country=e.get("country"),
                same_eulogy=tuple(e.get("same_eulogy", [])),
            )

        i18n: dict[str, dict[str, str]] = {}
        for f in sorted((crmedr_path / "i18n").glob("*.json")):
            i18n[f.stem] = json.loads(f.read_text())
        i18n.setdefault("la", {})

        ed_raw = json.loads((clbdr_path / "data/editions.json").read_text())
        editions: dict[str, EditionMeta] = {}
        for e in ed_raw["entries"]:
            if e.get("book") != MARTYROLOGY_BOOK:
                continue
            editions[e["id"]] = EditionMeta(
                id=e["id"],
                nature=e["nature"],
                language=e["language"],
                scope=e["scope"],
                promulgated=str(e["promulgated"]),
                promulgated_year=int(str(e["promulgated"])[:4]),
                decree=e.get("decree"),
                predecessor=e.get("predecessor"),
                successor=e.get("successor"),
                translation_of=e.get("translation_of"),
                note=e.get("note"),
            )
        return cls(entries, editions, i18n)
