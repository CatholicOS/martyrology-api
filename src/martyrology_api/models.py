from typing import Literal

from pydantic import BaseModel


def scope_dict(scope: str) -> dict:
    """The wire shape of an edition's scope: {"type": "universal"} or
    {"type": "nation", "nation": <ISO code>}. Shared by discovery's
    EditionOut.scope and read's EditionMetadataOut.scope so the two never
    drift apart."""
    return {"type": "universal"} if scope == "universal" else {"type": "nation", "nation": scope}


def promulgation_dict(decree: str | None, promulgated: str) -> dict:
    """The wire shape of an edition's promulgation: {"decree": ..., "date": ...}.
    Shared by discovery's EditionOut.promulgation and read's
    EditionMetadataOut.promulgation."""
    return {"decree": decree, "date": promulgated}


class EditionMetadataOut(BaseModel):
    book: str = "martyrologium_romanum"
    year: int
    nature: str
    scope: dict
    locale: str
    promulgation: dict
    predecessor: str | None = None
    successor: str | None = None
    translation_of: str | None = None


class MetadataOut(BaseModel):
    edition: str
    edition_metadata: EditionMetadataOut
    resolved_from: dict | None = None
    month: int
    day: int | None = None
    access: str = "public"
    access_info: str | None = None


class ElogiumOut(BaseModel):
    id: str | None
    entry: int | None
    asterisk: bool
    unnumbered: bool
    anchor_day: str
    text: str | None


class DayContentOut(BaseModel):
    titulus: str | None
    elogia: list[ElogiumOut]
    conclusio: str | None


class DayOut(DayContentOut):
    metadata: MetadataOut


class MonthOut(BaseModel):
    metadata: MetadataOut
    days: dict[str, DayContentOut]


class EditionPlacementOut(BaseModel):
    day_printed: str
    entry: int | None
    asterisk: bool
    unnumbered: bool
    text: str | None


class EulogyOut(BaseModel):
    id: str
    subject: dict[str, str]
    anchor_day: str
    deprecated: bool
    editions: dict[str, EditionPlacementOut]


class GovernanceOut(BaseModel):
    governing_body: str
    type: str
    nation: str | None = None


class AvailabilityOut(BaseModel):
    status: str
    note: str | None = None


class EditionOut(BaseModel):
    edition_id: str
    book: str = "martyrologium_romanum"
    year: int
    nature: str
    scope: dict
    locale: str
    promulgation: dict
    predecessor: str | None = None
    successor: str | None = None
    governance: GovernanceOut
    availability: AvailabilityOut
    aligned: bool | None = None


class EditionsOut(BaseModel):
    editions: list[EditionOut]


class CatalogEntryOut(BaseModel):
    id: str
    subject: str | None
    anchor_day: str
    deprecated: bool
    present: bool | None = None
    day_printed: str | None = None
    entry: int | None = None


class CatalogOut(BaseModel):
    elogia: list[CatalogEntryOut]


class WriteReceiptOut(BaseModel):
    branch: str
    commit_sha: str
    pr_url: str


class EditionCreateIn(BaseModel):
    shape: Literal["day-structured", "flat"] = "day-structured"
    note: str | None = None


class EditionPatchIn(BaseModel):
    note: str | None = None


class DayPatchIn(BaseModel):
    titulus: str | None = None
    conclusio: str | None = None
    order: list[str] | None = None


class ElogiumPutIn(BaseModel):
    text: str
    day: int | None = None
    position: int | None = None


class ElogiumPatchIn(BaseModel):
    text: str
