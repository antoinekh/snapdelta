from datetime import UTC, datetime

from snapdelta_lab import srlinux

from .test_frr import output


def test_interfaces_and_subinterfaces_without_the_unused_ports() -> None:
    found = {interface.name: interface for interface in srlinux.interfaces(output("srlinux", "r3", "interfaces"))}
    assert sorted(found) == [
        "ethernet-1/1",
        "ethernet-1/1.0",
        "ethernet-1/2",
        "ethernet-1/2.0",
        "lo0",
        "lo0.0",
        "lo1",
        "lo1.0",
    ]
    port, sub = found["ethernet-1/1"], found["ethernet-1/1.0"]
    assert (port.description, port.admin_state, port.oper_state, port.kind) == ("to r1", "up", "up", "ethernet")
    assert (sub.kind, sub.parent, sub.ipv4, sub.mtu) == ("subinterface", "ethernet-1/1", ["10.13.0.3/24"], 1500)
    assert found["lo1"].description == "customer lan" and found["lo0.0"].ipv4 == ["10.0.0.3/32"]


def test_bgp_peers_count_the_routes_that_the_import_kept() -> None:
    rib = output("srlinux", "r3", "rib")
    peers = {peer.peer: peer for peer in srlinux.bgp_peers(output("srlinux", "r3", "bgp"), rib)}
    assert sorted(peers) == ["10.13.0.1", "10.23.0.2"]
    peer = peers["10.13.0.1"]
    assert (peer.remote_as, peer.state, peer.prefixes_received, peer.prefixes_sent) == (65001, "Established", 4, 4)
    assert peer.up_since is not None and peer.up_since.tzinfo is not None


def test_bgp_routes_take_their_attributes_from_the_attribute_sets() -> None:
    routes = {route.prefix: route for route in srlinux.bgp_routes("10.13.0.1", output("srlinux", "r3", "rib"))}
    # r3 drops the routes of its own AS that r1 sends back.
    assert sorted(routes) == ["10.0.0.1/32", "10.0.0.2/32", "192.168.1.0/24", "192.168.2.0/24"]
    lan = routes["192.168.1.0/24"]
    assert (lan.next_hop, lan.as_path, lan.origin, lan.best) == ("10.13.0.1", "65001", "IGP", True)
    assert (routes["10.0.0.2/32"].as_path, routes["10.0.0.2/32"].best) == ("65001 65002", False)


def test_a_timestamp_with_its_age_in_words() -> None:
    assert srlinux._timestamp("2026-10-13T18:21:59.700Z (3 minutes ago)") == datetime(
        2026, 10, 13, 18, 21, 59, 700000, tzinfo=UTC
    )
