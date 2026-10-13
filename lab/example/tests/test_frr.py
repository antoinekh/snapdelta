import json
from pathlib import Path

from snapdelta_lab import frr

OUTPUTS = Path(__file__).parent / "outputs"


def output(source: str, target: str, key: str) -> dict:
    return json.loads((OUTPUTS / source / target / f"{key}.json").read_text())


def test_interfaces_leave_out_the_management_and_link_local_addresses() -> None:
    found = {interface.name: interface for interface in frr.interfaces(output("frr", "r1", "interfaces"))}
    assert sorted(found) == ["eth1", "eth1.10", "eth2", "lan", "lo"]
    trunk = found["eth1.10"]
    assert (trunk.kind, trunk.parent, trunk.vlan_id) == ("Vlan", "eth1", 10)
    assert trunk.description == "trunk to r2, vlan 10"
    assert trunk.ipv4 == ["10.12.10.1/24"] and trunk.ipv6 == []
    assert (trunk.admin_state, trunk.oper_state) == ("up", "up")
    assert "10.0.0.1/32" in found["lo"].ipv4


def test_bgp_peers_and_their_routes() -> None:
    peers = {peer.peer: peer for peer in frr.bgp_peers(output("frr", "r1", "bgp"))}
    assert sorted(peers) == ["10.12.10.2", "10.13.0.3"]
    peer = peers["10.12.10.2"]
    assert (peer.remote_as, peer.state, peer.update_source) == (65002, "Established", None)
    assert (peer.prefixes_received, peer.prefixes_sent, peer.hostname) == (4, 6, "r2")
    # The SR Linux peer sends no host name.
    assert peers["10.13.0.3"].hostname is None
    assert frr.established(list(peers.values())) == ["10.12.10.2", "10.13.0.3"]
    routes = {route.prefix: route for route in frr.bgp_routes("10.12.10.2", output("frr", "r1", "routes-10.12.10.2"))}
    assert sorted(routes) == ["10.0.0.2/32", "10.0.0.3/32", "192.168.2.0/24", "192.168.3.0/24"]
    lan = routes["192.168.2.0/24"]
    assert (lan.next_hop, lan.as_path, lan.origin, lan.best) == ("10.12.10.2", "65002", "IGP", True)
    assert routes["10.0.0.3/32"].as_path == "65002 65003"
