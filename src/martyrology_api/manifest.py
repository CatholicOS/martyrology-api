import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

BUNDLE_FORMAT = 1


class Manifest(BaseModel):
    """The deployment manifest written into every release bundle.

    `data` maps a data-repository nickname (texts, crmedr, clbdr) to the
    commit SHA that was bundled; `files` maps every bundled path to its
    sha256. Together they are the auditable record of which corpus is live.
    """

    bundle_format: int
    api_version: str
    api_commit: str
    data: dict[str, str]
    python_requires: str
    files: dict[str, str]


def load_manifest(path: Path | None) -> Manifest | None:
    """Read a deployment manifest, or None when it is absent or unusable.

    Absence is the ordinary development case: no bundle, no manifest. A
    malformed manifest, or one written by a future bundle format, is also
    reported as absent rather than raised. /healthz is what the deploy
    script polls to decide whether to roll back, so it must keep answering
    even when the manifest is the thing that is broken.
    """
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError:
        return None
    if manifest.bundle_format != BUNDLE_FORMAT:
        return None
    return manifest
