"""Compare profiles: pair the names of two targets, then drop the differences that a profile accepts."""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping
from fnmatch import fnmatchcase
from typing import Any

from diffsync.enum import DiffSyncFlags
from pydantic import BaseModel

from .errors import SnapdeltaError
from .settings import Limit, Profile
from .snapshot import StoredSource, build_snapshot_file, stored_source_class
from .source import Source

OWN = ("-", "+")


class CountCheck(BaseModel):
    """The number of objects of one model on each side, and whether the change stays within the limit."""

    model: str
    left: int
    right: int
    limit: Limit
    within: bool
    # The objects of one side only that the check took out of the diff, because the count stays within the limit.
    hidden: int = 0


class AppliedProfile(BaseModel):
    """The profile of a compare, as the output keeps it: its name, its rules and the results of its count checks."""

    name: str
    rules: Profile
    counts: list[CountCheck] = []


def flags(profile: Profile | None) -> DiffSyncFlags:
    """The diffsync flags of a profile; the right side is the diffsync source, so its objects are the unmatched src."""
    found = DiffSyncFlags.NONE
    if profile is not None and profile.skip_only_right:
        found |= DiffSyncFlags.SKIP_UNMATCHED_SRC
    if profile is not None and profile.skip_only_left:
        found |= DiffSyncFlags.SKIP_UNMATCHED_DST
    return found


def within(limit: Limit, left: Any, right: Any) -> bool:
    """Whether two numbers differ by `limit` at most; a percent is relative to the left value."""
    if not _number(left) or not _number(right):
        return False
    allowed = abs(left) * float(limit[:-1]) / 100 if isinstance(limit, str) else limit
    return abs(right - left) <= allowed


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def rule_for(patterns: Collection[str], model: str, attribute: str) -> str | None:
    """The first pattern that matches `model.attribute`; `*` matches any part."""
    return next((pattern for pattern in patterns if fnmatchcase(f"{model}.{attribute}", pattern)), None)


def accepted(profile: Profile | None, model: str, attribute: str, left: Any, right: Any) -> str | None:
    """Why the profile accepts a difference of one attribute ("ignored", "within tolerance"), or None."""
    if profile is None:
        return None
    if rule_for(profile.ignore, model, attribute):
        return "ignored"
    pattern = rule_for(profile.tolerance, model, attribute)
    if pattern is not None and within(profile.tolerance[pattern], left, right):
        return "within tolerance"
    return None


def paired(adapter: Source, pairs: Mapping[str, str]) -> StoredSource:
    """A copy of the right side that uses the left names: in its identifiers and in its values, lists included."""
    to_left = {right: left for left, right in pairs.items()}
    snapshot = build_snapshot_file(adapter)
    cls = stored_source_class(snapshot.source)
    renamed_ids: dict[str, dict[str, str]] = {}
    data: dict[str, dict[str, dict[str, Any]]] = {}
    for model, objects in snapshot.data.items():
        model_class = getattr(cls, model)
        children_fields = set(model_class._children.values())
        renamed_ids[model], data[model] = {}, {}
        for unique_id, values in objects.items():
            renamed = {
                key: value if key in children_fields else _rename(value, to_left) for key, value in values.items()
            }
            new_id = model_class.create_unique_id(**{key: renamed[key] for key in model_class._identifiers})
            if new_id in data[model]:
                raise SnapdeltaError(f"pairs give two {model} objects of {snapshot.target} the same name {new_id!r}")
            renamed_ids[model][unique_id], data[model][new_id] = new_id, renamed
    for model, objects in data.items():
        for values in objects.values():
            for child_model, field in getattr(cls, model)._children.items():
                values[field] = [renamed_ids.get(child_model, {}).get(child, child) for child in values.get(field, [])]
    reference = adapter.reference if isinstance(adapter, StoredSource) else None
    copied = cls(snapshot.model_copy(update={"data": data}), reference)
    copied.load()
    return copied


def _rename(value: Any, names: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return names.get(value, value)
    if isinstance(value, list):
        return [_rename(item, names) for item in value]
    return value


def refine(
    diff: dict[str, Any], summary: dict[str, int], profile: Profile, left: Source, right: Source
) -> tuple[dict[str, Any], dict[str, int], list[CountCheck]]:
    """The diff and its summary without the differences that the profile accepts, and the count checks."""
    refined, counts = copy.deepcopy(diff), dict(summary)
    checks = []
    for model, limit in profile.counts.items():
        if model not in left.loaded or model not in right.loaded:
            continue
        left_count, right_count = len(left.get_all(model)), len(right.get_all(model))
        check = CountCheck(model=model, left=left_count, right=right_count, limit=limit, within=False)
        check.within = within(limit, left_count, right_count)
        if check.within:
            check.hidden = _hide_one_side(refined, model, counts)
        checks.append(check)
    for model, elements in refined.items():
        _drop_accepted(model, elements, profile, counts)
    return _pruned(refined), counts, checks


def _hide_one_side(elements_by_model: dict[str, Any], model: str, summary: dict[str, int]) -> int:
    hidden = 0
    for element_model, elements in elements_by_model.items():
        for name in list(elements):
            element = elements[name]
            if element_model == model and (element.get("-") is None) != (element.get("+") is None):
                del elements[name]
                _uncount(element, summary)
                hidden += 1
            else:
                hidden += _hide_one_side(_children(element), model, summary)
    return hidden


def _uncount(element: dict[str, Any], summary: dict[str, int]) -> None:
    """Take a removed element and its children out of the summary, as diffsync counted them."""
    left, right = element.get("-"), element.get("+")
    action = "update" if left and right else "delete" if left is not None else "create" if right is not None else None
    summary[action or "no-change"] = summary.get(action or "no-change", 0) - 1
    for children in _children(element).values():
        for child in children.values():
            _uncount(child, summary)


def _drop_accepted(model: str, elements: dict[str, Any], profile: Profile, summary: dict[str, int]) -> None:
    for element in elements.values():
        left, right = element.get("-"), element.get("+")
        if left and right:
            for key in [key for key in left if accepted(profile, model, key, left[key], right.get(key))]:
                del left[key], right[key]
            if not left:
                del element["-"], element["+"]
                summary["update"] = summary.get("update", 0) - 1
                summary["no-change"] = summary.get("no-change", 0) + 1
        for child_model, children in _children(element).items():
            _drop_accepted(child_model, children, profile, summary)


def _pruned(elements_by_model: dict[str, Any]) -> dict[str, Any]:
    """Without the elements that hold no difference any more, nor the models left empty."""
    kept: dict[str, Any] = {}
    for model, elements in elements_by_model.items():
        model_kept = {}
        for name, element in elements.items():
            children = _pruned(_children(element))
            own = {key: element[key] for key in OWN if key in element}
            if own or children:
                model_kept[name] = own | children
        if model_kept:
            kept[model] = model_kept
    return kept


def _children(element: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in element.items() if key not in OWN}
