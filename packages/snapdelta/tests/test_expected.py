import pytest
from typer.testing import CliRunner

from snapdelta.cli import app
from snapdelta.errors import SnapdeltaError
from snapdelta.expected import ExpectedChange, check_expected, parse_expected, read_expected
from snapdelta.output import Output, compare
from snapdelta.store import FileStore


def test_a_diff_against_its_expected_changes(make_adapter) -> None:
    output = compare(make_adapter(), make_adapter(description="to R3", extra_interface=True))
    expected = parse_expected(
        [
            {"model": "interface", "object": "eth0", "change": "changed", "attribute": "description", "right": "to R3"},
            {"model": "vlan", "object": "30", "change": "added"},
        ]
    )
    check = check_expected(output, expected)
    assert [item.object for item in check.done] == ["eth0"] and [item.object for item in check.missing] == ["30"]
    assert [item.describe() for item in check.unexpected] == ["interface:eth2 added"]
    assert not check.as_expected
    again = Output.model_validate_json(output.model_copy(update={"expectations": check}).model_dump_json())
    assert again.expectations == check and again.expectations.done[0].values == {"right": "to R3"}


def test_a_wrong_expected_change_says_why(tmp_path) -> None:
    with pytest.raises(SnapdeltaError, match="item 1: .*left and right need an attribute"):
        parse_expected([{"model": "vlan", "object": "1", "change": "changed", "left": 1}])
    path = tmp_path / "expected.yaml"
    path.write_text("changes:\n  - {model: vlan, object: '30', change: added}\n")
    assert read_expected(path) == [ExpectedChange(model="vlan", object="30", change="added")]


def test_the_cli_exits_0_when_the_diff_is_the_expected_changes(make_adapter, tmp_path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_snapshot(make_adapter(label="before"))
    store.save_snapshot(make_adapter(label="after", extra_interface=True))
    path = tmp_path / "expected.yaml"
    path.write_text("- {model: interface, object: eth2, change: added}\n")
    base = ["--store", str(tmp_path / "store"), "diff", "R1@before", "R1@after", "--expect", str(path)]
    done = CliRunner().invoke(app, base)
    assert done.exit_code == 0, done.output
    assert "expected: 1 done, 0 missing, 0 unexpected: as expected" in done.output
    path.write_text("- {model: vlan, object: '99', change: added}\n")
    assert CliRunner().invoke(app, base).exit_code == 1
