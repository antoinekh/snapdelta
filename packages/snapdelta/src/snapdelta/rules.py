"""Compare rules: two values of one attribute that differ as written but mean the same, for a profile to accept.

A profile names a rule per attribute, `[profiles.NAME.rules] "interface.description" = "case-insensitive"`. The
built-in rules are entry points of the group `snapdelta.rules`, as the rules of a plugin are.
"""

from __future__ import annotations

import ipaddress
import json
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable
from typing import Any, ClassVar

from .plugins import ExtensionPoint, Problem, Severity, abstract_problems, subclass_check


class Rule(ABC):
    """A kind of rule: whether two values that differ mean the same."""

    # One line for the help of the profiles: what the rule accepts.
    description: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def same(cls, left: Any, right: Any) -> bool:
        """True when the two values mean the same; called only on two values that differ."""


def each(left: Any, right: Any, same: Callable[[Any, Any], bool]) -> bool:
    """`same` on two values, or on each pair of two lists of the same length."""
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same(one, other) for one, other in zip(left, right, strict=True))
    return same(left, right)


def _texts(compare: Callable[[str], str]) -> Callable[[Any, Any], bool]:
    def same(left: Any, right: Any) -> bool:
        return isinstance(left, str) and isinstance(right, str) and compare(left) == compare(right)

    return same


class CaseInsensitive(Rule):
    description = "Texts that differ only in upper and lower case: Uplink and uplink."

    @classmethod
    def same(cls, left: Any, right: Any) -> bool:
        return each(left, right, _texts(str.casefold))


class Whitespace(Rule):
    description = "Texts that differ only in spaces: two spaces, a space at the end, a line break."

    @classmethod
    def same(cls, left: Any, right: Any) -> bool:
        return each(left, right, _texts(lambda text: " ".join(text.split())))


class Unordered(Rule):
    description = "Lists with the same items in another order: [10, 20] and [20, 10]."

    @classmethod
    def same(cls, left: Any, right: Any) -> bool:
        if not (isinstance(left, list) and isinstance(right, list)):
            return False
        return Counter(json.dumps(item, sort_keys=True, default=str) for item in left) == Counter(
            json.dumps(item, sort_keys=True, default=str) for item in right
        )


class IpAddress(Rule):
    description = "The same address or prefix written in two ways: 2001:db8::1 and 2001:DB8:0:0::1."

    @classmethod
    def same(cls, left: Any, right: Any) -> bool:
        return each(left, right, _same_address)


def _same_address(left: Any, right: Any) -> bool:
    if not (isinstance(left, str) and isinstance(right, str)):
        return False
    try:
        if "/" in left or "/" in right:
            return ipaddress.ip_interface(left) == ipaddress.ip_interface(right)
        return ipaddress.ip_address(left) == ipaddress.ip_address(right)
    except ValueError:
        return False


def _inspect_rule(name: str, cls: type[Rule]) -> list[Problem]:
    problems = abstract_problems(cls)
    if problems:
        return problems
    if not cls.description:
        problems.append(Problem(Severity.WARNING, "description is empty: the help of the profiles shows it"))
    try:
        cls.same("eth0", "eth1")
    except Exception as error:
        problems.append(Problem(Severity.ERROR, f"same() fails on two texts: {type(error).__name__}: {error}"))
    return problems


def _rules_in_use(settings: Any) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for name, profile in sorted(settings.profiles.items()):
        for rule in dict.fromkeys(profile.rules.values()):
            used.setdefault(rule, []).append(f"[profiles.{name}]")
    return used


RULES: ExtensionPoint[type[Rule]] = ExtensionPoint(
    group="snapdelta.rules",
    kind="compare rule",
    interface="snapdelta.rules.Rule",
    table=None,
    check=subclass_check(Rule),
    inspect=_inspect_rule,
    in_use=_rules_in_use,
)
