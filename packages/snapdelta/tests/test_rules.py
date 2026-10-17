import json
from typing import Any

import pytest
from pydantic import ValidationError

from snapdelta.doctor import plugin_reports
from snapdelta.output import compare
from snapdelta.plugins import register_plugin
from snapdelta.profile import accepted
from snapdelta.rules import RULES, CaseInsensitive, IpAddress, Rule, Unordered, Whitespace
from snapdelta.settings import Profile
from snapdelta_example import ExampleSource


def test_the_built_in_rules() -> None:
    assert CaseInsensitive.same("Uplink to R2", "uplink TO r2") and not CaseInsensitive.same("a", "b")
    assert CaseInsensitive.same(["A", "b"], ["a", "B"]) and not CaseInsensitive.same(["a"], ["a", "b"])
    assert not CaseInsensitive.same(1, "1")
    assert Whitespace.same("uplink  to R2 ", "uplink to\nR2") and not Whitespace.same("uplink", "up link")
    assert Unordered.same([10, 20, 20], [20, 10, 20]) and not Unordered.same([10, 20], [10, 20, 20])
    assert Unordered.same([{"a": 1, "b": 2}], [{"b": 2, "a": 1}]) and not Unordered.same("ab", "ba")
    assert IpAddress.same("2001:db8::1", "2001:DB8:0:0::1") and IpAddress.same("10.0.0.1/24", "10.0.0.1/24")
    assert IpAddress.same(["2001:db8::1/64"], ["2001:db8:0::1/64"])
    assert not IpAddress.same("10.0.0.1/24", "10.0.0.1/25") and not IpAddress.same("eth0", "eth1")


def test_a_profile_rule_accepts_a_difference_that_means_the_same(tmp_path) -> None:
    def load(description: str, label: str) -> ExampleSource:
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps({"device": "R1", "interfaces": [{"name": "eth0", "description": description}]}))
        source = ExampleSource(files=[path], label=label)
        source.load()
        return source

    profile = Profile(rules={"interface.description": "case-insensitive"})
    assert accepted(profile, "interface", "description", "Uplink", "uplink") == "case-insensitive"
    assert accepted(profile, "interface", "description", "Uplink", "Downlink") is None
    assert not compare(load("Uplink", "before"), load("UPLINK", "after"), profile=profile).has_differences()
    assert compare(load("Uplink", "before"), load("Downlink", "after"), profile=profile).has_differences()


def test_a_profile_names_an_installed_rule() -> None:
    with pytest.raises(ValidationError, match=r"no compare rule 'regex' is installed \(installed: case-insensitive"):
        Profile(rules={"interface.description": "regex"})
    with pytest.raises(ValidationError, match="model.attribute"):
        Profile(rules={"description": "case-insensitive"})


class Prefix(Rule):
    description = "Texts with the same first three letters."

    @classmethod
    def same(cls, left: Any, right: Any) -> bool:
        return str(left)[:3] == str(right)[:3]


class Broken(Rule):
    @classmethod
    def same(cls, left: Any, right: Any) -> bool:
        raise TypeError("no")


def test_a_plugin_rule_joins_the_built_in_ones_and_the_doctor_tries_it() -> None:
    register_plugin(RULES, "prefix", Prefix)
    register_plugin(RULES, "broken", Broken)
    assert accepted(Profile(rules={"*.description": "prefix"}), "interface", "description", "upl", "uplink")
    problems = {report.name: [item.message for item in report.problems] for report in plugin_reports([RULES])}
    assert problems["prefix"] == [] and problems["case-insensitive"] == []
    assert problems["broken"] == [
        "description is empty: the help of the profiles shows it",
        "same() fails on two texts: TypeError: no",
    ]
