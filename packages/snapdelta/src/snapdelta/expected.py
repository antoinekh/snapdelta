"""Expected changes: what a change must do, written before it, and the check of a diff against it.

A diff then gives three lists: the expected changes that it holds, the expected changes that are missing, and the
changes that nobody expected. A pipeline or an AI agent can so check its own work with no person.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import SnapdeltaError

# The words of an expected change for the change kinds of a diff row.
CHANGE_WORDS = {"only-right": "added", "only-left": "removed", "changed": "changed"}


class ExpectedChange(BaseModel):
    """One change that must happen: an object added or removed, or one attribute changed, from a value to a value."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="The model of the object, such as vlan.")
    object: str = Field(description="The unique id of the object, such as 100 or eth1; * matches any part.")
    change: Literal["added", "removed", "changed"]
    attribute: str | None = Field(default=None, description="With changed: the attribute; none: any attribute.")
    # The value before (`left`) and after (`right`) that the change must give; a side not given takes any value.
    values: dict[Literal["left", "right"], Any] = {}
    target: str | None = Field(default=None, description="Only the diff of this target; none: the diff of each.")

    @model_validator(mode="before")
    @classmethod
    def _sides(cls, value: Any) -> Any:
        """`left: 1500` and `right: 9000` of a file go to `values`, since any value, null included, can be meant."""
        if isinstance(value, dict) and ({"left", "right"} & set(value)):
            value = dict(value)
            value["values"] = {side: value.pop(side) for side in ("left", "right") if side in value}
        return value

    @model_validator(mode="after")
    def _attribute_of_a_change(self) -> ExpectedChange:
        if self.change != "changed" and (self.attribute is not None or self.values):
            raise ValueError("attribute, left and right go with change: changed")
        if self.attribute is None and self.values:
            raise ValueError("left and right need an attribute")
        return self

    def describe(self) -> str:
        where = f"{self.target}: " if self.target else ""
        if self.attribute is None:
            return f"{where}{self.model} {self.object} {self.change}"
        values = f" {self._side('left')} -> {self._side('right')}" if self.values else ""
        return f"{where}{self.model} {self.object} {self.attribute}{values}"

    def _side(self, side: Literal["left", "right"]) -> str:
        return json.dumps(self.values[side], default=str) if side in self.values else "*"

    def matches(self, row: Any, target: str) -> bool:
        """Whether a change row of the diff of `target` is this expected change."""
        if self.target is not None and self.target != target:
            return False
        if row.model != self.model or not fnmatchcase(row.name, self.object):
            return False
        if CHANGE_WORDS[row.change] != self.change:
            return False
        if self.change != "changed" or self.attribute is None:
            return True
        if row.attribute != self.attribute:
            return False
        left_ok = "left" not in self.values or row.left == self.values["left"]
        right_ok = "right" not in self.values or row.right == self.values["right"]
        return left_ok and right_ok


class UnexpectedChange(BaseModel):
    """A change of the diff that no expected change names."""

    key: str
    change: str
    attribute: str | None = None
    left: Any = None
    right: Any = None

    def describe(self) -> str:
        if self.attribute is None:
            return f"{self.key} {self.change}"
        return f"{self.key} {self.attribute}: {_value(self.left)} -> {_value(self.right)}"


class ExpectationCheck(BaseModel):
    """A diff against its expected changes: the expected changes done, the missing ones, and the unexpected changes."""

    done: list[ExpectedChange] = []
    missing: list[ExpectedChange] = []
    unexpected: list[UnexpectedChange] = []

    @property
    def as_expected(self) -> bool:
        return not self.missing and not self.unexpected


def check_expected(output: Any, expected: Sequence[ExpectedChange]) -> ExpectationCheck:
    """The check of an output against the expected changes that apply to its target."""
    from .render import change_rows

    target = output.right.target
    mine = [item for item in expected if item.target is None or item.target == target]
    rows = list(change_rows(output))
    done = [item for item in mine if any(item.matches(row, target) for row in rows)]
    missing = [item for item in mine if item not in done]
    unexpected = [
        UnexpectedChange(
            key=f"{row.model}:{row.name}",
            change=CHANGE_WORDS[row.change],
            attribute=row.attribute,
            left=row.left,
            right=row.right,
        )
        for row in rows
        if not any(item.matches(row, target) for item in mine)
    ]
    return ExpectationCheck(done=done, missing=missing, unexpected=unexpected)


def read_expected(path: Path) -> list[ExpectedChange]:
    """The expected changes of a YAML or JSON file: a list, or a mapping with the list under `changes`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SnapdeltaError(f"{path}: {error.strerror}") from error
    try:
        value = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as error:
        raise SnapdeltaError(f"{path}: not valid {'JSON' if path.suffix == '.json' else 'YAML'} ({error})") from error
    items = value.get("changes") if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise SnapdeltaError(f"{path}: give a list of expected changes, or a mapping with the list under changes")
    return parse_expected(items, str(path))


def parse_expected(items: Iterable[Any], where: str = "expected changes") -> list[ExpectedChange]:
    found = []
    for index, item in enumerate(items):
        try:
            found.append(ExpectedChange.model_validate(item))
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in entry['loc']) or 'change'}: {entry['msg']}"
                for entry in error.errors()
            )
            raise SnapdeltaError(f"{where}: item {index + 1}: {problems}") from error
    return found


def _value(value: Any) -> str:
    return json.dumps(value, default=str)
