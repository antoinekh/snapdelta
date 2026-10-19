"""Compare two loads, and save or read the diff output in a JSON file."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from diffsync.diff import Diff
from diffsync.enum import DiffSyncFlags
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .activity import acting_user
from .errors import NoCommonModelError, SnapdeltaError
from .expected import ExpectationCheck
from .jsonfile import read_json, write_json
from .profile import AppliedProfile, flags, paired, refine
from .settings import Profile
from .snapshot import StoredSource
from .source import Source
from .ticket import TicketText, normalize_ticket, ticket_key

SCHEMA = 3
NOTE_LINE_MAX = 120
NOTE_MAX = 2000


CHANGES = ("create", "update", "delete")
OWN = ("-", "+")


class Side(BaseModel):
    target: str
    source: str
    label: str | None
    captured_at: datetime
    snapshot: str | None


class Review(BaseModel):
    """A change that a person checked and accepted."""

    by: str | None = Field(default=None, max_length=80, pattern=r"^[^\n]*$")
    at: datetime
    comment: str | None = Field(default=None, max_length=500)


class Output(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=SCHEMA, alias="schema")
    created_at: datetime
    versions: dict[str, str]
    left: Side
    right: Side
    compared: list[str]
    skipped: list[str]
    not_selected: list[str] = []
    summary: dict[str, int]
    diff: dict[str, Any]
    profile: AppliedProfile | None = None
    # Given by the user, at the compare or later: the work that the diff belongs to, and what the diff is about.
    ticket: TicketText
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    # By the key of a changed object, `MODEL:UNIQUE_ID`: the changes that a person accepted.
    reviews: dict[str, Review] = {}
    # The check against the changes that the compare was given as expected, when it was given some.
    expectations: ExpectationCheck | None = None

    def has_differences(self) -> bool:
        return any(self.summary.get(action, 0) for action in CHANGES)

    def change_keys(self) -> list[str]:
        """The key of each object with a change of its own, in diff order, each child after its parent."""
        return list(_change_keys(self.diff))

    def review_counts(self) -> tuple[int, int]:
        """How many changed objects are accepted, and how many there are."""
        keys = self.change_keys()
        return sum(key in self.reviews for key in keys), len(keys)


def change_key(model: str, name: str) -> str:
    return f"{model}:{name}"


def _change_keys(tree: dict[str, Any]) -> Iterator[str]:
    for model, elements in tree.items():
        for name, element in elements.items():
            if element.get("-") or element.get("+"):
                yield change_key(model, name)
            yield from _change_keys({key: value for key, value in element.items() if key not in OWN})


def reviewed(output: Output, key: str, review: Review | None) -> Output:
    """The output with the change `key` accepted by `review`, or no longer accepted when it is None."""
    if key not in output.change_keys():
        raise SnapdeltaError(f"no change {key!r} in this diff: give MODEL:UNIQUE_ID of a changed object")
    reviews = {name: item for name, item in output.reviews.items() if name != key}
    if review is not None:
        reviews[key] = review
    return output.model_copy(update={"reviews": reviews})


def new_review(by: str | None, comment: str | None) -> Review:
    """A review by `by`; the user that a server knows from its login wins, so a user cannot write another name."""
    who = acting_user.get() or by
    try:
        return Review(by=(who or "").strip() or None, at=datetime.now(UTC), comment=(comment or "").strip() or None)
    except ValidationError as error:
        problems = "; ".join(f"{item['loc'][0]}: {item['msg']}" for item in error.errors())
        raise SnapdeltaError(f"not a valid review ({problems})") from error


def described(output: Output, *, ticket: str | None = None, note: str | None = None) -> Output:
    """The output with this ticket and note; None keeps the current one, an empty text removes it."""
    values = output.model_dump(by_alias=True)
    if ticket is not None:
        values["ticket"] = normalize_ticket(ticket)
    if note is not None:
        values["note"] = note.strip() or None
    try:
        return Output.model_validate(values)
    except ValidationError as error:
        problems = "; ".join(f"{item['loc'][0]}: {item['msg']}" for item in error.errors())
        raise SnapdeltaError(f"not a valid ticket or note ({problems})") from error


def check_note(note: str | None) -> None:
    """A note that is too long fails here, before any work, not when the output is described."""
    if note is not None and len(note.strip()) > NOTE_MAX:
        raise SnapdeltaError(f"not a valid note: {NOTE_MAX} characters at most")


def note_line(note: str | None) -> str | None:
    """The first non-empty line of a note, cut for a list."""
    line = next((line.strip() for line in (note or "").splitlines() if line.strip()), None)
    if line is not None and len(line) > NOTE_LINE_MAX:
        return f"{line[: NOTE_LINE_MAX - 1]}…"
    return line


def common_models(left: Sequence[str], right: Sequence[str]) -> list[str]:
    """The top-level models of both sides, in the order of the left side."""
    return [name for name in left if name in right]


def skipped_models(left: Sequence[str], right: Sequence[str]) -> list[str]:
    """The top-level models of one side only, which a compare leaves out."""
    return sorted(set(left) ^ set(right))


def compare(
    left: Source,
    right: Source,
    models: Sequence[str] | None = None,
    profile: Profile | None = None,
    profile_name: str | None = None,
) -> Output:
    """The diff of two loads; `models` limits it to some models that both loads hold, `profile` sets its rules."""
    left_side, right_side = _side(left), _side(right)
    ticket = left.ticket if ticket_key(left.ticket) == ticket_key(right.ticket) else None
    common = common_models(left.top_level, right.top_level)
    if not common:
        raise NoCommonModelError(
            f"{left_side.target} and {right_side.target} have no loaded model in common "
            f"(left: {', '.join(left.loaded) or 'none'}; right: {', '.join(right.loaded) or 'none'})"
        )
    for name in models or []:
        if name not in common:
            raise SnapdeltaError(f"{name} is not loaded on both sides; models in common: {', '.join(common)}")
    compared = [name for name in common if name in models] if models else common
    if profile is not None and profile.pairs:
        right = paired(right, profile.pairs)
    diff = _diff(left, right, compared, flags(profile))
    tree, summary, applied = diff.dict(), diff.summary(), None
    if profile is not None:
        tree, summary, checks = refine(tree, summary, profile, left, right)
        applied = AppliedProfile(name=profile_name or "", rules=profile, counts=checks)
    return Output(
        created_at=datetime.now(UTC),
        versions={name: version(name) for name in ("diffsync", "snapdelta")},
        left=left_side,
        right=right_side,
        compared=compared,
        skipped=skipped_models(left.top_level, right.top_level),
        not_selected=[name for name in common if name not in compared],
        summary=summary,
        diff=tree,
        profile=applied,
        ticket=ticket,
    )


def _diff(left: Source, right: Source, models: list[str], diff_flags: DiffSyncFlags) -> Diff:
    # diffsync compares the top-level models of both sides, so the selection narrows them for this one diff.
    kept = left.top_level, right.top_level
    setattr(left, "top_level", models)  # noqa: B010
    setattr(right, "top_level", models)  # noqa: B010
    try:
        # diffsync takes the argument of diff_from as the source: "-" then holds the left values, "+" the right ones.
        return left.diff_from(right, flags=diff_flags)
    finally:
        setattr(left, "top_level", kept[0])  # noqa: B010
        setattr(right, "top_level", kept[1])  # noqa: B010


def save_output(output: Output, path: Path) -> None:
    write_json(path, output.model_dump_json(by_alias=True, indent=2))


def load_output(path: Path) -> Output:
    value = read_json(path, SCHEMA)
    try:
        return Output.model_validate(value)
    except ValidationError as error:
        raise SnapdeltaError(f"{path}: not a valid output ({error.error_count()} errors)") from error


def _side(adapter: Source) -> Side:
    target, captured_at = adapter.require_loaded()
    return Side(
        target=target,
        source=adapter.source,
        label=adapter.label,
        captured_at=captured_at,
        snapshot=adapter.reference if isinstance(adapter, StoredSource) else None,
    )
