"""The models of the lab, the same for every vendor: interfaces, BGP peers and the routes that each peer sends."""

from datetime import datetime

from diffsync import DiffSyncModel


class Interface(DiffSyncModel):
    """An interface or a subinterface; `admin_state` and `oper_state` are `up` or `down` on every vendor."""

    _modelname = "interface"
    _identifiers = ("name",)
    _attributes = ("description", "admin_state", "oper_state", "mtu", "kind", "parent", "vlan_id", "ipv4", "ipv6")

    name: str
    description: str | None = None
    admin_state: str | None = None
    oper_state: str | None = None
    mtu: int | None = None
    kind: str | None = None
    parent: str | None = None
    vlan_id: int | None = None
    ipv4: list[str] = []
    ipv6: list[str] = []


class BgpPeer(DiffSyncModel):
    """A BGP neighbor; its `routes` are the `BgpRoute` children, so a diff shows them under their peer."""

    _modelname = "bgp_peer"
    _identifiers = ("peer",)
    _attributes = ("remote_as", "state", "update_source", "prefixes_received", "prefixes_sent")
    _children = {"bgp_route": "routes"}

    peer: str
    remote_as: int | None = None
    state: str | None = None
    update_source: str | None = None
    # The routes that the import accepted: a route with the own AS in its path does not count.
    prefixes_received: int | None = None
    prefixes_sent: int | None = None
    routes: list[str] = []
    # Kept in the snapshot to read it, never compared: a session that restarts changes up_since.
    hostname: str | None = None
    up_since: datetime | None = None


class BgpRoute(DiffSyncModel):
    """A route that one peer sends, with `best` when the router uses it."""

    _modelname = "bgp_route"
    _identifiers = ("peer", "prefix")
    _attributes = ("next_hop", "as_path", "local_pref", "med", "origin", "best")

    peer: str
    prefix: str
    next_hop: str | None = None
    as_path: str | None = None
    local_pref: int | None = None
    med: int | None = None
    origin: str | None = None
    best: bool = False
