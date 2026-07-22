import json
from dataclasses import dataclass
from pathlib import Path

from .registry import Registry, anchor_day, slug_of


@dataclass
class Elogium:
    id: str
    text: str | None
    entry: int | None
    asterisk: bool
    unnumbered: bool
    anchor_month: int
    anchor_day: int


@dataclass
class DayData:
    month: int
    day: int
    titulus: str | None
    elogia: list[Elogium]
    conclusio: str | None


@dataclass
class Placement:
    edition_id: str
    day_printed: str
    entry: int | None
    asterisk: bool
    unnumbered: bool
    text: str | None


def detect_shape(raw: dict) -> str:
    for k in raw:
        return "flat" if k.startswith("mr:") else "day-structured"
    return "day-structured"


def _elogium(cid: str, text: str | None, position: int, registry: Registry) -> Elogium:
    am, ad = anchor_day(cid)
    reg = registry.entries.get(cid)
    return Elogium(
        id=cid, text=text, entry=position,
        asterisk=reg.asterisk if reg else False,
        unnumbered=reg.unnumbered if reg else False,
        anchor_month=am, anchor_day=ad)


def parse_month_file(raw: dict, month: int, shape: str, registry: Registry) -> dict[int, DayData]:
    days: dict[int, DayData] = {}
    if shape == "day-structured":
        for day_key, obj in raw.items():
            day = int(day_key)
            elogia = [_elogium(cid, text, i + 1, registry)
                      for i, (cid, text) in enumerate(obj.get("elogia", {}).items())]
            days[day] = DayData(month=month, day=day, titulus=obj.get("titulus"),
                                elogia=elogia, conclusio=obj.get("conclusio"))
    else:  # flat: membership/order/metadata from the registry, texts from the map
        by_day: dict[int, list] = {}
        for e in registry.entries.values():
            if e.deprecated or e.month != month or e.id not in raw:
                continue
            by_day.setdefault(e.day, []).append(e)
        for day, entries in by_day.items():
            entries.sort(key=lambda e: (not e.unnumbered,
                                         e.entry if e.entry is not None else float("inf"),
                                         e.id))
            elogia = [Elogium(id=e.id, text=raw[e.id], entry=e.entry,
                              asterisk=e.asterisk, unnumbered=e.unnumbered,
                              anchor_month=e.month, anchor_day=e.day)
                      for e in entries]
            days[day] = DayData(month=month, day=day, titulus=None,
                                elogia=elogia, conclusio=None)
    return days


class Store:
    def __init__(self, data_paths: list[Path], registry: Registry):
        self.registry = registry
        self._dirs: dict[str, Path] = {}
        for base in data_paths:
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if d.is_dir() and any((d / f"{m:02d}.json").exists() for m in range(1, 13)):
                    self._dirs.setdefault(d.name, d)
        self._months: dict[tuple[str, int], dict[int, DayData]] = {}
        self._shapes: dict[str, str] = {}

    def available(self) -> set[str]:
        return set(self._dirs)

    def _load_month(self, edition_id: str, month: int) -> dict[int, DayData]:
        key = (edition_id, month)
        if key in self._months:
            return self._months[key]
        d = self._dirs.get(edition_id)
        result: dict[int, DayData] = {}
        if d is not None:
            f = d / f"{month:02d}.json"
            if f.exists():
                raw = json.loads(f.read_text())
                self._shapes.setdefault(edition_id, detect_shape(raw))
                result = parse_month_file(raw, month, self._shapes[edition_id], self.registry)
        self._months[key] = result
        return result

    def shape(self, edition_id: str) -> str:
        if edition_id not in self._shapes:
            for m in range(1, 13):
                if self._load_month(edition_id, m):
                    break
        return self._shapes.get(edition_id, "day-structured")

    def month(self, edition_id: str, month: int) -> dict[int, DayData]:
        return self._load_month(edition_id, month)

    def day(self, edition_id: str, month: int, day: int) -> DayData | None:
        return self._load_month(edition_id, month).get(day)

    def find_by_slug(self, edition_id: str, month: int, day: int, slug: str):
        d = self.day(edition_id, month, day)
        if d is None:
            return None
        for e in d.elogia:
            if slug_of(e.id) == slug:
                return e
        return None

    def placements(self, canonical_id: str) -> list[Placement]:
        am, _ = anchor_day(canonical_id)
        out: list[Placement] = []
        for edition_id in sorted(self._dirs):
            months = [am] + [m for m in range(1, 13) if m != am]
            for m in months:
                found = next((("%02d-%02d" % (m, dd.day), e)
                              for dd in self._load_month(edition_id, m).values()
                              for e in dd.elogia if e.id == canonical_id), None)
                if found:
                    printed, e = found
                    out.append(Placement(edition_id=edition_id, day_printed=printed,
                                         entry=e.entry, asterisk=e.asterisk,
                                         unnumbered=e.unnumbered, text=e.text))
                    break
        return out
