import json

import pytest

from snapdelta.errors import NoCommonModelError, SnapdeltaError
from snapdelta.output import common_models, compare, described, load_output, note_line, save_output, skipped_models
from snapdelta.testing import sample


def test_identical_loads_have_no_difference(make_adapter) -> None:
    output = compare(make_adapter(), make_adapter())
    assert not output.has_differences()
    assert output.diff == {}
    assert output.summary["no-change"] == 4


def test_signs_left_is_minus_right_is_plus(make_adapter) -> None:
    left = make_adapter(description="old")
    right = make_adapter(description="new", primary=False, extra_interface=True)
    output = compare(left, right)
    interface = output.diff["interface"]
    assert (interface["eth0"]["-"], interface["eth0"]["+"]) == ({"description": "old"}, {"description": "new"})
    assert "+" in interface["eth2"] and "-" not in interface["eth2"]
    address = interface["eth0"]["address"]["eth0__10.0.0.1/30"]
    assert address == {"-": {"primary": True}, "+": {"primary": False}}
    assert (output.summary["create"], output.summary["delete"]) == (1, 0)
    assert output.has_differences()


def test_models_loaded_on_one_side_only_are_skipped(make_adapter) -> None:
    output = compare(make_adapter(), make_adapter(with_state=True))
    assert output.compared == ["interface", "vlan"]
    assert output.skipped == ["interface_state"]
    assert "interface_state" not in output.diff
    assert not output.has_differences()


def test_no_common_model(make_adapter) -> None:
    with pytest.raises(NoCommonModelError, match="no loaded model in common"):
        compare(make_adapter(), make_adapter(with_config=False, with_state=True))


def test_sides_record_the_snapshot_reference(make_adapter, store) -> None:
    reference = store.save_snapshot(make_adapter(label="before"))
    output = compare(store.load_snapshot(reference), make_adapter(label="after"))
    assert output.left.snapshot == reference
    assert (output.left.label, output.right.label, output.right.snapshot) == ("before", "after", None)


def test_output_round_trip(make_adapter, tmp_path) -> None:
    output = compare(make_adapter(description="old"), make_adapter(description="new"))
    path = tmp_path / "outputs" / "o.json"
    save_output(output, path)
    assert json.loads(path.read_text())["schema"] == 3
    assert load_output(path) == output


def test_selected_models_limit_the_compare(make_adapter) -> None:
    left = make_adapter(description="old")
    right = make_adapter(description="new", extra_interface=True)
    output = compare(left, right, models=["vlan"])
    assert output.compared == ["vlan"]
    assert output.not_selected == ["interface"]
    assert not output.has_differences()
    assert left.top_level == right.top_level == ["interface", "vlan"]
    assert compare(left, right).has_differences()


def test_a_selected_model_must_be_loaded_on_both_sides(make_adapter) -> None:
    with pytest.raises(
        SnapdeltaError, match="interface_state is not loaded on both sides; models in common: interface, vlan"
    ):
        compare(make_adapter(), make_adapter(with_state=True), models=["interface_state"])


def test_common_models_of_two_loads(make_adapter) -> None:
    left, right = make_adapter(), make_adapter(with_state=True)
    assert common_models(left.top_level, right.top_level) == ["interface", "vlan"]
    assert skipped_models(left.top_level, right.top_level) == ["interface_state"]


def test_a_change_can_be_accepted_and_withdrawn(make_adapter, tmp_path) -> None:
    from snapdelta.output import new_review, reviewed
    from snapdelta.render import render_markdown, render_output
    from snapdelta.store import FileStore

    store = FileStore(tmp_path)
    left = make_adapter(description="old")
    output = compare(left, make_adapter(description="new", primary=False, extra_interface=True))
    name = store.save_output(output)
    keys = output.change_keys()
    assert keys == ["interface:eth0", "address:eth0__10.0.0.1/30", "interface:eth2"]
    accepted = store.review_output(name, "interface:eth0", new_review(" alice ", "planned MTU change"))
    assert accepted.reviews["interface:eth0"].by == "alice"
    assert store.load_output(name).review_counts() == (1, len(keys))
    assert "accepted: 1 of" in render_output(accepted) and "interface:eth0 by alice" in render_output(accepted)
    assert "## Review" in render_markdown(accepted) and "planned MTU change" in render_markdown(accepted)
    assert store.review_output(name, "interface:eth0", None).reviews == {}
    with pytest.raises(SnapdeltaError, match="no change 'interface:nope'"):
        reviewed(accepted, "interface:nope", None)
    with pytest.raises(SnapdeltaError, match="not a valid review"):
        new_review("a" * 81, None)


def test_described_keeps_what_is_not_given_and_removes_an_empty_text(make_adapter) -> None:
    output = described(compare(make_adapter(), make_adapter(description="other")), ticket="CHG-1", note="first\nsecond")
    assert described(output, note="other").ticket == "CHG-1"
    assert described(output, ticket="").ticket is None
    assert described(output).note == "first\nsecond"
    assert described(output, note="  ").note is None


def test_a_ticket_is_normalized_and_the_note_is_trimmed(make_adapter) -> None:
    output = described(compare(make_adapter(), make_adapter()), ticket="  CHG-1 \t phase 2 ", note=" text \n")
    assert (output.ticket, output.note) == ("CHG-1 phase 2", "text")


def test_a_ticket_longer_than_80_is_refused(make_adapter) -> None:
    with pytest.raises(SnapdeltaError, match="not a valid ticket or note"):
        described(compare(make_adapter(), make_adapter()), ticket="x" * 81)


def test_note_line_is_the_first_line_cut_at_120() -> None:
    assert note_line("\n first \nsecond") == "first"
    assert note_line("y" * 130) == "y" * 119 + "…"
    assert note_line(None) is None


def test_a_diff_gets_the_ticket_that_both_sides_have() -> None:
    assert compare(sample(ticket="CHG-1"), sample(target="R2", ticket="chg-1")).ticket == "CHG-1"
    assert compare(sample(ticket="CHG-1"), sample(target="R2", ticket="CHG-2")).ticket is None
    assert compare(sample(ticket="CHG-1"), sample(target="R2")).ticket is None
