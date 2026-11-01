"""Every eulogy ID the older editions name is an ID of the pinned crmedr registry: a rename
that did not re-key them (crmedr's `apply_renames.py api`) leaves their eulogies under an ID
the API drops."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
REGISTRY = ROOT / "vendor" / "crmedr" / "data" / "martyrology_ids.json"
EDITIONS = (
    "martyrologium_romanum_1630",
    "martyrologium_romanum_1749",
    "martyrologium_romanum_1914_en_unofficial",
)
MRID = re.compile(r'"(mr:\d{4}-[a-z]+(?:-[a-z]+)*)"')

pytestmark = pytest.mark.skipif(not REGISTRY.is_file(), reason="vendor/crmedr not checked out")


@pytest.mark.parametrize("edition", EDITIONS)
def test_the_older_editions_name_only_registry_ids(edition):
    ids = {e["id"] for e in json.loads(REGISTRY.read_text(encoding="utf-8"))["entries"]}
    named = {}
    for path in sorted((ROOT / "data" / "editions" / edition).glob("*.json")):
        for mrid in MRID.findall(path.read_text(encoding="utf-8")):
            named.setdefault(mrid, path.name)
    assert {m: f for m, f in named.items() if m not in ids} == {}
