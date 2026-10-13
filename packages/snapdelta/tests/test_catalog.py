from diffsync import DiffSyncModel
from typer.testing import CliRunner

from snapdelta.catalog import catalog, describe
from snapdelta.cli import app
from snapdelta.source import Source
from snapdelta_example import ExampleLiveSource, ExampleSource


class OtherInterface(DiffSyncModel):
    """An interface with other attributes than the example."""

    _modelname = "interface"
    _identifiers = ("name",)
    _attributes = ("speed",)

    name: str
    speed: int | None = None


class OtherSource(Source):
    source = "other"
    interface = OtherInterface
    top_level = ["interface"]


def test_describe_gives_the_models_in_order_with_their_fields() -> None:
    described = describe(ExampleSource)
    assert [model.name for model in described.models] == ["interface", "address", "vlan", "interface_state"]
    address = described.models[1]
    assert (address.parent, address.unique_id) == ("interface", "interface__prefix")
    assert [(item.name, item.type) for item in described.models[0].attributes] == [
        ("description", "str | None"),
        ("enabled", "bool"),
        ("mtu", "int | None"),
    ]
    assert described.models[0].children == ["address"]
    assert [(item.name, item.type) for item in described.models[0].context] == [("ifindex", "int | None")]


def test_sources_with_the_same_models_form_one_set() -> None:
    sets = catalog({"example": ExampleSource, "example-live": ExampleLiveSource, "other": OtherSource})
    assert [found.names for found in sets] == [["example", "example-live"], ["other"]]
    assert sets[0].compares_with == ["other"] and sets[1].compares_with == ["example", "example-live"]
    assert sets[1].conflicts == [
        "interface: not the same fields as in example, example-live "
        "(only here: speed; only in example, example-live: description, enabled, mtu)"
    ]
    assert OtherInterface.__doc__ and sets[1].models[0].doc == "An interface with other attributes than the example."


def test_the_models_command(monkeypatch) -> None:
    from snapdelta import source

    monkeypatch.setitem(source._REGISTERED, "other", OtherSource)
    result = CliRunner().invoke(app, ["models", "--source", "other"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("other (files)\ncompares with: example, example-live\nwarning: interface:")
    assert "  interface  id: name\n    speed: int | None\n" in result.output
