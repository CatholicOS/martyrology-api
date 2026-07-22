from typing import Literal

from pydantic import BaseModel


class EditionMetadataOut(BaseModel):
    nature: str
    language: str
    scope: str
    promulgated: str
    decree: str | None = None
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
    id: str
    entry: int
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
    entry: int
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
