from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class FootnoteOut(BaseModel):
    """A footnote as printed under a eulogy: its printed mark, the phrase the
    mark follows in the text (null when it couldn't be anchored), its text."""

    mark: str
    after: str | None
    text: str


class MarginNoteOut(BaseModel):
    """A note printed in the margin beside a eulogy: its text, and the mark of the
    footnote it stands beside (null when it stands beside the eulogy's text)."""

    text: str
    note: str | None


class ErratumOut(BaseModel):
    """A correction the edition itself prints in its errata: `printed` occurs once in the
    eulogy's text; `replace` reads it as `corrected`, `add` adds `corrected` after it (or
    before it, `position` "before": an addition that opens the eulogy), `delete` drops it.
    `ref` is where the errata place it (page.line, or "vbique"), `entry` the erratum as
    printed. The text stays as printed."""

    kind: Literal["replace", "add", "delete"]
    printed: str
    corrected: str
    ref: str
    entry: str
    position: Literal["after", "before"] = "after"


class ElogiumOut(BaseModel):
    id: str | None
    entry: int | None
    asterisk: bool
    unnumbered: bool
    anchor_day: str
    text: str | None
    footnotes: list[FootnoteOut] = Field(default_factory=list)
    marginalia: list[MarginNoteOut] = Field(default_factory=list)
    errata: list[ErratumOut] = Field(default_factory=list)


class RubricaOut(BaseModel):
    """A rubric printed among the eulogies; `after` is the eulogy it follows, null at
    the head of the day."""

    after: str | None
    text: str


class LunaColumnOut(BaseModel):
    """One column of a day's lunar table: the letter of the Martyrology, the epact it
    stands for ("*", "j" … "xxix", or "25" for the Arabic 25), and the moon's age under it.
    `printed` is the age the edition prints there when it misprints it."""

    letter: str
    epact: str
    age: int
    printed: int | None = None
    # printed in red (the 2004 edition tells its two F apart by colour)
    red: bool = False


class LunaAnnouncementOut(BaseModel):
    """The moon announced on the day in `year`: the year's golden number, epact and letter
    (by the Gregorian computus, whatever edition is read), the column of the table it
    reads, and the age under it, as announced (`pronuntiatio`: "Luna vigesima prima")."""

    year: int
    golden_number: int
    epact: str
    letter: str
    column: int
    age: int
    pronuntiatio: str


class LunaOut(BaseModel):
    """The calendar apparatus an edition prints with the day: the lunar table under the
    heading (31 columns in printed order, set in `rows` of that many columns), the dominical
    letter and the epacts whose new moon falls on the day in the margin (null for an edition
    that prints none), and the announcement for a year (null before the Gregorian reform), in
    the edition's language."""

    rows: list[int]
    dominical_letter: str | None
    epactae: list[str] | None
    tabula: list[LunaColumnOut]
    annuntiatio: LunaAnnouncementOut | None


class DayContentOut(BaseModel):
    titulus: str | None
    elogia: list[ElogiumOut]
    rubricae: list[RubricaOut] = Field(default_factory=list)
    conclusio: str | None
    # Only for editions that print a lunar table under each day's heading.
    luna: LunaOut | None = None


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
    footnotes: list[FootnoteOut] = Field(default_factory=list)
    marginalia: list[MarginNoteOut] = Field(default_factory=list)
    errata: list[ErratumOut] = Field(default_factory=list)


class EulogyOut(BaseModel):
    id: str
    subject: dict[str, str]
    anchor_day: str
    deprecated: bool
    editions: dict[str, EditionPlacementOut]
    # The same eulogy printed by another edition on another day (other IDs).
    same_eulogy: list[str] = []


class GovernanceOut(BaseModel):
    governing_body: str
    type: str
    nation: str | None = None


class AvailabilityOut(BaseModel):
    status: str
    note: str | None = None


class SourceOut(BaseModel):
    """The printed copy an edition's texts were taken from, as described in
    the `source.json` beside its monthly files."""

    model_config = ConfigDict(extra="forbid")

    title: str
    imprint: str | None = None
    year: int | None = None
    rights: str | None = None
    isbn: str | None = None
    note: str | None = None
    # The edition prints the lunar table of the Gregorian computus under each day's
    # heading, as the editions up to 1962 ("gregorian") or as the 2004 edition
    # ("gregorian-2004"): its days carry `luna`.
    lunar_table: Literal["gregorian", "gregorian-2004"] | None = None


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
    source: SourceOut | None = None


class EditionsOut(BaseModel):
    editions: list[EditionOut]


class EditionAccessOut(BaseModel):
    can_read_texts: bool


class AccessOut(BaseModel):
    editions: dict[str, EditionAccessOut]


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


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    data: dict[str, str | None]
    # Attached editions: those whose texts this deployment can serve. The deploy
    # smoke check reads this list, so its shape must not change.
    editions: list[str]
    # Catalogued vs attached, so a transcription backlog (or a data pin that
    # dropped an edition) is visible without diffing against the catalog by hand.
    editions_catalogued: int
    editions_attached: int
    # Attached but absent from the catalog: a data fault, not a backlog. Their
    # texts are unreachable, since every read 404s an unregistered edition.
    editions_uncatalogued: list[str]


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


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: str
    governance_body: str
    relation: str


class PermissionOut(BaseModel):
    user: str
    governance_body: str
    relation: str


class PermissionListOut(BaseModel):
    governance_body: str
    permissions: list[PermissionOut]


class PermissionCheckOut(BaseModel):
    user: str
    governance_body: str
    relation: str
    allowed: bool
