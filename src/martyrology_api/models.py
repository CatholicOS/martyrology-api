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
