from snapdelta.output import compare, described
from snapdelta.render import change_rows, render_csv, render_markdown, render_output, render_snapshot, renderers


def test_render_marks_and_values(make_adapter) -> None:
    left = make_adapter(description="old")
    right = make_adapter(description="new", primary=False, extra_interface=True, label="after")
    text = render_output(compare(left, right))
    assert "~ eth0" in text
    assert 'description: "old" -> "new"' in text
    assert "+ eth2" in text
    assert "~ eth0__10.0.0.1/30" in text
    assert "primary: true -> false" in text
    assert "only left: 0, only right: 1, changed: 2, unchanged: 2" in text
    assert "R1 [after]" in text


def test_render_no_difference_and_skipped(make_adapter) -> None:
    text = render_output(compare(make_adapter(), make_adapter(with_state=True)))
    assert "no difference" in text
    assert "skipped (not loaded on both sides): interface_state" in text


def test_render_snapshot(make_adapter) -> None:
    lines = render_snapshot(make_adapter(with_state=True), "interface_state").splitlines()
    assert lines[2:] == ["interface_state", '  eth0: oper_status="up"']


def test_render_snapshot_lists_every_loaded_model(make_adapter) -> None:
    lines = render_snapshot(make_adapter()).splitlines()
    assert [line for line in lines if not line.startswith(" ")][2:] == ["interface", "address", "vlan"]


def test_children_are_indented_under_their_model_heading(make_adapter) -> None:
    lines = render_output(compare(make_adapter(), make_adapter(primary=False))).splitlines()
    heading = lines.index("      address")
    assert lines[heading + 1] == "        ~ eth0__10.0.0.1/30"
    assert lines[heading + 2] == "            primary: true -> false"


def test_change_rows_flatten_the_diff(make_adapter) -> None:
    output = compare(make_adapter(), make_adapter(primary=False, extra_interface=True))
    rows = [(row.model, row.name, row.parents, row.change, row.attribute) for row in change_rows(output)]
    assert ("address", "eth0__10.0.0.1/30", ("eth0",), "changed", "primary") in rows
    assert ("interface", "eth2", (), "only-right", None) in rows


def test_markdown_and_csv(make_adapter) -> None:
    output = compare(make_adapter(description="a|b"), make_adapter(description="c"))
    markdown = render_markdown(output)
    assert markdown.startswith("# R1 vs R1\n") and "## interface" in markdown
    assert '| eth0 | changed | description | "a\\|b" | "c" |' in markdown
    assert render_csv(output).splitlines()[0] == "model,object,parents,change,attribute,left,right"
    assert "No difference." in render_markdown(compare(make_adapter(), make_adapter()))


def test_the_built_in_formats() -> None:
    assert {"text", "json", "markdown", "csv"} <= set(renderers())


def test_the_profile_and_its_count_checks_are_printed(make_adapter) -> None:
    from snapdelta.settings import Profile

    output = compare(
        make_adapter(),
        make_adapter(extra_interface=True),
        profile=Profile(description="A few more interfaces", counts={"interface": "50%"}),
        profile_name="loose",
    )
    text = render_output(output)
    assert "profile: loose (A few more interfaces)" in text
    assert "count interface: 2 -> 3 (+1, +50.0%), limit 50%: within, 1 objects of one side not shown" in text
    assert "- Count interface: 2 -> 3" in render_markdown(output)


def test_the_text_shows_the_ticket_then_the_note(make_adapter) -> None:
    output = described(
        compare(make_adapter(), make_adapter(description="other")), ticket="CHG-1042", note="VLAN 20\non the trunk"
    )
    assert render_output(output).startswith("ticket: CHG-1042\nVLAN 20\non the trunk\n\nleft:")
    assert render_output(described(output, note="")).startswith("ticket: CHG-1042\n\nleft:")
    assert render_output(described(output, ticket="")).startswith("VLAN 20\non the trunk\n\nleft:")


def test_the_markdown_heading_is_the_ticket_and_the_targets(make_adapter) -> None:
    output = compare(make_adapter(), make_adapter(description="other"))
    ticketed = described(output, ticket="CHG-1042", note="VLAN 20")
    assert render_markdown(ticketed).startswith("# CHG-1042 · R1 vs R1\n\nVLAN 20\n\n| Side")
    assert render_markdown(output).startswith("# R1 vs R1\n")
