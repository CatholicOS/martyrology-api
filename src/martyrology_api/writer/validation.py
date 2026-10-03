import re

from ..grammar import DAYS_IN_MONTH
from ..problems import ApiProblem
from ..registry import Registry, anchor_day, is_canonical_id

ALLOWED_DAY_KEYS = {"titulus", "elogia", "rubricae", "conclusio"}


def _check_id(cid: str, registry: Registry, errors: list[str]) -> bool:
    if not is_canonical_id(cid):
        errors.append(f"'{cid}' is not a canonical id (mr:MMDD-slug)")
        return False
    if cid not in registry.entries:
        errors.append(f"'{cid}' is not in the CRMEDR registry; coin ids registry-side first")
        return False
    return True


def _check_rubricae(rubricae, elogia: dict, day_key: str, errors: list[str]) -> None:
    """A day's rubrics: [{"after": <a key of the day's elogia, or null for the head of
    the day>, "text": <non-empty string>}], in printed order (crmedr#51)."""
    if rubricae is None:
        return
    if not isinstance(rubricae, list):
        errors.append(f"day '{day_key}' rubricae must be a list")
        return
    for i, r in enumerate(rubricae):
        if not isinstance(r, dict) or set(r) != {"after", "text"}:
            errors.append(f"day '{day_key}' rubrica {i} must be an object with 'after' and 'text'")
            continue
        if r["after"] is not None and r["after"] not in elogia:
            errors.append(
                f"day '{day_key}' rubrica {i} follows '{r['after']}', not a eulogy of the day"
            )
        if not isinstance(r["text"], str) or not r["text"].strip():
            errors.append(f"day '{day_key}' rubrica {i} text must be a non-empty string")


def validate_month_payload(raw: dict, month: int, shape: str, registry: Registry) -> list[str]:
    errors: list[str] = []
    if shape == "flat":
        for cid, text in raw.items():
            if _check_id(cid, registry, errors) and anchor_day(cid)[0] != month:
                errors.append(
                    f"'{cid}' is anchored to month {anchor_day(cid)[0]:02d}, "
                    f"not {month:02d} (flat editions follow registry placement)"
                )
            if not isinstance(text, str) or not text.strip():
                errors.append(f"text for '{cid}' must be a non-empty string")
        return errors

    seen: dict[str, str] = {}
    for day_key, obj in raw.items():
        if not re.fullmatch(r"[1-9][0-9]?", day_key) or not 1 <= int(day_key) <= DAYS_IN_MONTH.get(
            month, 0
        ):
            errors.append(f"invalid day key '{day_key}' for month {month:02d} (unpadded 1-31)")
            continue
        if not isinstance(obj, dict):
            errors.append(f"day '{day_key}' must be an object")
            continue
        unknown = set(obj) - ALLOWED_DAY_KEYS
        if unknown:
            errors.append(f"day '{day_key}' has unknown keys: {sorted(unknown)}")
        for field in ("titulus", "conclusio"):
            if field in obj and obj[field] is not None and not isinstance(obj[field], str):
                errors.append(f"day '{day_key}' {field} must be a string or null")
        elogia = obj.get("elogia", {})
        if not isinstance(elogia, dict):
            errors.append(f"day '{day_key}' elogia must be an object")
            continue
        _check_rubricae(obj.get("rubricae"), elogia, day_key, errors)
        for cid, text in elogia.items():
            _check_id(cid, registry, errors)
            if not isinstance(text, str) or not text.strip():
                errors.append(f"text for '{cid}' (day {day_key}) must be a non-empty string")
            if cid in seen:
                errors.append(f"'{cid}' appears more than once (days {seen[cid]} and {day_key})")
            seen[cid] = day_key
    return errors


def validate_or_raise(raw: dict, month: int, shape: str, registry: Registry) -> None:
    errors = validate_month_payload(raw, month, shape, registry)
    if errors:
        raise ApiProblem(
            422,
            "Invalid month payload",
            detail=f"{len(errors)} validation error(s)",
            type_slug="invalid-payload",
            errors=errors,
        )
