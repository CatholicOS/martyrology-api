import json
from dataclasses import dataclass

from ..auth import Identity
from ..config import Settings
from ..problems import ApiProblem
from ..registry import Registry, anchor_day
from ..store import DayData, detect_shape, parse_month_file
from .base import ConflictError, VcsBackend
from .validation import validate_or_raise


@dataclass
class WriteReceipt:
    branch: str
    commit_sha: str
    pr_url: str


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


class CurationService:
    def __init__(self, backend: VcsBackend, registry: Registry, settings: Settings):
        self.backend = backend
        self.registry = registry
        self.settings = settings

    def repo_for(self, edition_id: str) -> str:
        return (self.settings.private_repo
                if edition_id in self.settings.restricted_set
                else self.settings.public_repo)

    def month_path(self, edition_id: str, month: int) -> str:
        return f"{self.settings.repo_data_prefix}/{edition_id}/{month:02d}.json"

    def edition_meta_path(self, edition_id: str) -> str:
        return f"{self.settings.repo_data_prefix}/{edition_id}/edition.json"

    def branch_for(self, identity: Identity, topic: str | None) -> str:
        return f"curation/{identity.username}/{topic or 'edits'}"

    def _require_registered(self, edition_id: str) -> None:
        if edition_id not in self.registry.editions:
            raise ApiProblem(422, "Unknown edition",
                             detail=f"'{edition_id}' must be registered in the CLBDR "
                                    "before texts can be curated.",
                             type_slug="unknown-edition")

    def _shape(self, edition_id: str, branch: str) -> str:
        meta = self.backend.read_file(self.repo_for(edition_id), branch,
                                      self.edition_meta_path(edition_id))
        if meta is not None:
            shape = json.loads(meta[0]).get("shape")
            if shape in ("day-structured", "flat"):
                return shape
        probe = self.backend.read_file(self.repo_for(edition_id), branch,
                                       self.month_path(edition_id, 1))
        if probe is not None:
            return detect_shape(json.loads(probe[0]))
        return "day-structured"

    def _read_month_raw(self, edition_id: str, month: int,
                        branch: str) -> tuple[dict, str | None]:
        got = self.backend.read_file(self.repo_for(edition_id), branch,
                                     self.month_path(edition_id, month))
        return (json.loads(got[0]), got[1]) if got is not None else ({}, None)

    def _commit(self, identity: Identity, edition_id: str, path: str,
                data: dict, action: str, topic: str | None,
                if_match: str | None) -> WriteReceipt:
        repo = self.repo_for(edition_id)
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(repo, branch)
        try:
            sha = self.backend.write_file(
                repo, branch, path, _dump(data),
                f"curation({edition_id}): {action}",
                identity.name or identity.username,
                identity.email or f"{identity.username}@users.noreply.local",
                expected_sha=if_match)
        except ConflictError as exc:
            raise ApiProblem(409, "Write conflict", detail=str(exc),
                             type_slug="write-conflict")
        pr = self.backend.open_pr(repo, branch, f"Curation: {edition_id}")
        return WriteReceipt(branch=branch, commit_sha=sha, pr_url=pr)

    # -- edition-level -----------------------------------------------------

    def create_edition(self, identity: Identity, edition_id: str, shape: str,
                       note: str | None, topic: str | None) -> WriteReceipt:
        self._require_registered(edition_id)
        default_meta = self.backend.read_file(
            self.repo_for(edition_id), "main", self.edition_meta_path(edition_id))
        if default_meta is not None:
            raise ApiProblem(409, "Edition already exists",
                             type_slug="already-exists")
        meta: dict = {"shape": shape}
        if note:
            meta["note"] = note
        receipt = self._commit(identity, edition_id,
                               self.edition_meta_path(edition_id), meta,
                               "create edition", topic, None)
        for m in range(1, 13):
            receipt = self._commit(identity, edition_id,
                                   self.month_path(edition_id, m), {},
                                   f"scaffold month {m:02d}", topic, None)
        return receipt

    def patch_edition(self, identity: Identity, edition_id: str,
                      fields: dict, topic: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        got = self.backend.read_file(self.repo_for(edition_id), branch,
                                     self.edition_meta_path(edition_id))
        if got is None:
            raise ApiProblem(404, "Edition has no data here",
                             type_slug="unknown-edition-data")
        meta = json.loads(got[0])
        meta.update({k: v for k, v in fields.items() if v is not None})
        return self._commit(identity, edition_id,
                            self.edition_meta_path(edition_id), meta,
                            "update edition metadata", topic, got[1])

    # -- month / day / eulogy ---------------------------------------------

    def put_month(self, identity: Identity, edition_id: str, month: int,
                  raw: dict, topic: str | None,
                  if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        validate_or_raise(raw, month, self._shape(edition_id, branch), self.registry)
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"replace month {month:02d}", topic, if_match)

    def patch_day(self, identity: Identity, edition_id: str, month: int, day: int,
                  payload: dict, topic: str | None,
                  if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        if self._shape(edition_id, branch) != "day-structured":
            raise ApiProblem(422, "Day edits need a day-structured edition",
                             type_slug="not-day-structured")
        raw, sha = self._read_month_raw(edition_id, month, branch)
        key = str(day)
        if key not in raw:
            raise ApiProblem(404, "No such day in this edition",
                             type_slug="unknown-day")
        for field in ("titulus", "conclusio"):
            if field in payload:
                raw[key][field] = payload[field]
        if "order" in payload:
            current = raw[key].get("elogia", {})
            if sorted(payload["order"]) != sorted(current):
                raise ApiProblem(422, "Order must be a permutation of the day's ids",
                                 type_slug="bad-order")
            raw[key]["elogia"] = {cid: current[cid] for cid in payload["order"]}
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"edit day {month:02d}-{day:02d}", topic,
                            if_match or sha)

    def _locate(self, edition_id: str, cid: str, branch: str) -> tuple[int, dict, str | None, str | None]:
        """Return (month, raw, blob_sha, day_key|None) for the month containing cid."""
        am, _ = anchor_day(cid)
        months = [am] + [m for m in range(1, 13) if m != am]
        for m in months:
            raw, sha = self._read_month_raw(edition_id, m, branch)
            if not raw:
                continue
            if detect_shape(raw) == "flat":
                if cid in raw:
                    return m, raw, sha, None
            else:
                for day_key, obj in raw.items():
                    if cid in obj.get("elogia", {}):
                        return m, raw, sha, day_key
        raise ApiProblem(404, "Eulogy not present in this edition",
                         type_slug="unknown-eulogy")

    def put_elogium(self, identity: Identity, edition_id: str, cid: str, text: str,
                    day: int | None, position: int | None, topic: str | None,
                    if_match: str | None) -> WriteReceipt:
        self._require_registered(edition_id)
        if cid not in self.registry.entries:
            raise ApiProblem(422, "Unknown canonical id", type_slug="invalid-payload",
                             errors=[f"'{cid}' is not in the CRMEDR registry"])
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        shape = self._shape(edition_id, branch)
        am, _ = anchor_day(cid)
        if shape == "flat":
            raw, sha = self._read_month_raw(edition_id, am, branch)
            raw[cid] = text
            return self._commit(identity, edition_id, self.month_path(edition_id, am),
                                raw, f"set text for {cid}", topic, if_match or sha)
        if day is None:
            raise ApiProblem(422, "A 'day' is required for day-structured editions",
                             type_slug="day-required")
        raw, sha = self._read_month_raw(edition_id, am, branch)
        obj = raw.setdefault(str(day), {"titulus": None, "elogia": {}, "conclusio": None})
        elogia = {k: v for k, v in obj.get("elogia", {}).items() if k != cid}
        items = list(elogia.items())
        idx = len(items) if position is None else max(0, position - 1)
        items.insert(idx, (cid, text))
        obj["elogia"] = dict(items)
        return self._commit(identity, edition_id, self.month_path(edition_id, am),
                            raw, f"place {cid} under day {day}", topic,
                            if_match or sha)

    def patch_elogium(self, identity: Identity, edition_id: str, cid: str,
                      text: str, topic: str | None,
                      if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        month, raw, sha, day_key = self._locate(edition_id, cid, branch)
        if day_key is None:
            raw[cid] = text
        else:
            raw[day_key]["elogia"][cid] = text
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"correct text of {cid}", topic, if_match or sha)

    def delete_elogium(self, identity: Identity, edition_id: str, cid: str,
                       topic: str | None, if_match: str | None) -> WriteReceipt:
        branch = self.branch_for(identity, topic)
        self.backend.ensure_branch(self.repo_for(edition_id), branch)
        month, raw, sha, day_key = self._locate(edition_id, cid, branch)
        if day_key is None:
            del raw[cid]
        else:
            del raw[day_key]["elogia"][cid]
        return self._commit(identity, edition_id, self.month_path(edition_id, month),
                            raw, f"remove {cid}", topic, if_match or sha)

    # -- draft reads -------------------------------------------------------

    def read_month_draft(self, edition_id: str, month: int,
                         branch: str) -> dict[int, DayData]:
        raw, _ = self._read_month_raw(edition_id, month, branch)
        return parse_month_file(raw, month, detect_shape(raw), self.registry)
