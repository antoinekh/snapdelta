"""The `frr` source: FRR routers, read with `vtysh -c "show ... json"`."""

from datetime import UTC, datetime
from typing import Any

from snapdelta.inputs import InputFile

from .base import LabSource
from .commands import Runner, json_of, run_all
from .models import BgpPeer, BgpRoute, Interface

COMMANDS = {
    "interfaces": "show interface json",
    "bgp": "show bgp neighbors json",
}
# containerlab gives eth0 its management address, which changes with each deployment.
MANAGEMENT = "eth0"


class FrrSource(LabSource):
    """An FRR router of the lab."""

    source = "frr"
    kind = "linux"
    shell = ("vtysh", "-c")
    # The DNS domain of the machine that runs the lab.
    noise = frozenset({"domainname", "advDomainName", "rcvDomainName"})

    @classmethod
    def fetch_outputs(cls, runner: Runner) -> dict[str, InputFile]:
        """The commands at the same time, then the routes of each established peer at the same time."""
        outputs = run_all(runner, COMMANDS)
        peers = established(bgp_peers(json_of(outputs["bgp"])))
        return outputs | run_all(runner, {routes_key(peer): routes_command(peer) for peer in peers})

    def fill(self, outputs: dict[str, InputFile]) -> None:
        for interface in interfaces(json_of(outputs["interfaces"])):
            self.add_or_warn(interface)
        for peer in bgp_peers(json_of(outputs["bgp"])):
            self.add_or_warn(peer)
            if routes_key(peer.peer) in outputs:
                for route in bgp_routes(peer.peer, json_of(outputs[routes_key(peer.peer)])):
                    self.add_child_or_warn("bgp_peer", peer.get_unique_id(), route)


def routes_key(peer: str) -> str:
    """The output name of the routes of one peer, also the file name of a capture."""
    return f"routes-{peer}"


def routes_command(peer: str) -> str:
    """The routes that one peer sent, after the import policy."""
    return f"show bgp ipv4 unicast neighbors {peer} routes json"


def interfaces(data: dict[str, Any]) -> list[Interface]:
    """`show interface json`: one object per interface name."""
    found = []
    for name, item in sorted(data.items()):
        if name == MANAGEMENT:
            continue
        addresses = [address["address"] for address in item.get("ipAddresses", [])]
        found.append(
            Interface(
                name=name,
                description=item.get("description"),
                admin_state=item.get("administrativeStatus"),
                oper_state=item.get("operationalStatus"),
                mtu=item.get("mtu"),
                kind=item.get("interfaceType"),
                parent=item.get("parentInterface"),
                vlan_id=item.get("vlanId"),
                ipv4=sorted(address for address in addresses if ":" not in address),
                # Link-local addresses come from random MAC addresses, so two deployments never match.
                ipv6=sorted(address for address in addresses if ":" in address and not address.startswith("fe80:")),
            )
        )
    return found


def bgp_peers(data: dict[str, Any]) -> list[BgpPeer]:
    """`show bgp neighbors json`: one object per peer address, next to a few global keys."""
    found = []
    for peer, item in sorted(data.items()):
        if not isinstance(item, dict) or "remoteAs" not in item:
            continue
        unicast = item.get("addressFamilyInfo", {}).get("ipv4Unicast", {})
        # FRR names a peer that sends no host name "Unknown".
        hostname = item.get("hostname")
        found.append(
            BgpPeer(
                peer=peer,
                remote_as=item.get("remoteAs"),
                state=item.get("bgpState"),
                update_source=item.get("updateSource"),
                prefixes_received=unicast.get("acceptedPrefixCounter"),
                prefixes_sent=unicast.get("sentPrefixCounter"),
                hostname=None if hostname == "Unknown" else hostname,
                up_since=_epoch(item.get("bgpTimerUpEstablishedEpoch")),
            )
        )
    return found


def _epoch(seconds: int | None) -> datetime | None:
    return datetime.fromtimestamp(seconds, UTC) if seconds else None


def established(peers: list[BgpPeer]) -> list[str]:
    """The peers that have routes to read."""
    return [peer.peer for peer in peers if peer.state == "Established"]


def bgp_routes(peer: str, data: dict[str, Any]) -> list[BgpRoute]:
    """`show bgp ipv4 unicast neighbors PEER routes json`: the paths of each prefix that PEER sent."""
    found = []
    for prefix, paths in sorted(data.get("routes", {}).items()):
        # Without add-path, a peer sends one path per prefix.
        path = paths[0]
        next_hops = sorted(hop["ip"] for hop in path.get("nexthops", []) if "ip" in hop)
        found.append(
            BgpRoute(
                peer=peer,
                prefix=prefix,
                next_hop=", ".join(next_hops) or None,
                as_path=path.get("path"),
                local_pref=path.get("locPrf"),
                med=path.get("metric"),
                origin=path.get("origin"),
                best=bool(path.get("bestpath")),
            )
        )
    return found
