"""The models: what snapdelta compares, written as diffsync models.

Both sides of a compare hold the same models, so a model is the meeting point of every source: two sources that
read different formats (two vendors, a file and an API) compare as long as they fill these classes.
"""

from diffsync import DiffSyncModel


class Interface(DiffSyncModel):
    # The model name: the key of the model in a snapshot, in a diff and in `--model interface`.
    _modelname = "interface"
    # The fields that name one object: two snapshots hold "the same" interface when its name is the same.
    _identifiers = ("name",)
    # The fields that a compare looks at: a different value is a change of the object.
    _attributes = ("description", "enabled", "mtu")
    # The child models, by model name and the field that lists them: an address belongs to its interface.
    _children = {"address": "addresses"}

    name: str
    description: str | None = None
    enabled: bool = True
    mtu: int | None = None
    # The unique ids of the child addresses; diffsync fills it when the source adds a child.
    addresses: list[str] = []
    # Neither an identifier nor an attribute: the snapshot keeps it for reading, and a compare never looks at it,
    # because a device can renumber its interfaces at each reboot.
    ifindex: int | None = None


class Address(DiffSyncModel):
    """A child of `Interface`: a compare reaches it through its interface."""

    _modelname = "address"
    # The interface is part of the name, so the same prefix on two interfaces gives two objects.
    _identifiers = ("interface", "prefix")
    _attributes = ("primary",)

    interface: str
    prefix: str
    primary: bool = False


class Vlan(DiffSyncModel):
    _modelname = "vlan"
    _identifiers = ("vlan_id",)
    _attributes = ("name",)

    vlan_id: int
    name: str | None = None


class InterfaceState(DiffSyncModel):
    """The operational state, apart from the configuration, so that a source can load one without the other."""

    _modelname = "interface_state"
    _identifiers = ("name",)
    _attributes = ("oper_status",)

    name: str
    oper_status: str | None = None
