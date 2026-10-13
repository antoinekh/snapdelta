"""The source `example`: it reads JSON files that a person gives, and fills the models.

    snapdelta snapshot samples/r1-before.json --source example --label before

A JSON file describes one device:

    {"device": "R1",
     "interfaces": [{"name": "eth0", "mtu": 1500, "addresses": [{"prefix": "10.0.0.1/30", "primary": true}]}],
     "vlans": [{"id": 10, "name": "users"}],
     "state": {"eth0": "up"}}
"""

import json
from typing import Any

from snapdelta.errors import LoadError
from snapdelta.inputs import InputFile
from snapdelta.source import Source

from .models import Address, Interface, InterfaceState, Vlan


class ExampleSource(Source):
    """A snapdelta source is a diffsync adapter: it declares its models, and `load()` fills them for one target."""

    # The name that users give with `--source`; it must equal the entry point name in pyproject.toml, because a
    # saved snapshot finds its models again by this name.
    source = "example"
    # The package of the plugin: snapdelta saves its version with each snapshot.
    distribution = "snapdelta-example"

    # The models, as in any diffsync adapter: one class attribute per model name.
    interface = Interface
    address = Address
    vlan = Vlan
    interface_state = InterfaceState
    # The models that a compare starts from; `address` is not here, because a compare reaches it from `interface`.
    top_level = ["interface", "vlan", "interface_state"]

    def load(self) -> None:
        """Called once per snapshot: get the data, fill the models, then call `finish_load()`."""
        target = None
        loaded: list[str] = []
        # read_inputs() reads the files given on the command line, and records their names and hashes in the
        # snapshot, so that a snapshot says what it was made from.
        for file in self.read_inputs():
            data = parse_json(file)
            target = target or data.get("device")
            loaded += self.fill(data)
        # The target name, and the models that this load filled: a compare uses only the models that both sides
        # filled, so a file with the state only still compares with a full snapshot, on the state.
        self.finish_load(target, loaded)

    def fill(self, data: dict[str, Any]) -> list[str]:
        """Add the objects of one device to the models; return the names of the models that the data holds."""
        loaded: list[str] = []
        if "interfaces" in data or "vlans" in data:
            for item in data.get("interfaces", []):
                interface = Interface(
                    name=item["name"],
                    description=item.get("description"),
                    enabled=item.get("enabled", True),
                    mtu=item.get("mtu"),
                    ifindex=item.get("ifindex"),
                )
                # add_or_warn() adds the object, or records a warning when the data holds it twice.
                self.add_or_warn(interface)
                for address in item.get("addresses", []):
                    child = Address(
                        interface=interface.name, prefix=address["prefix"], primary=address.get("primary", False)
                    )
                    # A child goes under its parent, so that the diff shows the address inside its interface.
                    self.add_child_or_warn("interface", interface.get_unique_id(), child)
            for item in data.get("vlans", []):
                self.add_or_warn(Vlan(vlan_id=item["id"], name=item.get("name")))
            loaded += ["interface", "address", "vlan"]
        if "state" in data:
            for name, status in data["state"].items():
                self.add_or_warn(InterfaceState(name=name, oper_status=status))
            loaded += ["interface_state"]
        return loaded


def parse_json(file: InputFile) -> dict[str, Any]:
    """The JSON object of a file; a LoadError when it is not one.

    A LoadError reaches the user as a short message with the name of the file, in the CLI and in the web UI.
    """
    try:
        data = json.loads(file.content)
    except json.JSONDecodeError as error:
        raise LoadError(file.name, f"not a valid JSON file ({error})") from error
    if not isinstance(data, dict):
        raise LoadError(file.name, "the file must hold a JSON object")
    return data
