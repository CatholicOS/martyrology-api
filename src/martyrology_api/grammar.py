import re
from dataclasses import dataclass

from .problems import ApiProblem

DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
                 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
NATION_RE = re.compile(r"^[A-Z]{2}$")


@dataclass
class ElogiaRequest:
    nation: str | None = None
    edition: str | None = None
    year: int | None = None
    month: int | None = None
    day: int | None = None
    slug: str | None = None


def _bad(detail: str) -> ApiProblem:
    return ApiProblem(400, "Malformed elogia path", detail=detail,
                      type_slug="malformed-path")


def parse_elogia_path(path: str) -> ElogiaRequest:
    segs = [s for s in path.split("/") if s]
    req = ElogiaRequest()

    if segs and segs[0] == "nation":
        if len(segs) < 2 or not NATION_RE.match(segs[1]):
            raise _bad("nation/ must be followed by an ISO 3166-1 alpha-2 code")
        req.nation, segs = segs[1], segs[2:]
    elif segs and segs[0] == "edition":
        if len(segs) < 2:
            raise _bad("edition/ must be followed by a CLBDR edition id")
        req.edition, segs = segs[1], segs[2:]

    if segs and re.fullmatch(r"\d{4}", segs[0]) and segs[0][0] != "0":
        if req.edition:
            raise _bad("a year segment cannot follow an explicit edition")
        req.year, segs = int(segs[0]), segs[1:]

    if not segs:
        raise _bad("a two-digit month segment is required")
    if not re.fullmatch(r"\d{2}", segs[0]) or not 1 <= int(segs[0]) <= 12:
        raise _bad(f"invalid month segment '{segs[0]}' (expected 01-12)")
    req.month, segs = int(segs[0]), segs[1:]

    if segs:
        if not re.fullmatch(r"\d{2}", segs[0]) or \
                not 1 <= int(segs[0]) <= DAYS_IN_MONTH[req.month]:
            raise _bad(f"invalid day segment '{segs[0]}' for month {req.month:02d}")
        req.day, segs = int(segs[0]), segs[1:]

    if segs:
        if req.day is None or not SLUG_RE.match(segs[0]):
            raise _bad(f"invalid slug segment '{segs[0]}'")
        req.slug, segs = segs[0], segs[1:]

    if segs:
        raise _bad(f"unexpected trailing segments: {'/'.join(segs)}")
    return req
