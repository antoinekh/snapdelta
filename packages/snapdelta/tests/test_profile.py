import json

import pytest
from pydantic import ValidationError

from snapdelta.errors import SnapdeltaError
from snapdelta.output import compare
from snapdelta.profile import accepted, within
from snapdelta.settings import Profile
from snapdelta_example import ExampleSource


@pytest.fixture
def load(tmp_path):
    """An example source from interfaces given as (name, description, mtu, addresses)."""

    def make(interfaces, label: str) -> ExampleSource:
        data = {
            "device": "R1",
            "interfaces": [
                {"name": name, "description": text, "mtu": mtu, "addresses": [{"prefix": item} for item in addresses]}
                for name, text, mtu, addresses in interfaces
            ],
        }
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps(data))
        source = ExampleSource(files=[path], label=label)
        source.load()
        return source

    return make


def test_within_a_number_or_a_percent() -> None:
    assert within(10, 100, 110) and not within(10, 100, 111)
    assert within("5%", 1000, 1050) and not within("5%", 1000, 1051)
    assert not within("5%", "a", "b") and not within(1, True, False)


def test_accepted_says_why() -> None:
    profile = Profile(ignore=["*.description"], tolerance={"interface.mtu": "10%"})
    assert accepted(profile, "interface", "description", "a", "b") == "ignored"
    assert accepted(profile, "interface", "mtu", 1500, 1600) == "within tolerance"
    assert accepted(profile, "interface", "mtu", 1500, 9000) is None
    assert accepted(None, "interface", "mtu", 1500, 1600) is None


def test_ignore_and_tolerance_take_the_differences_out_of_the_diff_and_the_summary(load) -> None:
    left = load([("eth0", "old", 1500, []), ("eth1", "x", 1500, [])], "left")
    right = load([("eth0", "new", 1550, []), ("eth1", "x", 9000, [])], "right")
    plain = compare(left, right)
    assert plain.summary["update"] == 2
    profile = Profile(ignore=["interface.description"], tolerance={"interface.mtu": "5%"})
    output = compare(left, right, profile=profile, profile_name="change")
    assert list(output.diff["interface"]) == ["eth1"]
    assert output.diff["interface"]["eth1"] == {"-": {"mtu": 1500}, "+": {"mtu": 9000}}
    assert (output.summary["update"], output.summary["no-change"]) == (1, plain.summary["no-change"] + 1)
    assert output.profile is not None and output.profile.name == "change"


def test_counts_hide_the_objects_of_one_side_within_the_limit(load) -> None:
    left = load([(f"eth{index}", None, None, []) for index in range(20)], "left")
    right = load([(f"eth{index}", None, None, []) for index in range(1, 21)], "right")
    loose = compare(left, right, profile=Profile(counts={"interface": "5%"}))
    assert loose.diff == {} and not loose.has_differences()
    assert loose.profile is not None
    check = loose.profile.counts[0]
    assert (check.model, check.left, check.right, check.within, check.hidden) == ("interface", 20, 20, True, 2)
    few = load([(f"eth{index}", None, None, []) for index in range(5)], "few")
    strict = compare(left, few, profile=Profile(counts={"interface": "5%"}))
    assert strict.profile is not None and not strict.profile.counts[0].within
    assert len(strict.diff["interface"]) == 15


def test_pairs_give_the_right_side_the_left_names(load) -> None:
    left = load([("ge-0/0/6", "uplink", 9192, ["10.0.0.1/30"])], "left")
    right = load([("1/1/1", "uplink", 9192, ["10.0.0.1/30"])], "right")
    assert set(compare(left, right).diff["interface"]) == {"ge-0/0/6", "1/1/1"}
    output = compare(left, right, profile=Profile(pairs={"ge-0/0/6": "1/1/1"}))
    assert output.diff == {} and output.summary["no-change"] == 2
    assert output.right.target == "R1" and output.right.label == "right"


def test_pairs_that_merge_two_objects_are_an_error(load) -> None:
    left = load([("c", None, None, [])], "left")
    right = load([("b", None, None, []), ("c", None, None, [])], "right")
    with pytest.raises(SnapdeltaError, match="the same name 'c'"):
        compare(left, right, profile=Profile(pairs={"c": "b"}))


def test_skip_the_objects_of_one_side(load) -> None:
    left = load([("eth0", None, None, []), ("eth1", None, None, [])], "left")
    right = load([("eth0", None, None, []), ("eth2", None, None, [])], "right")
    only_left = compare(left, right, profile=Profile(skip_only_right=True))
    assert list(only_left.diff["interface"]) == ["eth1"]
    only_right = compare(left, right, profile=Profile(skip_only_left=True))
    assert list(only_right.diff["interface"]) == ["eth2"]


def test_a_profile_with_bad_rules_is_refused() -> None:
    with pytest.raises(ValidationError, match="percent"):
        Profile(tolerance={"interface.mtu": "5 percent"})
    with pytest.raises(ValidationError, match="model.attribute"):
        Profile(ignore=["description"])
    with pytest.raises(ValidationError, match="negative"):
        Profile(counts={"interface": -1})


def test_a_description_is_one_short_line() -> None:
    assert Profile(description="A change window").description == "A change window"
    with pytest.raises(ValidationError):
        Profile(description="two\nlines")
    with pytest.raises(ValidationError):
        Profile(description="x" * 121)
