"""The `srlinux` source: Nokia SR Linux routers, read with `sr_cli "info from state ... | as json"`."""

import re
from datetime import datetime
from typing import Any

from snapdelta.inputs import InputFile

from .base import LabSource
from .commands import Runner, json_of, run_all
from .models import BgpPeer, BgpRoute, Interface

COMMANDS = {
    "interfaces": "info from state interface * | as json",
    "bgp": "info from state network-instance default protocols bgp neighbor * | as json",
    "rib": "info from state network-instance default bgp-rib | as json",
}
MANAGEMENT = "mgmt0"
# SR Linux says enable and disable; FRR, and so the models, say up and down.
ADMIN_STATES = {"enable": "up", "disable": "down"}
KIND_RE = re.compile(r"^[a-z]+")


class SrlinuxSource(LabSource):
    """A Nokia SR Linux router of the lab."""

    source = "srlinux"
    kind = "nokia_srlinux"
    shell = ("sr_cli",)
    noise = frozenset({"statistics", "traffic-rate", "transceiver", "ethernet", "sent-messages", "received-messages"})

    @classmethod
    def fetch_outputs(cls, runner: Runner) -> dict[str, InputFile]:
        """The three commands at the same time; the RIB holds the routes of every peer."""
        return run_all(runner, COMMANDS)

    def fill(self, outputs: dict[str, InputFile]) -> None:
        for interface in interfaces(json_of(outputs["interfaces"])):
            self.add_or_warn(interface)
        rib = json_of(outputs["rib"])
        for peer in bgp_peers(json_of(outputs["bgp"]), rib):
            self.add_or_warn(peer)
            for route in bgp_routes(peer.peer, rib):
                self.add_child_or_warn("bgp_peer", peer.get_unique_id(), route)


def interfaces(data: dict[str, Any]) -> list[Interface]:
    """`info from state interface *`: each interface, then each of its subinterfaces as `NAME.INDEX`."""
    found = []
    for item in data.get("interface", []):
        name = item["name"]
        subinterfaces = item.get("subinterface", [])
        # A port that nobody configured: disabled, with no subinterface.
        if name == MANAGEMENT or (item.get("admin-state") == "disable" and not subinterfaces):
            continue
        kind = KIND_RE.match(name)
        found.append(
            Interface(
                name=name,
                description=item.get("description"),
                admin_state=ADMIN_STATES.get(item.get("admin-state", "")),
                oper_state=item.get("oper-state"),
                mtu=item.get("mtu"),
                kind=kind.group() if kind else None,
            )
        )
        found += [_subinterface(name, sub) for sub in subinterfaces]
    return sorted(found, key=lambda interface: interface.name)


def _subinterface(parent: str, item: dict[str, Any]) -> Interface:
    ipv4 = [address["ip-prefix"] for address in item.get("ipv4", {}).get("address", [])]
    ipv6 = [address["ip-prefix"] for address in item.get("ipv6", {}).get("address", [])]
    vlan = item.get("vlan", {}).get("encap", {}).get("single-tagged", {}).get("vlan-id")
    return Interface(
        name=f"{parent}.{item['index']}",
        description=item.get("description"),
        admin_state=ADMIN_STATES.get(item.get("admin-state", "")),
        oper_state=item.get("oper-state"),
        mtu=item.get("ip-mtu"),
        kind="subinterface",
        parent=parent,
        vlan_id=vlan if isinstance(vlan, int) else None,
        ipv4=sorted(ipv4),
        ipv6=sorted(address for address in ipv6 if not address.startswith("fe80:")),
    )


def bgp_peers(data: dict[str, Any], rib: dict[str, Any]) -> list[BgpPeer]:
    """`info from state ... bgp neighbor *`, with the routes of the RIB that each peer sent and the import kept."""
    accepted: dict[str, int] = {}
    for route in _received(rib):
        accepted[route["neighbor"]] = accepted.get(route["neighbor"], 0) + 1
    found = []
    for item in sorted(data.get("neighbor", []), key=lambda item: item["peer-address"]):
        unicast = next((afi for afi in item.get("afi-safi", []) if afi.get("afi-safi-name") == "ipv4-unicast"), {})
        state = item.get("session-state", "")
        found.append(
            BgpPeer(
                peer=item["peer-address"],
                remote_as=item.get("peer-as"),
                # established becomes Established, as FRR writes it.
                state=state.capitalize() or None,
                prefixes_received=accepted.get(item["peer-address"], 0) if state == "established" else None,
                prefixes_sent=unicast.get("sent-routes"),
                up_since=_timestamp(item.get("last-established")) if state == "established" else None,
            )
        )
    return found


def bgp_routes(peer: str, rib: dict[str, Any]) -> list[BgpRoute]:
    """The routes of one peer in the RIB, with the attributes of their attribute set."""
    attributes = {item["index"]: item for item in rib.get("attr-sets", {}).get("attr-set", [])}
    found = []
    for route in _received(rib):
        if route["neighbor"] != peer:
            continue
        attribute = attributes.get(route.get("attr-id"), {})
        segments = attribute.get("as-path", {}).get("segment", [])
        as_path = " ".join(str(member) for segment in segments for member in segment.get("member", []))
        found.append(
            BgpRoute(
                peer=peer,
                prefix=route["prefix"],
                next_hop=attribute.get("next-hop"),
                as_path=as_path or None,
                local_pref=attribute.get("local-pref"),
                med=attribute.get("med"),
                # igp becomes IGP, as FRR writes it.
                origin=attribute["origin"].upper() if "origin" in attribute else None,
                best=bool(route.get("best-route")),
            )
        )
    return sorted(found, key=lambda route: route.prefix)


def _received(rib: dict[str, Any]) -> list[dict[str, Any]]:
    """The routes that the peers sent and the import kept: a route with the own AS in its path is not valid."""
    unicast = next((item for item in rib.get("afi-safi", []) if item.get("afi-safi-name") == "ipv4-unicast"), {})
    routes = unicast.get("ipv4-unicast", {}).get("rib-in-out", {}).get("rib-in-post", {}).get("route", [])
    return [route for route in routes if route.get("valid-route")]


def _timestamp(text: str | None) -> datetime | None:
    """`2026-10-13T18:21:59.700Z (3 minutes ago)` as a date."""
    return datetime.fromisoformat(text.split()[0].replace("Z", "+00:00")) if text else None
